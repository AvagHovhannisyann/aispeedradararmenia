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
from roadeye.reporting.export import summarize
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


def create_app(db_path: str | Path, evidence_dir: str | Path | None = None) -> FastAPI:
    """Build the review app against a specific database."""
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

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        page = STATIC_DIR / "review.html"
        if not page.exists():  # pragma: no cover - packaging error
            raise HTTPException(status_code=500, detail="review.html is missing")
        return page.read_text(encoding="utf-8")

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

    @app.get("/api/stats")
    def stats() -> dict[str, Any]:
        with _db() as db:
            return {
                "defects": summarize(db.list_defects()),
                "reviews": db.count("reviews"),
                "surveys": db.count("surveys"),
            }

    return app
