"""Tests for rolling defects up to the stretch of road they sit on.

The numbers here go into a maintenance budget, so the failure modes are about a report
that is *structurally* misleading rather than arithmetically wrong:

* a street nobody drove reading as a street with no defects,
* a density computed from a sample too short to divide by,
* rejected defects counted back into the total a crew is sent to,
* defects that matched nothing quietly vanishing.

All geometry is invented — no OpenStreetMap data is committed (ODbL, L-3).
"""

from __future__ import annotations

import datetime as dt

import pytest

from roadeye.domain.enums import (
    DamageClass,
    DefectStatus,
    LocationMethod,
    Severity,
    SeveritySource,
)
from roadeye.domain.models import Defect, Frame, GeoPoint
from roadeye.geolocation.geodesy import LatLon, destination_point
from roadeye.map_matching.network import NetworkProvenance, RoadNetwork, RoadSegment
from roadeye.reporting.segments import (
    MAX_FRAME_GAP_M,
    MIN_LENGTH_FOR_DENSITY_M,
    build_street_report,
)

NOW = dt.datetime(2026, 8, 22, 9, 0, tzinfo=dt.UTC)
ORIGIN = LatLon(40.1850, 44.5150)


def street(way: int, name: str, start: LatLon, bearing: float, length_m: float, pieces: int = 8):
    step = length_m / pieces
    points = [destination_point(start, bearing, step * i) for i in range(pieces + 1)]
    return [
        RoadSegment(
            segment_id=f"way/{way}#{i}",
            way_id=f"way/{way}",
            start=a,
            end=b,
            name=name,
            highway="residential",
        )
        for i, (a, b) in enumerate(zip(points, points[1:], strict=False))
    ]


def network(*groups: list[RoadSegment]) -> RoadNetwork:
    segments = [s for group in groups for s in group]
    return RoadNetwork(
        segments=segments,
        provenance=NetworkProvenance(
            source="synthetic",
            license="none — invented geometry",
            attribution="Invented for tests",
            retrieved_at=NOW,
        ),
    )


def drive(
    survey_id: str,
    start: LatLon,
    bearing: float,
    length_m: float,
    *,
    spacing_m: float = 5.0,
    first_index: int = 0,
) -> list[Frame]:
    """Frames along a straight line, one every ``spacing_m``."""
    count = int(length_m / spacing_m) + 1
    frames = []
    for i in range(count):
        point = destination_point(start, bearing, spacing_m * i)
        frames.append(
            Frame(
                frame_id=f"{survey_id}:f{first_index + i}",
                survey_id=survey_id,
                video_time_s=float(first_index + i),
                t_epoch_ms=int(NOW.timestamp() * 1000) + (first_index + i) * 1000,
                observation_location=GeoPoint(
                    lat=point.lat,
                    lon=point.lon,
                    method=LocationMethod.INTERPOLATED_PHONE_GPS,
                    uncertainty_m=5.0,
                ),
                heading_deg=bearing,
            )
        )
    return frames


def defect(
    defect_id: str,
    at: LatLon,
    *,
    status: DefectStatus = DefectStatus.PROBABLE,
    damage_class: DamageClass = DamageClass.POTHOLE,
    severity: Severity = Severity.UNASSESSED,
    severity_source: SeveritySource = SeveritySource.OTHER,
    survey_id: str = "s1",
) -> Defect:
    return Defect(
        defect_id=defect_id,
        damage_class=damage_class,
        location=GeoPoint(
            lat=at.lat,
            lon=at.lon,
            method=LocationMethod.INTERPOLATED_PHONE_GPS,
            uncertainty_m=6.0,
        ),
        confidence=0.8,
        severity=severity,
        severity_source=severity_source,
        status=status,
        first_seen=NOW,
        last_seen=NOW,
        survey_ids=[survey_id],
        observation_count=2,
    )


def find(report, name: str):
    return next((s for s in report.streets if s.name == name), None)


class TestCleanIsNotTheSameAsNeverDriven:
    """The distinction the whole module exists for. A rollup built only from defects
    cannot tell them apart — both produce an absent row — and a municipality reading one
    as the other is misled by the report's structure, not by a number."""

    def test_a_driven_street_with_no_defects_reads_as_clean(self):
        driven = street(1, "Driven Street", ORIGIN, 0.0, 400)
        report = build_street_report([], drive("s1", ORIGIN, 0.0, 400), network(driven))

        row = find(report, "Driven Street")
        assert row is not None, "a driven street must appear even with no defects"
        assert row.state == "clean"
        assert row.surveyed_m > 300

    def test_an_undriven_street_reads_as_not_surveyed(self):
        driven = street(1, "Driven Street", ORIGIN, 0.0, 400)
        elsewhere = destination_point(ORIGIN, 45.0, 3000.0)
        untouched = street(2, "Untouched Street", elsewhere, 90.0, 400)

        report = build_street_report(
            [],
            drive("s1", ORIGIN, 0.0, 400),
            network(driven, untouched),
            include_unsurveyed=True,
        )

        row = find(report, "Untouched Street")
        assert row is not None
        assert row.state == "not_surveyed"
        assert row.surveyed_m == 0.0
        assert row.defects_per_100m is None, "no coverage means no density, not zero"

    def test_the_denominator_is_reported_even_when_rows_are_hidden(self):
        """Four busy streets read as a survey of the city unless the report says how
        much of the network they are."""
        driven = street(1, "Driven Street", ORIGIN, 0.0, 400)
        elsewhere = destination_point(ORIGIN, 45.0, 3000.0)
        untouched = street(2, "Untouched Street", elsewhere, 90.0, 1600)

        report = build_street_report([], drive("s1", ORIGIN, 0.0, 400), network(driven, untouched))

        assert len(report.streets) == 1, "unsurveyed rows are hidden by default"
        assert report.network_ways == 2
        assert report.coverage_fraction is not None
        assert 0.1 < report.coverage_fraction < 0.3
        assert "never driven" in report.summary()


class TestCoverageComesFromFrames:
    def test_length_matches_the_distance_driven(self):
        driven = street(1, "Measured Street", ORIGIN, 0.0, 500)
        report = build_street_report([], drive("s1", ORIGIN, 0.0, 500), network(driven))
        assert find(report, "Measured Street").surveyed_m == pytest.approx(500, abs=25)

    def test_a_gap_between_frames_is_not_counted_as_driven(self):
        """A signal loss or a stopped app is a break in coverage. Bridging it would claim
        to have inspected road nobody drove past."""
        driven = street(1, "Gappy Street", ORIGIN, 0.0, 1000)
        near = drive("s1", ORIGIN, 0.0, 100)
        far_start = destination_point(ORIGIN, 0.0, 100 + MAX_FRAME_GAP_M * 3)
        far = drive("s1", far_start, 0.0, 100, first_index=500)

        report = build_street_report([], near + far, network(driven))
        # ~200 m of real driving, and the ~180 m gap must not be added to it.
        assert find(report, "Gappy Street").surveyed_m == pytest.approx(200, abs=30)

    def test_two_surveys_of_one_street_both_count(self):
        driven = street(1, "Twice Street", ORIGIN, 0.0, 400)
        report = build_street_report(
            [],
            drive("s1", ORIGIN, 0.0, 400) + drive("s2", ORIGIN, 0.0, 400),
            network(driven),
        )
        assert find(report, "Twice Street").surveyed_m == pytest.approx(800, abs=50)

    def test_driving_off_the_network_is_reported_not_dropped(self):
        driven = street(1, "Real Street", ORIGIN, 0.0, 400)
        nowhere = destination_point(ORIGIN, 45.0, 5000.0)
        report = build_street_report([], drive("s1", nowhere, 0.0, 300), network(driven))
        assert report.unmatched_m > 200


class TestCoverageCannotBeInflated:
    """Coverage answers "how much of this network have we inspected?".

    Two ways of driving a lot without inspecting any more of it must not move that
    number. Both were live bugs: coverage divided *total driven distance* by network
    length, so either one could report a city as surveyed on the strength of a drive
    that never touched most of it.
    """

    def test_driving_off_the_network_covers_none_of_it(self):
        driven = street(1, "Real Street", ORIGIN, 0.0, 400)
        nowhere = destination_point(ORIGIN, 45.0, 5000.0)

        report = build_street_report([], drive("s1", nowhere, 0.0, 2000), network(driven))

        assert report.unmatched_m > 1500, "the driving happened and is reported"
        assert report.on_network_m == 0.0
        assert report.coverage_fraction == 0.0, "5 km away is not coverage of this street"

    def test_driving_one_street_twice_does_not_cover_it_twice(self):
        driven = street(1, "Twice Street", ORIGIN, 0.0, 400)
        other = street(2, "Untouched Street", destination_point(ORIGIN, 45.0, 3000.0), 0.0, 400)
        frames = drive("s1", ORIGIN, 0.0, 400) + drive("s2", ORIGIN, 0.0, 400)

        report = build_street_report([], frames, network(driven, other))
        row = find(report, "Twice Street")

        assert row.surveyed_m == pytest.approx(800, abs=50), "800 m was genuinely driven"
        assert row.covered_m == pytest.approx(400, abs=1), "of one 400 m street"
        # Half the network inspected, not all of it — and never more than all of it.
        assert report.coverage_fraction == pytest.approx(0.5, abs=0.05)

    def test_a_row_reports_the_length_of_the_street_it_is_on(self):
        """ "400 m of 1.2 km driven" is a different sentence from "400 m driven"."""
        driven = street(1, "Long Street", ORIGIN, 0.0, 1200)
        report = build_street_report([], drive("s1", ORIGIN, 0.0, 400), network(driven))
        row = find(report, "Long Street")

        assert row.length_m == pytest.approx(1200, abs=5)
        assert row.covered_m == pytest.approx(400, abs=25)
        assert row.to_json()["length_m"] == pytest.approx(1200, abs=5)


class TestDensity:
    def test_is_none_below_the_minimum_sample(self):
        """Two defects in 8 m is not 25 per 100 m; it is too small a sample to divide
        by."""
        short = street(1, "Short Street", ORIGIN, 0.0, 30)
        report = build_street_report(
            [defect("d1", ORIGIN)],
            drive("s1", ORIGIN, 0.0, 30, spacing_m=3.0),
            network(short),
        )
        row = find(report, "Short Street")
        assert row.surveyed_m < MIN_LENGTH_FOR_DENSITY_M
        assert row.defects_per_100m is None

    def test_is_computed_over_the_driven_length(self):
        driven = street(1, "Dense Street", ORIGIN, 0.0, 400)
        defects = [defect(f"d{i}", destination_point(ORIGIN, 0.0, 40 * i)) for i in range(1, 9)]
        report = build_street_report(defects, drive("s1", ORIGIN, 0.0, 400), network(driven))
        row = find(report, "Dense Street")
        assert row.outstanding == 8
        assert row.defects_per_100m == pytest.approx(2.0, abs=0.3)

    def test_rejected_defects_do_not_count_toward_the_work(self):
        """A human said those are not there. Counting them again throws away the only
        judgement in the system that is not a guess."""
        driven = street(1, "Checked Street", ORIGIN, 0.0, 400)
        defects = [
            defect("d1", destination_point(ORIGIN, 0.0, 100)),
            defect("d2", destination_point(ORIGIN, 0.0, 200), status=DefectStatus.VERIFIED),
            defect("d3", destination_point(ORIGIN, 0.0, 300), status=DefectStatus.REJECTED),
        ]
        report = build_street_report(defects, drive("s1", ORIGIN, 0.0, 400), network(driven))
        row = find(report, "Checked Street")

        assert row.probable == 1
        assert row.verified == 1
        assert row.rejected == 1
        assert row.outstanding == 2, "rejected must not be in the number a crew is sent to"
        assert sum(row.by_class.values()) == 2, "a rejected defect is not a defect by class"

    def test_worst_only_ranks_stretches_long_enough_to_rate(self):
        """Ranking a 10 m sample against a 2 km one puts noise at the top of a work
        plan."""
        long_street = street(1, "Long Street", ORIGIN, 0.0, 400)
        tiny_at = destination_point(ORIGIN, 90.0, 2000.0)
        tiny = street(2, "Tiny Street", tiny_at, 0.0, 20)

        report = build_street_report(
            [
                defect("d1", destination_point(ORIGIN, 0.0, 100)),
                defect("d2", tiny_at),
            ],
            drive("s1", ORIGIN, 0.0, 400) + drive("s2", tiny_at, 0.0, 20, spacing_m=2.0),
            network(long_street, tiny),
        )
        assert [s.name for s in report.worst()] == ["Long Street"]


class TestAggregationUnit:
    def test_rolls_up_per_way_not_per_segment(self):
        """A way is stored as many short segments. Reporting each one separately would
        turn one street into eight rows of two defects."""
        one = street(1, "One Way Street", ORIGIN, 0.0, 400, pieces=8)
        defects = [defect(f"d{i}", destination_point(ORIGIN, 0.0, 45 * i)) for i in range(1, 8)]
        report = build_street_report(defects, drive("s1", ORIGIN, 0.0, 400), network(one))
        assert len(report.streets) == 1
        assert report.streets[0].outstanding == 7

    def test_by_name_merges_ways_of_one_street(self):
        first = street(1, "Split Street", ORIGIN, 0.0, 300)
        second_start = destination_point(ORIGIN, 0.0, 300)
        second = street(2, "Split Street", second_start, 0.0, 300)

        report = build_street_report(
            [
                defect("d1", destination_point(ORIGIN, 0.0, 100)),
                defect("d2", destination_point(ORIGIN, 0.0, 400)),
            ],
            drive("s1", ORIGIN, 0.0, 600),
            network(first, second),
        )
        assert len(report.streets) == 2, "the way is still the identifier"
        merged = report.by_name()["Split Street"]
        assert merged.outstanding == 2
        assert merged.surveyed_m == pytest.approx(600, abs=60)

    def test_an_existing_match_is_reused(self):
        """A database that has already had `roadeye match-roads` run on it must not be
        re-matched to a different answer."""
        from roadeye.domain.models import RoadSegmentRef

        one = street(1, "Matched Street", ORIGIN, 0.0, 400)
        already = defect("d1", destination_point(ORIGIN, 0.0, 100)).model_copy(
            update={"road": RoadSegmentRef(source="osm", segment_id="way/1#2")}
        )
        report = build_street_report([already], drive("s1", ORIGIN, 0.0, 400), network(one))
        assert find(report, "Matched Street").outstanding == 1


class TestNothingVanishes:
    def test_a_defect_matching_no_street_is_counted(self):
        """A rollup silently missing a tenth of the defects is a rollup nobody should
        budget from."""
        one = street(1, "Real Street", ORIGIN, 0.0, 400)
        far_away = destination_point(ORIGIN, 45.0, 4000.0)
        report = build_street_report(
            [defect("d1", destination_point(ORIGIN, 0.0, 100)), defect("d2", far_away)],
            drive("s1", ORIGIN, 0.0, 400),
            network(one),
        )
        assert find(report, "Real Street").outstanding == 1
        assert report.unmatched_defects == 1

    def test_the_json_carries_the_notice_about_coverage(self):
        one = street(1, "Real Street", ORIGIN, 0.0, 400)
        payload = build_street_report([], drive("s1", ORIGIN, 0.0, 400), network(one)).to_json()
        assert "never driven" in payload["notice"]
        assert payload["coverage_fraction"] is not None
