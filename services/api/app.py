"""Local review API.

Serves the human-in-the-loop review workflow (M4), which is the mechanism that turns
drives into training data:

    AI detects -> human corrects -> correction stored -> dataset grows
        -> model retrained -> AI improves -> human work decreases

That loop is the product's compounding asset, so review speed is a first-class
concern. The API is shaped around deciding fast: one queue endpoint that returns
everything the UI needs in a single round trip, and one write endpoint per decision.

**Localhost only.** There is no authentication. Binding this beyond 127.0.0.1 would
expose survey imagery — which may contain identifiable people — to the network. See
``docs/SECURITY.md``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from roadeye.domain.enums import (
    DamageClass,
    DefectStatus,
    LocationMethod,
    ReviewAction,
    Severity,
    SeveritySource,
)
from roadeye.domain.models import Defect, GeoPoint, Review
from roadeye.reporting.export import summarize, to_geojson
from roadeye.storage.db import Database

STATIC_DIR = Path(__file__).parent / "static"


class ReviewRequest(BaseModel):
    """One human decision about one defect."""

    model_config = ConfigDict(extra="forbid")

    action: ReviewAction
    reviewer: str = Field(default="local", max_length=120)
    damage_class: DamageClass | None = None
    severity: Severity | None = None
    lat: float | None = Field(default=None, ge=-90.0, le=90.0)
    lon: float | None = Field(default=None, ge=-180.0, le=180.0)
    note: str | None = Field(default=None, max_length=2000)


def _apply(defect: Defect, updates: dict[str, Any]) -> Defect:
    """Return the defect with ``updates`` applied, re-validated as a whole.

    Rebuilt rather than mutated field by field. The domain model enforces cross-field
    invariants (an assessed severity must declare its source), and with
    validate_assignment enabled a per-field assignment is checked *between* the two
    writes — so a legitimate two-field change would be rejected halfway through.
    Constructing the full object applies every change atomically and still runs every
    validator, which is the point: the invariant is enforced, not sidestepped.
    """
    data = defect.model_dump()
    data.update(updates)
    data["updated_at"] = datetime.now(UTC)
    return Defect.model_validate(data)


#: Model-id prefixes that mean "this output describes no real road". The CLI already
#: prints a warning when the fake detector runs; a dashboard is where that warning
#: matters most, because synthetic markers on a real map of Yerevan look exactly like a
#: working product.
SYNTHETIC_MODEL_PREFIXES = ("fake-", "scripted-", "null-")


def _provenance(defects: list[Defect]) -> dict[str, Any]:
    """What produced this data, and whether it can be believed.

    Returned to the dashboard so the page can refuse to look authoritative about
    synthetic output. Reported from the defects themselves rather than from a flag
    somebody has to remember to set.

    Warnings are **codes with parameters, not prose**. The dashboard's first language is
    Armenian, and an English sentence built here would arrive untranslatable — the one
    place on the page where the honesty warnings live is the last place that should fall
    back to a language the reader may not have.
    """
    models = sorted({d.model_id for d in defects if d.model_id})
    synthetic = [m for m in models if m.startswith(SYNTHETIC_MODEL_PREFIXES)]
    warnings: list[dict[str, Any]] = []
    if synthetic:
        warnings.append({"code": "synthetic_detector", "models": ", ".join(synthetic)})
    if defects and not any(d.status is DefectStatus.VERIFIED for d in defects):
        warnings.append({"code": "none_verified"})
    return {"models": models, "synthetic": bool(synthetic), "warnings": warnings}


def create_app(
    db_path: str | Path,
    evidence_dir: str | Path | None = None,
    roads_path: str | Path | None = None,
) -> FastAPI:
    """Build the review app against a specific database.

    ``roads_path`` is an optional road network from ``roadeye roads``. When present the
    dashboard draws streets from it rather than depending on a tile server — which is
    the offline-first answer, and the only one compatible with
    ``tile.openstreetmap.org``'s usage policy excluding production use.
    """
    db_path = Path(db_path)
    if evidence_dir is None:
        from roadeye.reporting.evidence import evidence_dir_for

        evidence_dir = evidence_dir_for(db_path)
    evidence_dir = Path(evidence_dir).resolve()

    app = FastAPI(
        title="RoadEye review",
        description="Human-in-the-loop defect review. Localhost only; no auth.",
        version="0.1.0",
    )

    def _db() -> Database:
        # A connection per request. SQLite is single-writer and these are short
        # operations; a shared connection across threads is the classic way to get
        # "SQLite objects created in a thread can only be used in that same thread".
        return Database(db_path)

    def _static(relative: str, media_type: str) -> FileResponse:
        path = STATIC_DIR / relative
        if not path.is_file():  # pragma: no cover - packaging error
            raise HTTPException(status_code=500, detail=f"{relative} is missing")
        return FileResponse(path, media_type=media_type)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        page = STATIC_DIR / "review.html"
        if not page.exists():  # pragma: no cover - packaging error
            raise HTTPException(status_code=500, detail="review.html is missing")
        return page.read_text(encoding="utf-8")

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard() -> FileResponse:
        return _static("dashboard.html", "text/html")

    @app.get("/static/{name}")
    def static_asset(name: str) -> FileResponse:
        """Serve the dashboard's CSS and JS, and the vendored map library.

        Whitelisted by name rather than mounted as a directory: this app also serves an
        evidence directory of survey imagery, and a static mount is one misconfiguration
        away from serving the wrong tree. The paths below are constants in this file, so
        the request never supplies part of a path — only a key that must match exactly.
        """
        allowed = {
            "dashboard.css": ("dashboard.css", "text/css"),
            "dashboard.js": ("dashboard.js", "application/javascript"),
            # Third-party, kept under vendor/ so what is ours and what is not stays
            # obvious at a glance. MapLibre is BSD-3-Clause; the licence text ships
            # beside it, as that licence requires of a redistribution.
            "maplibre-gl.css": ("vendor/maplibre-gl.css", "text/css"),
            "maplibre-gl.js": ("vendor/maplibre-gl.js", "application/javascript"),
        }
        entry = allowed.get(name)
        if entry is None:
            raise HTTPException(status_code=404, detail="no such asset")
        return _static(*entry)

    @app.get("/api/queue")
    def queue(
        status: DefectStatus = DefectStatus.PROBABLE,
        limit: int = Query(default=200, ge=1, le=2000),
    ) -> dict[str, Any]:
        """Everything the review UI needs, in one request.

        Returned as a whole queue rather than one-at-a-time so the UI can preload the
        next image while the reviewer looks at the current one. Waiting on a network
        round trip between every decision is what makes review feel slow.
        """
        with _db() as db:
            defects = db.list_defects(status=status, limit=limit)
            all_defects = db.list_defects()
            items = []
            for defect in defects:
                observations = db.observations_for(defect.defect_id)
                items.append(
                    {
                        "defect_id": defect.defect_id,
                        "damage_class": defect.damage_class.value,
                        "confidence": round(defect.confidence, 4),
                        "severity": defect.severity.value,
                        "severity_source": defect.severity_source.value,
                        "status": defect.status.value,
                        "lat": defect.location.lat,
                        "lon": defect.location.lon,
                        "location_method": defect.location.method.value,
                        "uncertainty_m": round(defect.location.uncertainty_m, 2),
                        "observation_count": defect.observation_count,
                        "first_seen": defect.first_seen.isoformat(),
                        "survey_ids": defect.survey_ids,
                        "model_id": defect.model_id,
                        "processing_run_id": defect.processing_run_id,
                        "representative_frame_id": defect.representative_frame_id,
                        "context_image": f"{defect.defect_id}_context.jpg",
                        "crop_image": f"{defect.defect_id}_crop.jpg",
                        "detection_ids": [i for o in observations for i in o.detection_ids][:20],
                    }
                )
            return {
                "items": items,
                "pending": len(items),
                "totals": summarize(all_defects),
            }

    @app.get("/api/defects/{defect_id}")
    def defect_detail(defect_id: str) -> dict[str, Any]:
        with _db() as db:
            defect = db.get_defect(defect_id)
            if defect is None:
                raise HTTPException(status_code=404, detail="no such defect")
            return {
                "defect": defect.model_dump(mode="json"),
                "observations": [o.model_dump(mode="json") for o in db.observations_for(defect_id)],
                "reviews": [dict(r) for r in db.reviews_for(defect_id)],
            }

    @app.get("/api/evidence/{filename}")
    def evidence(filename: str) -> FileResponse:
        """Serve one evidence image.

        The filename is resolved and checked to be inside the evidence directory. It
        arrives from a URL, so treating it as a path without that check is a directory
        traversal — and these files sit next to a database of survey data.
        """
        candidate = (evidence_dir / filename).resolve()
        if not candidate.is_relative_to(evidence_dir) or not candidate.is_file():
            raise HTTPException(status_code=404, detail="no such evidence image")
        if candidate.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
            raise HTTPException(status_code=404, detail="not an image")
        return FileResponse(candidate, media_type="image/jpeg")

    @app.post("/api/defects/{defect_id}/review")
    def submit_review(defect_id: str, request: ReviewRequest) -> dict[str, Any]:
        """Record a human decision and apply it to the defect.

        The review row is append-only; the defect is updated to reflect the latest
        decision. Both happen, always — the defect shows current state, the review log
        shows how it got there. Overwriting without the log would make a municipal
        record unauditable.
        """
        with _db() as db:
            defect = db.get_defect(defect_id)
            if defect is None:
                raise HTTPException(status_code=404, detail="no such defect")

            previous: dict[str, Any] = {}
            updates: dict[str, Any] = {}

            if request.action is ReviewAction.APPROVE:
                previous["status"] = defect.status.value
                updates["status"] = DefectStatus.VERIFIED

            elif request.action is ReviewAction.REJECT:
                previous["status"] = defect.status.value
                updates["status"] = DefectStatus.REJECTED

            elif request.action is ReviewAction.CHANGE_CLASS:
                if request.damage_class is None:
                    raise HTTPException(status_code=422, detail="damage_class is required")
                previous["damage_class"] = defect.damage_class.value
                previous["status"] = defect.status.value
                updates["damage_class"] = request.damage_class
                # Correcting the class is itself an endorsement that something is
                # there, so the defect becomes verified rather than staying probable.
                updates["status"] = DefectStatus.VERIFIED

            elif request.action is ReviewAction.CHANGE_SEVERITY:
                if request.severity is None:
                    raise HTTPException(status_code=422, detail="severity is required")
                previous["severity"] = defect.severity.value
                previous["severity_source"] = defect.severity_source.value
                updates["severity"] = request.severity
                # Severity and its source must move together. The domain model rejects
                # an assessed severity with no stated source, so assigning them one at
                # a time would briefly construct an invalid defect — and with
                # validate_assignment on, that raises. Applying both at once is not a
                # workaround for the rule; it is the rule being enforced.
                updates["severity_source"] = SeveritySource.HUMAN

            elif request.action is ReviewAction.ADJUST_LOCATION:
                if request.lat is None or request.lon is None:
                    raise HTTPException(status_code=422, detail="lat and lon are required")
                previous["location"] = defect.location.model_dump(mode="json")
                updates["location"] = GeoPoint(
                    lat=request.lat,
                    lon=request.lon,
                    # A human placing the marker is the most trustworthy method we
                    # have; the uncertainty is theirs, not the GPS's.
                    method=LocationMethod.MANUAL_CORRECTION,
                    uncertainty_m=2.0,
                )
                previous["status"] = defect.status.value
                updates["status"] = DefectStatus.VERIFIED

            defect = _apply(defect, updates)
            new = {
                k: (v.value if hasattr(v, "value") else v)
                if not isinstance(v, GeoPoint)
                else v.model_dump(mode="json")
                for k, v in updates.items()
            }
            db.upsert_defects([defect])
            db.append_review(
                Review(
                    review_id=f"rev_{uuid4().hex[:12]}",
                    defect_id=defect_id,
                    action=request.action,
                    reviewer=request.reviewer,
                    previous_value=previous or None,
                    new_value=new or None,
                    note=request.note,
                )
            )
            return {"defect_id": defect_id, "status": defect.status.value, "applied": new}

    @app.get("/api/map")
    def map_data(
        damage_class: DamageClass | None = None,
        status: DefectStatus | None = None,
        survey_id: str | None = None,
        min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
        severity: Severity | None = None,
        limit: int = Query(default=5000, ge=1, le=50000),
    ) -> dict[str, Any]:
        """Everything the dashboard map draws, in one request.

        Returns GeoJSON so the payload is directly consumable by MapLibre, QGIS and
        anything else a municipality already owns — the dashboard is not a privileged
        client with its own private format.

        ``totals`` is computed over the **whole database**, not the filtered set, and
        ``shown`` over the filtered one. Both are returned because a summary that
        silently reflected the active filter would let someone read "12 defects" off a
        screen that was hiding ninety.
        """
        with _db() as db:
            everything = db.list_defects()
            defects = db.list_defects(
                damage_class=damage_class,
                status=status,
                survey_id=survey_id,
                min_confidence=min_confidence or None,
                limit=limit,
            )
            if severity is not None:
                defects = [d for d in defects if d.severity is severity]

            surveys = sorted({s for d in everything for s in d.survey_ids})

        collection = to_geojson(defects)
        collection["roadeye"]["shown"] = summarize(defects)
        collection["roadeye"]["totals"] = summarize(everything)
        collection["roadeye"]["surveys"] = surveys
        collection["roadeye"]["truncated"] = len(defects) >= limit
        collection["roadeye"]["provenance"] = _provenance(everything)
        return collection

    @app.get("/api/roads")
    def roads() -> dict[str, Any]:
        """The local road network as GeoJSON lines, for street context without tiles.

        Loaded per request rather than cached at startup so replacing the file does not
        need a restart — a city extract is tens of thousands of short segments, which is
        milliseconds to read and a rounding error next to the request itself.

        Returns an empty collection rather than a 404 when no network is configured: the
        dashboard treats streets as optional decoration and must not have to distinguish
        "no roads file" from "the request failed".
        """
        empty: dict[str, Any] = {"type": "FeatureCollection", "features": [], "attribution": None}
        if roads_path is None or not Path(roads_path).is_file():
            return empty

        from roadeye.map_matching.network import RoadNetwork

        network = RoadNetwork.load(roads_path)

        # One feature per way, not per segment: a city is ~100k segments and ~10k ways,
        # and MapLibre draws a tenth as many lines an order of magnitude faster.
        by_way: dict[str, dict[str, Any]] = {}
        for segment in network.segments:
            way = by_way.setdefault(
                segment.way_id,
                {"name": segment.name, "highway": segment.highway, "points": []},
            )
            way["points"].append(segment)

        features = []
        for way_id, way in by_way.items():
            segments = way["points"]
            coordinates = [[segments[0].start.lon, segments[0].start.lat]]
            coordinates.extend([[s.end.lon, s.end.lat] for s in segments])
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": coordinates},
                    "properties": {
                        "way_id": way_id,
                        "name": way["name"],
                        "highway": way["highway"],
                    },
                }
            )
        return {
            "type": "FeatureCollection",
            "features": features,
            "attribution": network.attribution,
        }

    @app.get("/api/streets")
    def streets(limit: int = Query(default=200, ge=1, le=5000)) -> dict[str, Any]:
        """Defects rolled up per stretch of road, with how much of each was driven.

        Empty when no road network is configured — the rollup is a statement about
        streets, and without geometry there are no streets to make it about.
        """
        if roads_path is None or not Path(roads_path).is_file():
            return {"streets": [], "available": False}

        from roadeye.map_matching.network import RoadNetwork
        from roadeye.reporting.segments import build_street_report

        network = RoadNetwork.load(roads_path)
        with _db() as db:
            report = build_street_report(db.list_defects(), db.survey_frames(), network)

        payload = report.to_json()
        payload["available"] = True
        # Densest first: a work plan is read from the top.
        payload["streets"] = sorted(
            (s for s in payload["streets"] if s["state"] != "not_surveyed"),
            key=lambda s: (-(s["defects_per_100m"] or 0), -s["outstanding"]),
        )[:limit]
        return payload

    @app.get("/api/stats")
    def stats() -> dict[str, Any]:
        with _db() as db:
            return {
                "defects": summarize(db.list_defects()),
                "reviews": db.count("reviews"),
                "surveys": db.count("surveys"),
            }

    return app
