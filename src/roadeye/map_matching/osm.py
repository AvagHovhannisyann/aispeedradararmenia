"""Turn OpenStreetMap data into a :class:`RoadNetwork`.

Two input shapes, both parsed with the standard library only:

* **Overpass JSON** (``out geom;``) — the normal path. Overpass is built for exactly this
  query and returns way geometry inline, so no node table is needed.
* **OSM XML** — what an ``.osm`` file or the editing API returns. Useful offline and when
  Overpass is unreachable, which it is from some networks.

``.osm.pbf`` is deliberately not supported. It would need a protobuf decoder, and every
dependency in this project has to be licence-audited and justified; Overpass or an XML
extract covers a city without that. See ``docs/MAP_MATCHING.md``.

**Attribution is not optional.** OpenStreetMap is ODbL: any export leaning on this
geometry must carry "(c) OpenStreetMap contributors". Every network built here is stamped
with it so nothing downstream has to remember.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

from roadeye.geolocation.geodesy import LatLon
from roadeye.map_matching.network import NetworkProvenance, RoadNetwork, RoadSegment

#: Licence and attribution for anything OSM-derived. Verbatim from the OSM copyright
#: page; the wording is the obligation, so it is a constant rather than a formatted
#: string.
OSM_LICENSE = "ODbL-1.0"
OSM_ATTRIBUTION = "© OpenStreetMap contributors"

#: Public Overpass instance. Free and donated, with a usage policy: be gentle, cache
#: aggressively, identify yourself. RoadEye fetches a city once and reuses the file.
DEFAULT_OVERPASS_ENDPOINT = "https://overpass-api.de/api/interpreter"

USER_AGENT = "RoadEye/0.1 (road-condition survey; https://github.com/AvagHovhannisyann)"

#: Highway values a car-mounted survey can actually be driving on. Footways, cycleways,
#: steps and paths are excluded: matching a defect seen from a car onto a footpath two
#: metres away would be precisely the confident-and-wrong failure this module exists to
#: avoid.
DRIVABLE_HIGHWAYS = frozenset(
    {
        "motorway",
        "motorway_link",
        "trunk",
        "trunk_link",
        "primary",
        "primary_link",
        "secondary",
        "secondary_link",
        "tertiary",
        "tertiary_link",
        "unclassified",
        "residential",
        "living_street",
        "service",
        "road",
    }
)


@dataclass(frozen=True)
class BBox:
    """A geographic box, with the field order written down.

    Latitude/longitude ordering is the most reliably repeated bug in geospatial code —
    GeoJSON says ``[lon, lat]``, Overpass says ``south,west,north,east``, and this
    project's own :func:`~roadeye.geolocation.geodesy.bounding_box` returns
    ``(min_lat, max_lat, min_lon, max_lon)``. Naming the fields removes the guessing.
    """

    min_lat: float
    min_lon: float
    max_lat: float
    max_lon: float

    def __post_init__(self) -> None:
        if self.min_lat > self.max_lat or self.min_lon > self.max_lon:
            raise ValueError(f"degenerate bbox: {self}")

    @classmethod
    def around(cls, center: LatLon, radius_m: float) -> BBox:
        from roadeye.geolocation.geodesy import bounding_box

        min_lat, max_lat, min_lon, max_lon = bounding_box(center, radius_m)
        return cls(min_lat, min_lon, max_lat, max_lon)

    @classmethod
    def parse(cls, text: str) -> BBox:
        """Parse ``min_lat,min_lon,max_lat,max_lon`` as accepted on the command line."""
        parts = [p.strip() for p in text.split(",")]
        if len(parts) != 4:
            raise ValueError(f"expected 'min_lat,min_lon,max_lat,max_lon', got {text!r}")
        return cls(*(float(p) for p in parts))

    def overpass(self) -> str:
        """Overpass orders a bbox south, west, north, east."""
        return f"{self.min_lat},{self.min_lon},{self.max_lat},{self.max_lon}"

    def as_tuple(self) -> tuple[float, float, float, float]:
        """``(min_lat, max_lat, min_lon, max_lon)`` — the provenance/R*Tree ordering."""
        return (self.min_lat, self.max_lat, self.min_lon, self.max_lon)


def overpass_query(bbox: BBox, *, timeout_s: int = 180) -> str:
    """An Overpass QL query for drivable roads in ``bbox``.

    ``out geom`` returns each way's coordinates inline, which is what lets this module
    stay free of a node-resolution pass.
    """
    values = "|".join(sorted(DRIVABLE_HIGHWAYS))
    return (
        f"[out:json][timeout:{timeout_s}];\n"
        f'way["highway"~"^({values})$"]({bbox.overpass()});\n'
        f"out geom;"
    )


def fetch_overpass(
    bbox: BBox,
    *,
    endpoint: str = DEFAULT_OVERPASS_ENDPOINT,
    timeout_s: int = 180,
) -> RoadNetwork:
    """Fetch drivable roads for ``bbox`` from an Overpass instance.

    Never called by the test suite: the suite must pass with no network. Failures are
    re-raised with the endpoint named, because "connection reset" without context sends
    people looking in the wrong place — some networks block Overpass outright.
    """
    query = overpass_query(bbox, timeout_s=timeout_s)
    request = urllib.request.Request(
        endpoint,
        data=query.encode("utf-8"),
        headers={"User-Agent": USER_AGENT, "Content-Type": "text/plain; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s + 30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise OSError(
            f"Overpass request to {endpoint} failed: {exc}. Some networks block public "
            f"Overpass instances; export an .osm file instead and use "
            f"'roadeye roads import'."
        ) from exc

    return parse_overpass_json(
        payload,
        query=query,
        bbox=bbox,
    )


def parse_overpass_json(
    payload: dict[str, Any],
    *,
    query: str | None = None,
    bbox: BBox | None = None,
    retrieved_at: datetime | None = None,
) -> RoadNetwork:
    """Build a network from an Overpass ``out geom`` response."""
    segments: list[RoadSegment] = []
    for element in payload.get("elements", []):
        if element.get("type") != "way":
            continue
        tags = element.get("tags") or {}
        highway = tags.get("highway")
        if highway not in DRIVABLE_HIGHWAYS:
            continue
        points = [
            LatLon(float(node["lat"]), float(node["lon"]))
            for node in element.get("geometry") or []
            if node.get("lat") is not None and node.get("lon") is not None
        ]
        segments.extend(_segments_for_way(str(element.get("id")), points, tags))

    return RoadNetwork(
        segments=segments,
        provenance=NetworkProvenance(
            source="osm",
            license=OSM_LICENSE,
            attribution=OSM_ATTRIBUTION,
            retrieved_at=retrieved_at or datetime.now(UTC),
            query=query,
            bbox=bbox.as_tuple() if bbox else None,
        ),
    )


def parse_osm_xml(
    source: str | Path | BinaryIO,
    *,
    bbox: BBox | None = None,
    retrieved_at: datetime | None = None,
) -> RoadNetwork:
    """Build a network from OSM XML.

    One streaming pass. Nodes go into a coordinate dict and ways are buffered as their
    node-id lists, then resolved at the end. Ways are not resolved inline because the
    format does not promise nodes come first — they usually do, and code that relies on
    "usually" in a geospatial parser silently drops geometry on the file that doesn't.

    A way referencing a node the file does not contain is truncated at that point rather
    than dropped: an extract cut at a bbox boundary does this constantly, and the part of
    the street inside the box is still worth matching against.
    """
    handle, should_close = _as_binary(source)
    nodes: dict[str, LatLon] = {}
    ways: list[tuple[str, list[str], dict[str, Any]]] = []
    try:
        for _, element in ET.iterparse(handle, events=("end",)):
            if element.tag == "node":
                node_id = element.get("id")
                lat, lon = element.get("lat"), element.get("lon")
                if node_id and lat is not None and lon is not None:
                    nodes[node_id] = LatLon(float(lat), float(lon))
                element.clear()
            elif element.tag == "way":
                tags = {
                    key: tag.get("v")
                    for tag in element.findall("tag")
                    if (key := tag.get("k")) is not None
                }
                if tags.get("highway") in DRIVABLE_HIGHWAYS:
                    refs = [ref for nd in element.findall("nd") if (ref := nd.get("ref"))]
                    ways.append((element.get("id") or "?", refs, tags))
                element.clear()
    finally:
        if should_close:
            handle.close()

    segments: list[RoadSegment] = []
    for way_id, refs, tags in ways:
        points = [nodes[ref] for ref in refs if ref in nodes]
        segments.extend(_segments_for_way(way_id, points, tags))

    return RoadNetwork(
        segments=segments,
        provenance=NetworkProvenance(
            source="osm",
            license=OSM_LICENSE,
            attribution=OSM_ATTRIBUTION,
            retrieved_at=retrieved_at or datetime.now(UTC),
            query=str(source) if isinstance(source, str | Path) else None,
            bbox=bbox.as_tuple() if bbox else None,
        ),
    )


def _as_binary(source: str | Path | BinaryIO) -> tuple[BinaryIO, bool]:
    """Return a readable handle and whether closing it is ours to do.

    Closing a handle the caller opened is rude and, worse, breaks them silently later.
    """
    if isinstance(source, str | Path):
        return Path(source).open("rb"), True
    source.seek(0)
    return source, False


def _segments_for_way(way_id: str, points: list[LatLon], tags: dict[str, Any]) -> list[RoadSegment]:
    """Split a way into its consecutive vertex pairs.

    Zero-length pairs are dropped: duplicated nodes occur in real OSM data and a
    zero-length segment has no bearing, which would silently disable the heading check
    for anything that matched it.
    """
    if len(points) < 2:
        return []

    oneway, reverse = _oneway(tags)
    if reverse:
        # oneway=-1 means the way is one-way *against* its drawn direction. Reversing the
        # geometry makes the stored bearing the direction traffic actually travels, which
        # is the only thing the heading check can compare against.
        points = list(reversed(points))

    name = tags.get("name") or None
    highway = str(tags.get("highway", "unclassified"))

    segments: list[RoadSegment] = []
    for i, (a, b) in enumerate(zip(points, points[1:], strict=False)):
        if a == b:
            continue
        segments.append(
            RoadSegment(
                segment_id=f"way/{way_id}#{i}",
                way_id=f"way/{way_id}",
                start=a,
                end=b,
                name=name,
                highway=highway,
                oneway=oneway,
            )
        )
    return segments


def _oneway(tags: dict[str, Any]) -> tuple[bool, bool]:
    """``(is_oneway, reverse_geometry)`` from OSM's several ways of saying it."""
    value = str(tags.get("oneway", "")).strip().lower()
    if value in {"yes", "true", "1"}:
        return True, False
    if value == "-1":
        return True, True
    if value in {"no", "false", "0"}:
        return False, False
    # A roundabout is one-way by definition and very often carries no oneway tag.
    if str(tags.get("junction", "")).strip().lower() in {"roundabout", "circular"}:
        return True, False
    return False, False
