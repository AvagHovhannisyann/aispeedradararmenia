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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from roadeye.domain.models import Defect

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
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


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


def to_csv(defects: Sequence[Defect], path: str | Path) -> Path:
    """Write defects to CSV."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for defect in defects:
            writer.writerow(_row(defect))
    return out


def to_geojson(
    defects: Sequence[Defect],
    path: str | Path | None = None,
    *,
    attribution: str | None = None,
) -> dict[str, Any]:
    """Build a GeoJSON FeatureCollection, optionally writing it to ``path``.

    ``attribution`` should name any external data the export leans on. When defect
    positions have been map-matched against OpenStreetMap geometry, ODbL attribution
    belongs in the exported file itself, not only in the web UI — the file outlives the
    session that produced it.
    """
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
            "generated_at": _iso(datetime.now(timezone.utc)),
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
