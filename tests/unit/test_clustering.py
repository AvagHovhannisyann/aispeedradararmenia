"""Clustering tests: detections -> defects.

This is where "20 frame detections become 1 pothole" is actually enforced, and where
the opposite failure — an entire street collapsing into one defect — is guarded
against. Both directions are load-bearing for the product, so both are tested.
"""

from __future__ import annotations

import datetime as dt

import pytest

from roadeye.clustering.geo import (
    ClusterCandidate,
    ClusteringConfig,
    build_defects,
    cluster_candidates,
)
from roadeye.domain.enums import DamageClass, DefectStatus, LocationMethod, Severity
from roadeye.domain.models import DefectObservation, GeoPoint
from roadeye.geolocation.geodesy import LatLon, destination_point

BASE = dt.datetime(2026, 8, 18, 10, 42, 11, tzinfo=dt.timezone.utc)
ORIGIN = LatLon(40.18231, 44.51491)


def candidate(
    name: str,
    *,
    metres_east: float = 0.0,
    seconds: float = 0.0,
    damage_class: DamageClass = DamageClass.POTHOLE,
    confidence: float = 0.9,
    uncertainty_m: float = 5.0,
    survey_id: str = "s1",
) -> ClusterCandidate:
    point = destination_point(ORIGIN, 90.0, metres_east) if metres_east else ORIGIN
    return ClusterCandidate(
        observation=DefectObservation(
            observation_id=name,
            defect_id="",
            survey_id=survey_id,
            observed_at=BASE + dt.timedelta(seconds=seconds),
            confidence=confidence,
            location=GeoPoint(
                lat=point.lat,
                lon=point.lon,
                method=LocationMethod.INTERPOLATED_PHONE_GPS,
                uncertainty_m=uncertainty_m,
            ),
            representative_frame_id=f"frame-{name}",
        ),
        damage_class=damage_class,
    )


class TestBasicClustering:
    def test_nearby_same_class_merge(self):
        clusters = cluster_candidates(
            [candidate("a"), candidate("b", metres_east=3.0, seconds=1)]
        )
        assert len(clusters) == 1
        assert len(clusters[0]) == 2

    def test_distant_observations_stay_separate(self):
        clusters = cluster_candidates(
            [candidate("a"), candidate("b", metres_east=200.0, seconds=20)]
        )
        assert len(clusters) == 2

    def test_different_classes_never_merge(self):
        """A pothole and a crack at the same spot are two different work items."""
        clusters = cluster_candidates(
            [
                candidate("a", damage_class=DamageClass.POTHOLE),
                candidate("b", metres_east=1.0, damage_class=DamageClass.ALLIGATOR_CRACK),
            ]
        )
        assert len(clusters) == 2

    def test_class_constraint_can_be_disabled(self):
        clusters = cluster_candidates(
            [
                candidate("a", damage_class=DamageClass.POTHOLE),
                candidate("b", metres_east=1.0, damage_class=DamageClass.ALLIGATOR_CRACK),
            ],
            ClusteringConfig(require_same_class=False),
        )
        assert len(clusters) == 1

    def test_empty_input(self):
        assert cluster_candidates([]) == []

    def test_deterministic(self):
        """Same input must always give the same clustering, or provenance is worthless."""
        cands = [candidate(f"c{i}", metres_east=i * 4.0, seconds=i) for i in range(8)]
        first = [[c.observation.observation_id for c in cl] for cl in cluster_candidates(cands)]
        second = [
            [c.observation.observation_id for c in cl]
            for cl in cluster_candidates(list(reversed(cands)))
        ]
        assert first == second


class TestChainingRegression:
    """Regression: single-link clustering must not merge an entire street.

    Found by the first end-to-end smoke run — 234 observations along a 600 m drive
    collapsed into ONE defect, because each was within the merge radius of its
    neighbour and single-link agglomeration chained them all together. A real row of
    potholes would have been reported as a single item, which silently destroys the
    core product claim that one defect equals one repairable thing.

    The fix is the ``max_cluster_extent_m`` cap.
    """

    def test_long_chain_does_not_become_one_defect(self):
        # 100 observations at 5 m spacing = a 500 m street.
        cands = [candidate(f"c{i:03d}", metres_east=i * 5.0, seconds=i) for i in range(100)]
        clusters = cluster_candidates(cands)
        assert len(clusters) > 10, (
            f"chaining regression: 500 m of observations collapsed into "
            f"{len(clusters)} cluster(s)"
        )

    def test_no_cluster_exceeds_the_extent_cap(self):
        config = ClusteringConfig(max_cluster_extent_m=15.0)
        cands = [candidate(f"c{i:03d}", metres_east=i * 3.0, seconds=i) for i in range(60)]
        for cluster in cluster_candidates(cands, config):
            positions = [
                LatLon(c.observation.location.lat, c.observation.location.lon) for c in cluster
            ]
            if len(positions) < 2:
                continue
            from roadeye.geolocation.geodesy import haversine_m

            spread = max(
                haversine_m(a, b) for a in positions for b in positions
            )
            # Diameter may reach twice the centroid radius in the worst case.
            assert spread <= config.max_cluster_extent_m * 2 + 1e-6

    def test_tight_group_still_merges_into_one(self):
        """The cap must not break the primary job: many views of ONE pothole."""
        cands = [
            candidate(f"c{i}", metres_east=i * 0.4, seconds=i * 0.2, uncertainty_m=4.0)
            for i in range(20)
        ]
        clusters = cluster_candidates(cands)
        assert len(clusters) == 1
        assert len(clusters[0]) == 20


class TestUncertaintyHandling:
    def test_uncertain_fixes_merge_more_readily(self):
        precise = cluster_candidates(
            [candidate("a", uncertainty_m=1.0), candidate("b", metres_east=20.0, uncertainty_m=1.0)]
        )
        vague = cluster_candidates(
            [candidate("a", uncertainty_m=12.0), candidate("b", metres_east=20.0, uncertainty_m=12.0)]
        )
        assert len(precise) == 2
        assert len(vague) == 1

    def test_scaling_can_be_disabled(self):
        clusters = cluster_candidates(
            [
                candidate("a", uncertainty_m=20.0),
                candidate("b", metres_east=20.0, uncertainty_m=20.0),
            ],
            ClusteringConfig(scale_radius_by_uncertainty=False, merge_radius_m=12.0),
        )
        assert len(clusters) == 2


class TestBuildDefects:
    def test_creates_one_defect_per_cluster(self):
        defects, observations = build_defects(
            [candidate("a"), candidate("b", metres_east=2.0, seconds=1)]
        )
        assert len(defects) == 1
        assert defects[0].observation_count == 2
        assert all(o.defect_id == defects[0].defect_id for o in observations)

    def test_confidence_is_the_maximum_not_the_mean(self):
        """Seeing a pothole clearly once and vaguely five times is evidence FOR it.
        A mean would punish the approach-and-pass geometry every survey produces."""
        defects, _ = build_defects(
            [
                candidate("a", confidence=0.35),
                candidate("b", metres_east=1.0, seconds=1, confidence=0.95),
                candidate("c", metres_east=2.0, seconds=2, confidence=0.40),
            ]
        )
        assert defects[0].confidence == pytest.approx(0.95)

    def test_machine_may_only_assert_probable(self):
        defects, _ = build_defects([candidate("a")])
        assert defects[0].status is DefectStatus.PROBABLE
        assert defects[0].severity is Severity.UNASSESSED

    def test_uncertainty_never_beats_the_best_single_fix(self):
        """Combining many correlated GPS fixes must not manufacture sub-metre
        precision. Errors from one receiver seconds apart are not independent."""
        cands = [
            candidate(f"c{i}", metres_east=i * 0.2, seconds=i * 0.1, uncertainty_m=5.0)
            for i in range(200)
        ]
        defects, _ = build_defects(cands)
        assert defects[0].location.uncertainty_m >= 5.0

    def test_representative_frame_is_the_most_confident_view(self):
        defects, _ = build_defects(
            [
                candidate("low", confidence=0.4),
                candidate("high", metres_east=1.0, seconds=1, confidence=0.99),
            ]
        )
        assert defects[0].representative_frame_id == "frame-high"

    def test_tracks_first_and_last_seen(self):
        defects, _ = build_defects(
            [candidate("a", seconds=0), candidate("b", metres_east=1.0, seconds=30)]
        )
        assert defects[0].first_seen == BASE
        assert defects[0].last_seen == BASE + dt.timedelta(seconds=30)

    def test_records_every_contributing_survey(self):
        """Cross-survey merging is what makes trend analysis possible at all."""
        defects, _ = build_defects(
            [
                candidate("a", survey_id="aug18", seconds=0),
                candidate("b", survey_id="sep08", metres_east=1.5, seconds=60),
            ]
        )
        assert defects[0].survey_ids == ["aug18", "sep08"]
        assert defects[0].observation_count == 2

    def test_defect_ids_are_prefixed_and_stable(self):
        defects, _ = build_defects(
            [candidate("a"), candidate("b", metres_east=100.0, seconds=10)],
            defect_id_prefix="survey42",
        )
        assert [d.defect_id for d in defects] == ["survey42_00001", "survey42_00002"]

    def test_provenance_is_attached(self):
        defects, _ = build_defects(
            [candidate("a")], model_id="m1", processing_run_id="run7"
        )
        assert defects[0].model_id == "m1"
        assert defects[0].processing_run_id == "run7"

    def test_empty_input(self):
        defects, observations = build_defects([])
        assert defects == []
        assert observations == []
