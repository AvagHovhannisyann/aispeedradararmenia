"""Tests for assigning defects to streets.

All geometry here is **invented**. No OpenStreetMap data is committed to this
repository: OSM is ODbL, share-alike applies to data, and a cached extract sitting in a
proprietary tree is the ambiguity ``docs/LICENSE_AUDIT.md`` (L-3) exists to avoid. The
parsers are exercised against hand-written payloads in the OSM *format*, which is a
schema, not a database.

The properties under test are the ones that decide whether a municipal work order sends
a crew to the right street:

* uncertainty is never reduced by snapping, and grows to cover the move,
* heading decides between streets, not just distance,
* an ambiguous match is refused rather than guessed,
* matching changes position and road reference and nothing else.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from roadeye.domain.enums import (
    DamageClass,
    DefectStatus,
    LocationMethod,
    Severity,
    SeveritySource,
)
from roadeye.domain.models import Defect, GeoPoint
from roadeye.geolocation.geodesy import LatLon, destination_point, haversine_m
from roadeye.map_matching.matcher import (
    MatchingConfig,
    match_defects,
    match_point,
)
from roadeye.map_matching.network import (
    ROAD_NETWORK_SCHEMA_VERSION,
    NetworkProvenance,
    RoadNetwork,
    RoadSegment,
)
from roadeye.map_matching.osm import (
    OSM_ATTRIBUTION,
    BBox,
    overpass_query,
    parse_osm_xml,
    parse_overpass_json,
)

NOW = dt.datetime(2026, 8, 22, 9, 0, tzinfo=dt.UTC)

#: A crossroads in central Yerevan. Real coordinates, invented streets — a route through
#: (0, 0) would hide a latitude/longitude swap.
ORIGIN = LatLon(40.1850, 44.5150)


def way(
    way_id: int,
    name: str | None,
    start: LatLon,
    bearing: float,
    length_m: float,
    *,
    vertices: int = 6,
    highway: str = "primary",
    **tags: str,
) -> dict:
    """An Overpass ``out geom`` way element."""
    step = length_m / (vertices - 1)
    geometry = [
        {"lat": p.lat, "lon": p.lon}
        for p in (destination_point(start, bearing, step * i) for i in range(vertices))
    ]
    element_tags = {"highway": highway, **tags}
    if name:
        element_tags["name"] = name
    return {"type": "way", "id": way_id, "tags": element_tags, "geometry": geometry}


def crossroads() -> RoadNetwork:
    """Mashtots runs north through ORIGIN; Tumanyan runs east through it."""
    return parse_overpass_json(
        {
            "elements": [
                way(1, "Mashtots Avenue", destination_point(ORIGIN, 180, 200), 0.0, 400),
                way(2, "Tumanyan Street", destination_point(ORIGIN, 270, 200), 90.0, 400),
            ]
        }
    )


def point_near(bearing_from_origin: float, along_m: float, offset_m: float) -> GeoPoint:
    """A fix ``along_m`` up a street and ``offset_m`` to its right."""
    on_street = destination_point(ORIGIN, bearing_from_origin, along_m)
    off = destination_point(on_street, (bearing_from_origin + 90.0) % 360.0, offset_m)
    return GeoPoint(
        lat=off.lat,
        lon=off.lon,
        method=LocationMethod.INTERPOLATED_PHONE_GPS,
        uncertainty_m=8.0,
    )


def defect(defect_id: str, location: GeoPoint, **overrides) -> Defect:
    fields = {
        "defect_id": defect_id,
        "damage_class": DamageClass.POTHOLE,
        "location": location,
        "confidence": 0.71,
        "severity": Severity.UNASSESSED,
        "severity_source": SeveritySource.OTHER,
        "status": DefectStatus.PROBABLE,
        "observation_count": 3,
        "survey_ids": ["s1"],
        "first_seen": NOW,
        "last_seen": NOW,
        "model_id": "m1",
    }
    fields.update(overrides)
    return Defect(**fields)


class TestUncertaintyIsNeverImproved:
    """Snapping to a centreline feels like it should sharpen the estimate. It does not:
    the along-road position is still only as good as the GPS fix, and we do not know
    which lane or which side of the road."""

    def test_uncertainty_does_not_shrink(self):
        network = crossroads()
        location = point_near(0.0, 60.0, 3.0)
        result = match_point(network, location, heading_deg=0.0)
        assert result.matched
        assert result.location.uncertainty_m >= location.uncertainty_m

    def test_uncertainty_grows_to_cover_the_snap(self):
        """If the snap moved the point 12 m and we still claimed +/-8 m, the true
        position could now sit outside our own stated circle."""
        network = crossroads()
        location = point_near(0.0, 60.0, 12.0)
        result = match_point(network, location, heading_deg=0.0)
        assert result.matched

        moved = haversine_m(
            LatLon(location.lat, location.lon),
            LatLon(result.location.lat, result.location.lon),
        )
        assert moved == pytest.approx(12.0, abs=0.5)
        assert result.location.uncertainty_m >= moved - 1e-6
        assert result.location.uncertainty_m == pytest.approx(12.0, abs=0.5)

    def test_match_distance_is_recorded(self):
        """'On Mashtots Avenue' and '19 m from Mashtots Avenue, nothing else nearby' are
        different claims. Only one belongs on a work order, so the distance is kept."""
        network = crossroads()
        result = match_point(network, point_near(0.0, 60.0, 6.0), heading_deg=0.0)
        assert result.road is not None
        assert result.road.match_distance_m == pytest.approx(6.0, abs=0.5)
        assert result.road.heading_delta_deg == pytest.approx(0.0, abs=1.0)


class TestHeadingDecidesBetweenStreets:
    def test_heading_picks_the_street_that_was_driven(self):
        network = crossroads()
        # 5 m from the crossroads along Mashtots, so both streets are close.
        location = point_near(0.0, 8.0, 4.0)
        result = match_point(network, location, heading_deg=0.0)
        assert result.matched
        assert result.road is not None
        assert result.road.name == "Mashtots Avenue"

    def test_a_perpendicular_street_is_rejected_however_close(self):
        """The nearest centreline at a crossroads is often the one the survey never
        drove."""
        network = crossroads()
        result = match_point(network, point_near(0.0, 60.0, 3.0), heading_deg=90.0)
        assert not result.matched
        assert result.reason == "heading_mismatch"

    def test_heading_mismatch_is_distinguished_from_no_road_nearby(self):
        """'No road near this defect' and 'the only road near it runs the wrong way' are
        different problems; a stats line conflating them sends the reader astray."""
        network = crossroads()
        far = GeoPoint(
            lat=40.30,
            lon=44.70,
            method=LocationMethod.INTERPOLATED_PHONE_GPS,
            uncertainty_m=8.0,
        )
        assert match_point(network, far, heading_deg=0.0).reason == "no_candidates"

    def test_a_two_way_street_accepts_either_direction(self):
        network = parse_overpass_json(
            {"elements": [way(1, "Two Way", destination_point(ORIGIN, 180, 200), 0.0, 400)]}
        )
        result = match_point(network, point_near(0.0, 60.0, 4.0), heading_deg=180.0)
        assert result.matched, "driving a two-way street southbound is normal"

    def test_a_oneway_street_rejects_the_reverse(self):
        """Driving a one-way street backwards is not something the survey did, so a
        reversed heading there is evidence the match is wrong, not noise."""
        network = parse_overpass_json(
            {
                "elements": [
                    way(
                        1,
                        "One Way",
                        destination_point(ORIGIN, 180, 200),
                        0.0,
                        400,
                        oneway="yes",
                    )
                ]
            }
        )
        result = match_point(network, point_near(0.0, 60.0, 4.0), heading_deg=180.0)
        assert not result.matched
        assert result.reason == "heading_mismatch"

    def test_oneway_minus_one_reverses_the_stored_geometry(self):
        """``oneway=-1`` means one-way *against* the drawn direction. Stored unreversed,
        the bearing would be 180 degrees from the direction traffic actually travels and
        the heading check would reject every correct match."""
        network = parse_overpass_json(
            {
                "elements": [
                    way(
                        1,
                        "Reverse Way",
                        destination_point(ORIGIN, 180, 200),
                        0.0,
                        400,
                        oneway="-1",
                    )
                ]
            }
        )
        assert all(s.oneway for s in network.segments)
        assert network.segments[0].bearing_deg == pytest.approx(180.0, abs=1.0)

        southbound = match_point(network, point_near(0.0, 60.0, 4.0), heading_deg=180.0)
        assert southbound.matched

    def test_a_roundabout_is_oneway_without_saying_so(self):
        network = parse_overpass_json(
            {
                "elements": [
                    way(
                        1,
                        "Circle",
                        ORIGIN,
                        0.0,
                        100,
                        junction="roundabout",
                    )
                ]
            }
        )
        assert all(s.oneway for s in network.segments)


class TestAmbiguityIsRefused:
    def test_two_streets_at_the_same_distance_produce_no_match(self):
        """A defect keeping its interpolated coordinate is a nuisance. A defect labelled
        with the wrong street sends a crew to the wrong place, and nothing downstream can
        tell that it happened."""
        network = crossroads()
        centre = GeoPoint(
            lat=ORIGIN.lat,
            lon=ORIGIN.lon,
            method=LocationMethod.INTERPOLATED_PHONE_GPS,
            uncertainty_m=8.0,
        )
        result = match_point(network, centre)
        assert not result.matched
        assert result.reason == "ambiguous"

    def test_consecutive_segments_of_one_street_are_not_ambiguous(self):
        """A way is stored as many short segments. Being equidistant from two of them is
        the normal case and picking either is correct — ambiguity is about streets."""
        network = crossroads()
        # Sitting on a shape point of Mashtots: two of its segments are equidistant.
        on_vertex = destination_point(destination_point(ORIGIN, 180, 200), 0.0, 80.0)
        location = GeoPoint(
            lat=on_vertex.lat,
            lon=on_vertex.lon,
            method=LocationMethod.INTERPOLATED_PHONE_GPS,
            uncertainty_m=8.0,
        )
        result = match_point(network, location, heading_deg=0.0)
        assert result.matched
        assert result.road is not None
        assert result.road.name == "Mashtots Avenue"

    def test_heading_resolves_what_distance_cannot(self):
        network = crossroads()
        centre = GeoPoint(
            lat=ORIGIN.lat,
            lon=ORIGIN.lon,
            method=LocationMethod.INTERPOLATED_PHONE_GPS,
            uncertainty_m=8.0,
        )
        result = match_point(network, centre, heading_deg=0.0)
        assert result.matched
        assert result.road is not None
        assert result.road.name == "Mashtots Avenue"


class TestDistanceLimits:
    def test_a_defect_far_from_any_road_is_not_matched(self):
        """30 m from the nearest centreline is a car park or a courtyard, not that road."""
        network = crossroads()
        result = match_point(network, point_near(0.0, 60.0, 40.0), heading_deg=0.0)
        assert not result.matched

    def test_max_distance_is_respected(self):
        network = crossroads()
        location = point_near(0.0, 60.0, 18.0)
        tight = MatchingConfig(max_distance_m=10.0)
        assert not match_point(network, location, heading_deg=0.0, config=tight).matched
        loose = MatchingConfig(max_distance_m=25.0, min_search_radius_m=25.0)
        assert match_point(network, location, heading_deg=0.0, config=loose).matched

    def test_require_named_skips_unnamed_geometry(self):
        network = parse_overpass_json(
            {
                "elements": [
                    way(1, None, destination_point(ORIGIN, 180, 200), 0.0, 400, highway="service")
                ]
            }
        )
        location = point_near(0.0, 60.0, 4.0)
        assert match_point(network, location, heading_deg=0.0).matched
        strict = MatchingConfig(require_named=True)
        assert not match_point(network, location, heading_deg=0.0, config=strict).matched


class TestMatchingChangesNothingElse:
    def test_only_location_and_road_change(self):
        """Map matching is a statement about where a defect is, never about whether it
        exists or what it is."""
        network = crossroads()
        original = defect("d1", point_near(0.0, 60.0, 5.0))
        matched, _ = match_defects([original], network, headings={"d1": 0.0})

        before = original.model_dump()
        after = matched[0].model_dump()
        changed = {k for k in before if before[k] != after[k]}
        assert changed <= {"location", "road", "updated_at"}, changed

    def test_a_human_corrected_position_is_never_overwritten(self):
        """MANUAL_CORRECTION outranks anything a machine derives."""
        network = crossroads()
        corrected = point_near(0.0, 60.0, 5.0).model_copy(
            update={"method": LocationMethod.MANUAL_CORRECTION}
        )
        original = defect("d1", corrected)
        matched, stats = match_defects([original], network, headings={"d1": 0.0})
        assert matched[0] is original
        assert stats["skipped_manual"] == 1

    def test_stats_account_for_every_defect(self):
        """A silent map-matching pass is one nobody can audit."""
        network = crossroads()
        defects = [
            defect("hit", point_near(0.0, 60.0, 5.0)),
            defect("ambiguous", point_near(0.0, 0.0, 0.0)),
            defect(
                "far",
                GeoPoint(
                    lat=40.30,
                    lon=44.70,
                    method=LocationMethod.INTERPOLATED_PHONE_GPS,
                    uncertainty_m=8.0,
                ),
            ),
        ]
        _, stats = match_defects(defects, network, headings={"hit": 0.0})
        assert sum(v for k, v in stats.items() if k != "matched_named") == len(defects)

    def test_the_matched_location_method_says_how_it_was_derived(self):
        network = crossroads()
        matched, _ = match_defects(
            [defect("d1", point_near(0.0, 60.0, 5.0))], network, headings={"d1": 0.0}
        )
        assert matched[0].location.method is LocationMethod.ROAD_SEGMENT_MATCHED


class TestNetwork:
    def test_derived_fields_follow_the_geometry(self):
        segment = RoadSegment(
            segment_id="way/1#0",
            way_id="way/1",
            start=ORIGIN,
            end=destination_point(ORIGIN, 90.0, 100.0),
        )
        assert segment.length_m == pytest.approx(100.0, abs=0.5)
        assert segment.bearing_deg == pytest.approx(90.0, abs=0.5)

    def test_nearby_returns_a_superset_never_a_subset(self):
        """The index only narrows candidates; missing one silently drops a real match, so
        the over-approximation is deliberate."""
        network = crossroads()
        location = point_near(0.0, 60.0, 5.0)
        point = LatLon(location.lat, location.lon)

        found = set(network.nearby(point, 30.0))
        truly_near = {
            s
            for s in network.segments
            if min(haversine_m(point, s.start), haversine_m(point, s.end)) <= 30.0
        }
        assert truly_near <= found

    def test_round_trips_through_a_file(self, tmp_path):
        network = crossroads()
        path = network.save(tmp_path / "roads.json")
        loaded = RoadNetwork.load(path)
        assert len(loaded) == len(network)
        assert loaded.named_streets() == network.named_streets()
        assert loaded.provenance.attribution == network.provenance.attribution

    def test_round_trips_through_a_gzipped_file(self, tmp_path):
        network = crossroads()
        path = network.save(tmp_path / "roads.json.gz")
        assert RoadNetwork.load(path).named_streets() == network.named_streets()

    def test_a_future_schema_version_is_refused(self, tmp_path):
        """Reading a newer file as if it were this one is how a silent format change
        becomes wrong coordinates."""
        path = tmp_path / "roads.json"
        payload = crossroads().to_json()
        payload["schema_version"] = ROAD_NETWORK_SCHEMA_VERSION + 1
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ValueError, match="schema_version"):
            RoadNetwork.load(path)

    def test_provenance_survives_the_round_trip(self, tmp_path):
        network = RoadNetwork(
            segments=crossroads().segments,
            provenance=NetworkProvenance(
                source="osm",
                license="ODbL-1.0",
                attribution=OSM_ATTRIBUTION,
                retrieved_at=NOW,
                query="[out:json];way[highway](...);out geom;",
                bbox=(40.18, 40.19, 44.51, 44.52),
            ),
        )
        loaded = RoadNetwork.load(network.save(tmp_path / "r.json"))
        assert loaded.provenance.attribution == OSM_ATTRIBUTION
        assert loaded.provenance.retrieved_at == NOW
        assert loaded.provenance.bbox == (40.18, 40.19, 44.51, 44.52)


class TestOsmParsing:
    def test_non_drivable_highways_are_excluded(self):
        """A defect seen from a car must not be matched onto a footpath two metres
        away."""
        network = parse_overpass_json(
            {
                "elements": [
                    way(1, "Pedestrian Alley", ORIGIN, 0.0, 200, highway="footway"),
                    way(2, "Bike Path", ORIGIN, 90.0, 200, highway="cycleway"),
                    way(3, "Real Street", ORIGIN, 45.0, 200, highway="residential"),
                ]
            }
        )
        assert set(network.named_streets()) == {"Real Street"}

    def test_zero_length_segments_are_dropped(self):
        """Duplicated nodes occur in real data, and a zero-length segment has no bearing
        — which would silently disable the heading check for anything matching it."""
        duplicated = {
            "type": "way",
            "id": 1,
            "tags": {"highway": "residential", "name": "Doubled"},
            "geometry": [
                {"lat": ORIGIN.lat, "lon": ORIGIN.lon},
                {"lat": ORIGIN.lat, "lon": ORIGIN.lon},
                {"lat": ORIGIN.lat + 0.001, "lon": ORIGIN.lon},
            ],
        }
        network = parse_overpass_json({"elements": [duplicated]})
        assert len(network) == 1
        assert all(s.length_m > 0 for s in network.segments)

    def test_a_way_with_one_point_yields_nothing(self):
        single = {
            "type": "way",
            "id": 1,
            "tags": {"highway": "residential"},
            "geometry": [{"lat": ORIGIN.lat, "lon": ORIGIN.lon}],
        }
        assert len(parse_overpass_json({"elements": [single]})) == 0

    def test_osm_data_carries_its_attribution(self):
        """ODbL is not optional, and an attribution someone must remember to add is one
        that eventually gets forgotten."""
        assert crossroads().attribution == OSM_ATTRIBUTION
        assert crossroads().provenance.license == "ODbL-1.0"

    def test_xml_and_overpass_json_agree(self, tmp_path):
        xml = f"""<?xml version="1.0"?>
<osm version="0.6">
  <node id="1" lat="{ORIGIN.lat}" lon="{ORIGIN.lon}"/>
  <node id="2" lat="{ORIGIN.lat + 0.002}" lon="{ORIGIN.lon}"/>
  <node id="3" lat="{ORIGIN.lat + 0.004}" lon="{ORIGIN.lon}"/>
  <way id="10">
    <nd ref="1"/><nd ref="2"/><nd ref="3"/>
    <tag k="highway" v="primary"/>
    <tag k="name" v="Mashtots Avenue"/>
  </way>
  <way id="11">
    <nd ref="1"/><nd ref="2"/>
    <tag k="highway" v="footway"/>
  </way>
</osm>"""
        path = tmp_path / "a.osm"
        path.write_text(xml, encoding="utf-8")
        network = parse_osm_xml(path)
        assert network.named_streets() == {"Mashtots Avenue": 2}
        assert network.attribution == OSM_ATTRIBUTION

    def test_xml_nodes_may_follow_the_ways_that_reference_them(self):
        """The format does not promise nodes come first. They usually do, and a parser
        relying on 'usually' silently drops geometry on the file that doesn't."""
        xml = f"""<?xml version="1.0"?>
<osm version="0.6">
  <way id="10">
    <nd ref="1"/><nd ref="2"/>
    <tag k="highway" v="residential"/>
    <tag k="name" v="Late Nodes Street"/>
  </way>
  <node id="1" lat="{ORIGIN.lat}" lon="{ORIGIN.lon}"/>
  <node id="2" lat="{ORIGIN.lat + 0.002}" lon="{ORIGIN.lon}"/>
</osm>"""
        network = parse_osm_xml(_as_stream(xml))
        assert network.named_streets() == {"Late Nodes Street": 1}

    def test_a_way_referencing_a_missing_node_is_truncated_not_dropped(self):
        """Extracts cut at a bbox boundary do this constantly, and the part of the street
        inside the box is still worth matching against."""
        xml = f"""<?xml version="1.0"?>
<osm version="0.6">
  <node id="1" lat="{ORIGIN.lat}" lon="{ORIGIN.lon}"/>
  <node id="2" lat="{ORIGIN.lat + 0.002}" lon="{ORIGIN.lon}"/>
  <way id="10">
    <nd ref="1"/><nd ref="2"/><nd ref="999"/>
    <tag k="highway" v="residential"/>
    <tag k="name" v="Clipped Street"/>
  </way>
</osm>"""
        network = parse_osm_xml(_as_stream(xml))
        assert network.named_streets() == {"Clipped Street": 1}

    def test_a_caller_supplied_stream_is_not_closed(self):
        stream = _as_stream('<?xml version="1.0"?><osm version="0.6"></osm>')
        parse_osm_xml(stream)
        assert not stream.closed, "closing a handle the caller opened breaks them later"


class TestBBox:
    def test_overpass_orders_south_west_north_east(self):
        """Overpass, GeoJSON and this project's own bounding_box() each order a box
        differently. Getting it wrong queries the wrong hemisphere."""
        assert BBox(40.1, 44.4, 40.2, 44.6).overpass() == "40.1,44.4,40.2,44.6"

    def test_parse_round_trips(self):
        assert BBox.parse("40.1,44.4,40.2,44.6") == BBox(40.1, 44.4, 40.2, 44.6)

    def test_a_flipped_box_is_rejected(self):
        with pytest.raises(ValueError, match="degenerate"):
            BBox(40.2, 44.4, 40.1, 44.6)

    def test_parse_rejects_the_wrong_arity(self):
        with pytest.raises(ValueError, match="min_lat"):
            BBox.parse("40.1,44.4,40.2")

    def test_the_query_asks_only_for_drivable_roads(self):
        query = overpass_query(BBox(40.1, 44.4, 40.2, 44.6))
        assert "residential" in query and "footway" not in query
        assert "out geom" in query


def _as_stream(text: str):
    import io

    return io.BytesIO(text.encode("utf-8"))
