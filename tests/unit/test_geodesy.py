"""Geodesy tests.

These functions underpin every distance, every merge decision and every exported
coordinate, so they are tested against known values rather than against themselves.
"""

from __future__ import annotations

import math

import pytest

from roadeye.geolocation.geodesy import (
    EARTH_RADIUS_M,
    LatLon,
    bearing_difference_deg,
    bounding_box,
    destination_point,
    haversine_m,
    initial_bearing_deg,
    interpolate_bearing,
    interpolate_position,
    normalize_bearing,
    point_to_segment_distance_m,
)

YEREVAN = LatLon(40.18231, 44.51491)


class TestHaversine:
    def test_zero_distance(self):
        assert haversine_m(YEREVAN, YEREVAN) == pytest.approx(0.0, abs=1e-6)

    def test_one_degree_of_latitude(self):
        """One degree of latitude is ~111.19 km anywhere on a sphere."""
        d = haversine_m(LatLon(40.0, 44.0), LatLon(41.0, 44.0))
        assert d == pytest.approx(math.pi / 180 * EARTH_RADIUS_M, rel=1e-9)
        assert d == pytest.approx(111_195, rel=1e-3)

    def test_longitude_shrinks_with_latitude(self):
        """A degree of longitude is shorter nearer the poles — catches a swapped
        cos(lat) term, which is the classic haversine typo.

        The comparison is against the *parallel arc* length, cos(lat) x equatorial.
        A great circle between two points at equal latitude bulges poleward and is
        therefore very slightly shorter than following the parallel, so we assert
        "just below, and close to" rather than exact equality. At one degree of
        separation the difference is ~0.5 m in 85 km.
        """
        at_equator = haversine_m(LatLon(0.0, 0.0), LatLon(0.0, 1.0))
        at_yerevan = haversine_m(LatLon(40.0, 0.0), LatLon(40.0, 1.0))
        parallel_arc = at_equator * math.cos(math.radians(40.0))

        assert at_yerevan < at_equator
        assert at_yerevan <= parallel_arc
        assert at_yerevan == pytest.approx(parallel_arc, rel=1e-4)

    def test_short_longitude_step_matches_parallel_arc(self):
        """Over the short distances RoadEye actually works at, the great circle and
        the parallel arc converge — so the cos(lat) relationship holds tightly."""
        at_equator = haversine_m(LatLon(0.0, 0.0), LatLon(0.0, 0.001))
        at_yerevan = haversine_m(LatLon(40.0, 0.0), LatLon(40.0, 0.001))
        assert at_yerevan == pytest.approx(at_equator * math.cos(math.radians(40.0)), rel=1e-9)

    def test_symmetric(self):
        a, b = LatLon(40.1, 44.5), LatLon(40.2, 44.6)
        assert haversine_m(a, b) == pytest.approx(haversine_m(b, a))

    def test_small_distance_precision(self):
        """Ten metres must not be lost to floating-point cancellation — this is the
        regime we actually operate in."""
        d = haversine_m(YEREVAN, LatLon(YEREVAN.lat, YEREVAN.lon + 0.000118))
        assert d == pytest.approx(10.0, abs=0.1)

    @pytest.mark.parametrize("lat,lon", [(91.0, 0.0), (-91.0, 0.0), (0.0, 181.0), (0.0, -181.0)])
    def test_rejects_out_of_range(self, lat, lon):
        with pytest.raises(ValueError):
            haversine_m(LatLon(lat, lon), YEREVAN)


class TestBearing:
    def test_due_north(self):
        assert initial_bearing_deg(LatLon(40.0, 44.0), LatLon(41.0, 44.0)) == pytest.approx(0.0)

    def test_due_east(self):
        assert initial_bearing_deg(LatLon(40.0, 44.0), LatLon(40.0, 45.0)) == pytest.approx(
            90.0, abs=0.5
        )

    def test_due_south(self):
        assert initial_bearing_deg(LatLon(41.0, 44.0), LatLon(40.0, 44.0)) == pytest.approx(180.0)

    def test_always_in_range(self):
        b = initial_bearing_deg(LatLon(40.0, 44.0), LatLon(39.0, 43.0))
        assert 0.0 <= b < 360.0

    @pytest.mark.parametrize(
        "a,b,expected",
        [
            (0.0, 0.0, 0.0),
            (0.0, 90.0, 90.0),
            (350.0, 10.0, 20.0),
            (10.0, 350.0, 20.0),
            (0.0, 180.0, 180.0),
        ],
    )
    def test_difference_takes_short_way(self, a, b, expected):
        assert bearing_difference_deg(a, b) == pytest.approx(expected)

    def test_normalize(self):
        assert normalize_bearing(370.0) == pytest.approx(10.0)
        assert normalize_bearing(-10.0) == pytest.approx(350.0)


class TestInterpolateBearing:
    def test_wraps_the_short_way_through_north(self):
        """350 -> 10 must pass through 0, not sweep backwards through 180.

        Naive linear interpolation gives 180 here. That would point a vehicle the wrong
        way down the street and break every heading-based map match.
        """
        assert interpolate_bearing(350.0, 10.0, 0.5) == pytest.approx(0.0)

    def test_reverse_direction_also_wraps(self):
        assert interpolate_bearing(10.0, 350.0, 0.5) == pytest.approx(0.0)

    def test_simple_case(self):
        assert interpolate_bearing(0.0, 90.0, 0.5) == pytest.approx(45.0)

    def test_endpoints_exact(self):
        assert interpolate_bearing(37.0, 200.0, 0.0) == pytest.approx(37.0)
        assert interpolate_bearing(37.0, 200.0, 1.0) == pytest.approx(200.0)

    def test_result_always_normalized(self):
        for alpha in (0.0, 0.25, 0.5, 0.75, 1.0):
            assert 0.0 <= interpolate_bearing(350.0, 20.0, alpha) < 360.0


class TestDestinationPoint:
    def test_round_trip(self):
        """Travelling out and measuring back returns the original distance."""
        target = destination_point(YEREVAN, 90.0, 100.0)
        assert haversine_m(YEREVAN, target) == pytest.approx(100.0, abs=0.01)

    def test_bearing_preserved(self):
        target = destination_point(YEREVAN, 45.0, 500.0)
        assert initial_bearing_deg(YEREVAN, target) == pytest.approx(45.0, abs=0.01)

    def test_east_increases_longitude(self):
        assert destination_point(YEREVAN, 90.0, 100.0).lon > YEREVAN.lon

    def test_north_increases_latitude(self):
        assert destination_point(YEREVAN, 0.0, 100.0).lat > YEREVAN.lat

    def test_zero_distance_is_identity(self):
        p = destination_point(YEREVAN, 123.0, 0.0)
        assert p.lat == pytest.approx(YEREVAN.lat)
        assert p.lon == pytest.approx(YEREVAN.lon)

    def test_rejects_negative_distance(self):
        with pytest.raises(ValueError):
            destination_point(YEREVAN, 90.0, -1.0)


class TestInterpolatePosition:
    def test_midpoint(self):
        a, b = LatLon(40.0, 44.0), LatLon(40.0, 44.001)
        assert interpolate_position(a, b, 0.5).lon == pytest.approx(44.0005)

    def test_endpoints(self):
        a, b = LatLon(40.0, 44.0), LatLon(41.0, 45.0)
        assert interpolate_position(a, b, 0.0) == a
        assert interpolate_position(a, b, 1.0) == b


class TestPointToSegment:
    def test_point_on_segment(self):
        d = point_to_segment_distance_m(
            LatLon(40.0, 44.0005), LatLon(40.0, 44.0), LatLon(40.0, 44.001)
        )
        assert d == pytest.approx(0.0, abs=0.5)

    def test_point_beside_segment(self):
        """A point offset perpendicular reports roughly that offset."""
        d = point_to_segment_distance_m(
            LatLon(40.0001, 44.0005), LatLon(40.0, 44.0), LatLon(40.0, 44.001)
        )
        assert d == pytest.approx(11.1, abs=1.0)

    def test_clamps_beyond_endpoint(self):
        """A point far past the end of a segment is not reported as being on it."""
        far = LatLon(40.0, 44.01)
        d = point_to_segment_distance_m(far, LatLon(40.0, 44.0), LatLon(40.0, 44.001))
        assert d == pytest.approx(haversine_m(far, LatLon(40.0, 44.001)), rel=0.05)

    def test_degenerate_segment(self):
        p, s = LatLon(40.001, 44.0), LatLon(40.0, 44.0)
        assert point_to_segment_distance_m(p, s, s) == pytest.approx(haversine_m(p, s))


class TestBoundingBox:
    def test_contains_the_circle(self):
        min_lat, max_lat, min_lon, max_lon = bounding_box(YEREVAN, 100.0)
        assert min_lat < YEREVAN.lat < max_lat
        assert min_lon < YEREVAN.lon < max_lon

    def test_is_conservative(self):
        """The box must over-approximate: every point at exactly the radius, in every
        direction, must fall inside. An under-approximating box would silently drop
        defects from R*Tree queries."""
        radius = 250.0
        min_lat, max_lat, min_lon, max_lon = bounding_box(YEREVAN, radius)
        for bearing in range(0, 360, 15):
            p = destination_point(YEREVAN, float(bearing), radius)
            assert min_lat <= p.lat <= max_lat
            assert min_lon <= p.lon <= max_lon

    def test_rejects_negative_radius(self):
        with pytest.raises(ValueError):
            bounding_box(YEREVAN, -1.0)
