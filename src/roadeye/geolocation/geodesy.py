"""Geodetic primitives.

Pure standard-library maths: no numpy, no GIS dependency. These functions are used
everywhere (interpolation, clustering, map-matching, uncertainty), so they are kept
dependency-free and heavily tested.

Model: a spherical Earth. Over the distances RoadEye cares about — tens of metres
between consecutive GPS fixes, a few kilometres per survey — the spherical error is
well under a metre, which is far smaller than consumer GPS accuracy (typically 3-15 m).
Using WGS84 ellipsoidal formulae here would add dependency weight and imply a precision
the input data does not have.

All angles are in **degrees** at the API boundary and radians internally.
"""

from __future__ import annotations

import math
from typing import NamedTuple

#: Mean Earth radius in metres (IUGG mean radius R1).
EARTH_RADIUS_M = 6_371_008.8


class LatLon(NamedTuple):
    """A geographic coordinate in decimal degrees (WGS84)."""

    lat: float
    lon: float


def _validate(lat: float, lon: float) -> None:
    if not -90.0 <= lat <= 90.0:
        raise ValueError(f"latitude out of range: {lat}")
    if not -180.0 <= lon <= 180.0:
        raise ValueError(f"longitude out of range: {lon}")


def haversine_m(a: LatLon, b: LatLon) -> float:
    """Great-circle distance between two points, in metres.

    >>> round(haversine_m(LatLon(40.0, 44.0), LatLon(40.0, 44.0)), 6)
    0.0
    """
    _validate(*a)
    _validate(*b)
    lat1, lon1, lat2, lon2 = map(math.radians, (a.lat, a.lon, b.lat, b.lon))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    # asin form is numerically better than acos for small distances, which is the
    # regime we actually operate in (consecutive GPS fixes metres apart).
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def initial_bearing_deg(a: LatLon, b: LatLon) -> float:
    """Initial great-circle bearing from ``a`` to ``b``, degrees clockwise from north.

    Returns a value in ``[0, 360)``.
    """
    _validate(*a)
    _validate(*b)
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    dlon = math.radians(b.lon - a.lon)
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return math.degrees(math.atan2(y, x)) % 360.0


def destination_point(origin: LatLon, bearing_deg: float, distance_m: float) -> LatLon:
    """Point reached by travelling ``distance_m`` from ``origin`` on ``bearing_deg``.

    This is how an estimated defect position is derived from a vehicle position plus a
    forward/lateral offset (see ``docs/GEOLOCATION.md``).
    """
    _validate(*origin)
    if distance_m < 0:
        raise ValueError(f"distance must be non-negative, got {distance_m}")
    ang = distance_m / EARTH_RADIUS_M
    brg = math.radians(bearing_deg)
    lat1, lon1 = math.radians(origin.lat), math.radians(origin.lon)

    sin_lat2 = math.sin(lat1) * math.cos(ang) + math.cos(lat1) * math.sin(ang) * math.cos(brg)
    lat2 = math.asin(max(-1.0, min(1.0, sin_lat2)))
    y = math.sin(brg) * math.sin(ang) * math.cos(lat1)
    x = math.cos(ang) - math.sin(lat1) * sin_lat2
    lon2 = lon1 + math.atan2(y, x)
    # Normalise longitude to [-180, 180).
    lon_deg = (math.degrees(lon2) + 540.0) % 360.0 - 180.0
    return LatLon(math.degrees(lat2), lon_deg)


def normalize_bearing(deg: float) -> float:
    """Wrap any bearing into ``[0, 360)``."""
    return deg % 360.0


def bearing_difference_deg(a: float, b: float) -> float:
    """Smallest absolute angular difference between two bearings, in ``[0, 180]``.

    Used by map-matching to test whether the vehicle's heading is compatible with a
    road segment's direction.
    """
    d = abs(normalize_bearing(a) - normalize_bearing(b)) % 360.0
    return 360.0 - d if d > 180.0 else d


def interpolate_bearing(a: float, b: float, alpha: float) -> float:
    """Interpolate between two bearings the short way around the circle.

    Naive linear interpolation is wrong at the wrap point: interpolating 350 -> 10
    linearly sweeps backwards through 180 instead of forwards through 0. Heading is
    circular, so this walks the shorter arc.

    >>> round(interpolate_bearing(350.0, 10.0, 0.5), 6)
    0.0
    """
    a_n, b_n = normalize_bearing(a), normalize_bearing(b)
    delta = ((b_n - a_n + 540.0) % 360.0) - 180.0
    return normalize_bearing(a_n + delta * alpha)


def interpolate_position(a: LatLon, b: LatLon, alpha: float) -> LatLon:
    """Linearly interpolate between two coordinates.

    Deliberately linear in lat/lon rather than a great-circle slerp. Between consecutive
    GPS fixes (metres apart, ~1 s) the difference is far below GPS noise, and linear is
    easier to reason about and to test. Over long gaps this assumption degrades, which
    is exactly why :mod:`roadeye.geolocation.timesync` flags large gaps rather than
    silently interpolating across them.
    """
    return LatLon(a.lat + (b.lat - a.lat) * alpha, a.lon + (b.lon - a.lon) * alpha)


def cross_track_distance_m(point: LatLon, seg_start: LatLon, seg_end: LatLon) -> float:
    """Perpendicular distance from ``point`` to the great circle through the segment.

    Signed magnitude is discarded (absolute value returned) because map-matching only
    cares how far off the road the fix is, not which side.
    """
    d13 = haversine_m(seg_start, point) / EARTH_RADIUS_M
    if d13 == 0.0:
        return 0.0
    theta13 = math.radians(initial_bearing_deg(seg_start, point))
    theta12 = math.radians(initial_bearing_deg(seg_start, seg_end))
    return abs(
        math.asin(max(-1.0, min(1.0, math.sin(d13) * math.sin(theta13 - theta12)))) * EARTH_RADIUS_M
    )


def point_to_segment_distance_m(point: LatLon, seg_start: LatLon, seg_end: LatLon) -> float:
    """Distance from ``point`` to the *finite* segment ``seg_start``-``seg_end``.

    Unlike :func:`cross_track_distance_m`, this clamps to the segment endpoints, so a
    point far beyond the end of a road segment is not reported as being on it.
    """
    if seg_start == seg_end:
        return haversine_m(point, seg_start)

    seg_len = haversine_m(seg_start, seg_end)
    # Along-track distance: projection of the point onto the segment's great circle.
    d13 = haversine_m(seg_start, point) / EARTH_RADIUS_M
    theta13 = math.radians(initial_bearing_deg(seg_start, point))
    theta12 = math.radians(initial_bearing_deg(seg_start, seg_end))
    xtd = math.asin(max(-1.0, min(1.0, math.sin(d13) * math.sin(theta13 - theta12))))
    cos_xtd = math.cos(xtd)
    if cos_xtd == 0.0:
        return abs(xtd) * EARTH_RADIUS_M
    ratio = max(-1.0, min(1.0, math.cos(d13) / cos_xtd))
    atd = math.acos(ratio) * EARTH_RADIUS_M

    if atd < 0.0:
        return haversine_m(point, seg_start)
    if atd > seg_len:
        return haversine_m(point, seg_end)
    return abs(xtd) * EARTH_RADIUS_M


def bounding_box(center: LatLon, radius_m: float) -> tuple[float, float, float, float]:
    """Axis-aligned lat/lon box enclosing a circle of ``radius_m`` around ``center``.

    Returned as ``(min_lat, max_lat, min_lon, max_lon)`` for direct use as an SQLite
    R*Tree query rectangle. The box is a conservative over-approximation: callers must
    still filter by true distance, which is the standard index-then-refine pattern.
    """
    _validate(*center)
    if radius_m < 0:
        raise ValueError(f"radius must be non-negative, got {radius_m}")
    dlat = math.degrees(radius_m / EARTH_RADIUS_M)
    # Guard against the cos() term collapsing near the poles.
    cos_lat = math.cos(math.radians(center.lat))
    dlon = 180.0 if abs(cos_lat) < 1e-9 else math.degrees(radius_m / (EARTH_RADIUS_M * cos_lat))
    return (
        max(-90.0, center.lat - dlat),
        min(90.0, center.lat + dlat),
        max(-180.0, center.lon - dlon),
        min(180.0, center.lon + dlon),
    )
