"""Road geometry from an external network, and the index that makes matching fast.

A :class:`RoadNetwork` is a bag of short straight :class:`RoadSegment` pieces plus the
provenance of where they came from. It is deliberately *not* a routing graph: matching a
defect to the street it sits on needs proximity and direction, not connectivity, and a
graph would be a much larger thing to build, store and keep correct.

**The geometry is never committed to this repository.** OpenStreetMap data is ODbL, which
carries share-alike obligations on data; a cached extract living in a proprietary tree is
exactly the ambiguity ``docs/LICENSE_AUDIT.md`` (L-3) is trying not to create. Networks
are fetched into a git-ignored directory at run time, and every network carries the
attribution its licence requires so that an export can reproduce it.
"""

from __future__ import annotations

import gzip
import json
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from roadeye.geolocation.geodesy import (
    LatLon,
    bounding_box,
    haversine_m,
    initial_bearing_deg,
)

#: On-disk format version. Bump *and* write a migration on any breaking change.
ROAD_NETWORK_SCHEMA_VERSION = 1

#: Index cell size in degrees. ~550 m north-south, ~420 m east-west at Yerevan's
#: latitude — comfortably larger than a typical OSM way segment, so most segments land
#: in one or two cells while a lookup still scans only a handful.
BUCKET_DEG = 0.005


@dataclass(frozen=True)
class RoadSegment:
    """One straight piece of a road: the span between two consecutive way vertices.

    ``bearing_deg`` and ``length_m`` are derived from the endpoints on construction
    rather than stored, so they cannot drift out of agreement with the geometry.
    """

    segment_id: str
    way_id: str
    start: LatLon
    end: LatLon
    name: str | None = None
    highway: str = "unclassified"
    oneway: bool = False

    bearing_deg: float = field(init=False, repr=False)
    length_m: float = field(init=False, repr=False)

    def __post_init__(self) -> None:
        # Frozen dataclasses forbid plain assignment; this is the documented escape
        # hatch for derived fields.
        object.__setattr__(self, "length_m", haversine_m(self.start, self.end))
        bearing = 0.0 if self.start == self.end else initial_bearing_deg(self.start, self.end)
        object.__setattr__(self, "bearing_deg", bearing)

    def to_json(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "id": self.segment_id,
            "way": self.way_id,
            "a": [round(self.start.lat, 7), round(self.start.lon, 7)],
            "b": [round(self.end.lat, 7), round(self.end.lon, 7)],
            "highway": self.highway,
        }
        if self.name:
            record["name"] = self.name
        if self.oneway:
            record["oneway"] = True
        return record

    @classmethod
    def from_json(cls, record: dict[str, Any]) -> RoadSegment:
        return cls(
            segment_id=record["id"],
            way_id=record["way"],
            start=LatLon(*record["a"]),
            end=LatLon(*record["b"]),
            name=record.get("name"),
            highway=record.get("highway", "unclassified"),
            oneway=bool(record.get("oneway", False)),
        )


@dataclass(frozen=True)
class NetworkProvenance:
    """Where this geometry came from, and what its licence obliges us to say.

    ``attribution`` travels with every export built on this network. The obligation
    outlives the session that produced the file, so it belongs in the data, not only in
    a web page's footer.
    """

    source: str
    license: str
    attribution: str
    retrieved_at: datetime
    #: The Overpass query, file path or URL this came from — enough to fetch it again.
    query: str | None = None
    #: ``(min_lat, max_lat, min_lon, max_lon)`` actually covered, if known.
    bbox: tuple[float, float, float, float] | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "license": self.license,
            "attribution": self.attribution,
            "retrieved_at": self.retrieved_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "query": self.query,
            "bbox": list(self.bbox) if self.bbox else None,
        }

    @classmethod
    def from_json(cls, record: dict[str, Any]) -> NetworkProvenance:
        bbox = record.get("bbox")
        return cls(
            source=record["source"],
            license=record["license"],
            attribution=record["attribution"],
            retrieved_at=datetime.fromisoformat(record["retrieved_at"].replace("Z", "+00:00")),
            query=record.get("query"),
            bbox=tuple(bbox) if bbox else None,  # type: ignore[arg-type]
        )


class RoadNetwork:
    """Segments plus a uniform-grid index over them.

    The index is a plain dict of lat/lon cells. It is not an R*Tree and does not need to
    be: a city extract is tens of thousands of short segments, a lookup touches a handful
    of cells, and the whole thing costs no dependency and no file format. Following the
    same index-then-refine discipline as the SQLite side, the cells only narrow the
    candidate set — true distance is always recomputed.
    """

    def __init__(self, segments: list[RoadSegment], provenance: NetworkProvenance) -> None:
        self.segments = segments
        self.provenance = provenance
        self._index: dict[tuple[int, int], list[int]] = {}
        for i, segment in enumerate(segments):
            for cell in _cells_spanned(segment.start, segment.end):
                self._index.setdefault(cell, []).append(i)

    def __len__(self) -> int:
        return len(self.segments)

    @property
    def attribution(self) -> str:
        return self.provenance.attribution

    def nearby(self, point: LatLon, radius_m: float) -> list[RoadSegment]:
        """Segments whose index cells intersect a circle of ``radius_m`` around ``point``.

        A superset of the true answer, by design. Callers filter by real distance.
        """
        if radius_m < 0:
            raise ValueError(f"radius must be non-negative, got {radius_m}")
        min_lat, max_lat, min_lon, max_lon = bounding_box(point, radius_m)
        seen: set[int] = set()
        for lat_cell in range(_cell(min_lat), _cell(max_lat) + 1):
            for lon_cell in range(_cell(min_lon), _cell(max_lon) + 1):
                seen.update(self._index.get((lat_cell, lon_cell), ()))
        return [self.segments[i] for i in sorted(seen)]

    def named_streets(self) -> dict[str, int]:
        """Street name -> segment count. Useful for eyeballing a fresh extract."""
        counts: dict[str, int] = {}
        for segment in self.segments:
            if segment.name:
                counts[segment.name] = counts.get(segment.name, 0) + 1
        return counts

    # ------------------------------------------------------------------ persistence

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": ROAD_NETWORK_SCHEMA_VERSION,
            "provenance": self.provenance.to_json(),
            "segments": [s.to_json() for s in self.segments],
        }

    def save(self, path: str | Path) -> Path:
        """Write the network. A ``.gz`` suffix compresses it; a city extract shrinks ~5x."""
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.to_json(), separators=(",", ":"))
        if out.suffix == ".gz":
            with gzip.open(out, "wt", encoding="utf-8") as fh:
                fh.write(payload)
        else:
            out.write_text(payload, encoding="utf-8")
        return out

    @classmethod
    def load(cls, path: str | Path) -> RoadNetwork:
        src = Path(path)
        if src.suffix == ".gz":
            with gzip.open(src, "rt", encoding="utf-8") as fh:
                record = json.load(fh)
        else:
            record = json.loads(src.read_text(encoding="utf-8"))

        version = record.get("schema_version")
        if version != ROAD_NETWORK_SCHEMA_VERSION:
            raise ValueError(
                f"{src}: road network schema_version {version!r}, this build reads "
                f"{ROAD_NETWORK_SCHEMA_VERSION}. Re-fetch the network rather than "
                f"guessing at the difference."
            )
        return cls(
            segments=[RoadSegment.from_json(s) for s in record["segments"]],
            provenance=NetworkProvenance.from_json(record["provenance"]),
        )


def _cell(degrees: float) -> int:
    return math.floor(degrees / BUCKET_DEG)


def _cells_spanned(start: LatLon, end: LatLon) -> set[tuple[int, int]]:
    """Every index cell the segment's bounding box touches.

    The bounding box over-covers a diagonal segment, which costs a few extra candidates
    and never loses one. Missing a cell would silently drop a real match, so the error is
    taken in the safe direction.
    """
    lat_lo, lat_hi = sorted((start.lat, end.lat))
    lon_lo, lon_hi = sorted((start.lon, end.lon))
    return {
        (lat_cell, lon_cell)
        for lat_cell in range(_cell(lat_lo), _cell(lat_hi) + 1)
        for lon_cell in range(_cell(lon_lo), _cell(lon_hi) + 1)
    }
