"""Export defects to formats a municipality can actually use.

CSV opens in Excel; GeoJSON opens in QGIS, ArcGIS, Leaflet, MapLibre and every GIS a
city already owns. Both carry enough provenance to answer, from the file alone:

* which survey and date produced this,
* which model version,
* whether a human verified it,
* how uncertain the position is.

An export that omits uncertainty and review state is worse than no export: it looks
authoritative and is not. Every row here states both.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from roadeye.domain.models import Defect
from roadeye.map_matching.osm import OSM_ATTRIBUTION

#: Attribution text keyed by the ``RoadSegmentRef.source`` that obliges it. Derived from
#: the data rather than passed in by the caller, because an attribution someone has to
#: remember to add is one that eventually gets forgotten — and one added unconditionally
#: is noise that teaches readers to skip the field.
ATTRIBUTION_BY_SOURCE = {
    "osm": f"Road network data {OSM_ATTRIBUTION}, ODbL.",
}

#: Column order for CSV export. Stable: municipal users build spreadsheets on it.
CSV_COLUMNS = [
    "defect_id",
    "damage_class",
    "latitude",
    "longitude",
    "location_method",
    "location_uncertainty_m",
    "road_name",
    "road_segment_id",
    "road_source",
    "confidence",
    "severity",
    "severity_source",
    "status",
    "trend",
    "observation_count",
    "survey_ids",
    "first_seen",
    "last_seen",
    "model_id",
    "processing_run_id",
    "representative_frame_id",
    "representative_image",
]


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _row(defect: Defect) -> dict[str, Any]:
    return {
        "defect_id": defect.defect_id,
        "damage_class": defect.damage_class.value,
        # Six decimal places is ~0.11 m at the equator. Our uncertainty is metres, so
        # printing more would imply precision the sensor does not have.
        "latitude": round(defect.location.lat, 6),
        "longitude": round(defect.location.lon, 6),
        "location_method": defect.location.method.value,
        "location_uncertainty_m": round(defect.location.uncertainty_m, 2),
        "road_name": defect.road.name if defect.road else "",
        "road_segment_id": defect.road.segment_id if defect.road else "",
        "road_source": defect.road.source if defect.road else "",
        "confidence": round(defect.confidence, 4),
        "severity": defect.severity.value,
        "severity_source": defect.severity_source.value,
        "status": defect.status.value,
        "trend": defect.trend.value,
        "observation_count": defect.observation_count,
        "survey_ids": ";".join(defect.survey_ids),
        "first_seen": _iso(defect.first_seen),
        "last_seen": _iso(defect.last_seen),
        "model_id": defect.model_id or "",
        "processing_run_id": defect.processing_run_id or "",
        "representative_frame_id": defect.representative_frame_id or "",
        "representative_image": defect.representative_image_path or "",
    }


def required_attribution(defects: Iterable[Defect]) -> str | None:
    """Attribution the defects themselves oblige, or ``None`` if they oblige nothing.

    A defect that was never map-matched leans on no external database, so the export
    carries no attribution. One matched against OpenStreetMap does, and ODbL says so.
    """
    sources = {d.road.source for d in defects if d.road is not None}
    notices = [ATTRIBUTION_BY_SOURCE[s] for s in sorted(sources) if s in ATTRIBUTION_BY_SOURCE]
    return " ".join(notices) or None


def to_csv(defects: Sequence[Defect], path: str | Path) -> Path:
    """Write defects to CSV, plus an attribution sidecar when one is owed.

    CSV has nowhere to put a licence notice — no header, no metadata block — so when the
    rows carry OSM-derived road references the obligation is written to
    ``<name>.ATTRIBUTION.txt`` next to the file. A spreadsheet emailed to a municipality
    travels alone otherwise, and the obligation travels with the data, not with us.
    """
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for defect in defects:
            writer.writerow(_row(defect))

    notice = required_attribution(defects)
    sidecar = out.with_suffix(out.suffix + ".ATTRIBUTION.txt")
    if notice:
        sidecar.write_text(notice + "\n", encoding="utf-8")
    elif sidecar.exists():
        # A re-export with no matched defects must not leave the previous run's notice
        # sitting next to data it no longer describes.
        sidecar.unlink()
    return out


def to_geojson(
    defects: Sequence[Defect],
    path: str | Path | None = None,
    *,
    attribution: str | None = None,
) -> dict[str, Any]:
    """Build a GeoJSON FeatureCollection, optionally writing it to ``path``.

    ``attribution`` names any external data the export leans on. Left as ``None`` it is
    derived from the defects via :func:`required_attribution`, which is the right default
    in both directions: an export of map-matched defects carries the ODbL notice into the
    file, where it outlives the session that produced it, and an export that touched no
    external database does not claim to have used one.
    """
    if attribution is None:
        attribution = required_attribution(defects)
    features = []
    for defect in defects:
        props = _row(defect)
        # Coordinates live in geometry; repeating them as properties invites the two
        # copies to drift apart in downstream tooling.
        props.pop("latitude")
        props.pop("longitude")
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    # GeoJSON is [longitude, latitude] — the reverse of how humans say
                    # it, and a perennial source of maps that render in the ocean.
                    "coordinates": [
                        round(defect.location.lon, 6),
                        round(defect.location.lat, 6),
                    ],
                },
                "properties": props,
            }
        )

    collection: dict[str, Any] = {
        "type": "FeatureCollection",
        "features": features,
        "roadeye": {
            "generated_at": _iso(datetime.now(UTC)),
            "defect_count": len(features),
            "notice": (
                "Positions are estimates with stated uncertainty. Defects with "
                "status='probable' have NOT been verified by a human reviewer."
            ),
        },
    }
    if attribution:
        collection["attribution"] = attribution

    if path is not None:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(collection, indent=2), encoding="utf-8")
    return collection


def summarize(defects: Iterable[Defect], *, distance_km: float | None = None) -> dict[str, Any]:
    """Counts a municipal reader actually wants, keeping probable and verified apart.

    Conflating "the AI found 147" with "147 defects exist" is the fastest way to lose a
    pilot, so the two never share a number here.
    """
    defects = list(defects)
    by_class: dict[str, int] = {}
    by_status: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for d in defects:
        by_class[d.damage_class.value] = by_class.get(d.damage_class.value, 0) + 1
        by_status[d.status.value] = by_status.get(d.status.value, 0) + 1
        by_severity[d.severity.value] = by_severity.get(d.severity.value, 0) + 1

    summary: dict[str, Any] = {
        "total": len(defects),
        "by_class": by_class,
        "by_status": by_status,
        "by_severity": by_severity,
        "mean_location_uncertainty_m": (
            round(sum(d.location.uncertainty_m for d in defects) / len(defects), 2)
            if defects
            else None
        ),
    }
    if distance_km and distance_km > 0:
        summary["survey_distance_km"] = round(distance_km, 3)
        summary["defects_per_km"] = round(len(defects) / distance_km, 2)
    return summary
