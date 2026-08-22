"""Temporal tracking tests.

The core requirement: one pothole approaching the camera across N frames must become
ONE track, not N. Everything else here guards the ways that can go wrong.
"""

from __future__ import annotations

from roadeye.domain.enums import DamageClass
from roadeye.domain.models import BoundingBox, Detection
from roadeye.tracking.tracker import GreedyIouTracker, TrackingConfig

SURVEY = "s1"
T0 = 1_787_049_731_000


def detection(
    idx: int,
    x1: float,
    y1: float,
    size: float = 100.0,
    *,
    damage_class: DamageClass = DamageClass.POTHOLE,
    confidence: float = 0.9,
) -> Detection:
    return Detection(
        detection_id=f"det{idx:04d}",
        frame_id=f"f{idx:04d}",
        survey_id=SURVEY,
        damage_class=damage_class,
        confidence=confidence,
        bbox=BoundingBox(x1=x1, y1=y1, x2=x1 + size, y2=y1 + size),
        model_id="test",
    )


class TestApproachingDefect:
    def test_one_pothole_across_frames_is_one_track(self):
        """The headline case: a defect drifting down the frame as the car approaches.

        Without this, a dashboard shows 6 potholes where the road has 1.
        """
        tracker = GreedyIouTracker()
        for i in range(6):
            det = detection(i, 500.0 + i * 12, 600.0 + i * 25, size=100 + i * 12)
            tracker.update(SURVEY, det.frame_id, T0 + i * 400, [det])
        tracks = tracker.finish(SURVEY)
        assert len(tracks) == 1
        assert tracks[0].observation_count == 6

    def test_detections_are_tagged_with_their_track(self):
        """Provenance: a defect must be walkable back to individual frames."""
        tracker = GreedyIouTracker()
        dets = []
        for i in range(3):
            det = detection(i, 500.0 + i * 10, 600.0 + i * 10)
            dets.append(det)
            tracker.update(SURVEY, det.frame_id, T0 + i * 400, [det])
        tracker.finish(SURVEY)
        assert all(d.track_id is not None for d in dets)
        assert len({d.track_id for d in dets}) == 1

    def test_two_separate_defects_are_two_tracks(self):
        tracker = GreedyIouTracker()
        for i in range(4):
            left = detection(i * 2, 100.0 + i * 5, 600.0)
            right = detection(i * 2 + 1, 1400.0 + i * 5, 600.0)
            tracker.update(SURVEY, f"f{i}", T0 + i * 400, [left, right])
        assert len(tracker.finish(SURVEY)) == 2


class TestAssociationRules:
    def test_no_overlap_starts_a_new_track(self):
        tracker = GreedyIouTracker()
        a = detection(0, 100.0, 100.0)
        tracker.update(SURVEY, "f0", T0, [a])
        b = detection(1, 1500.0, 900.0)
        tracker.update(SURVEY, "f1", T0 + 400, [b])
        assert len(tracker.finish(SURVEY)) == 2

    def test_different_classes_do_not_associate(self):
        """A pothole must not absorb a crack detection just because boxes overlap."""
        tracker = GreedyIouTracker()
        tracker.update(SURVEY, "f0", T0, [detection(0, 500.0, 600.0)])
        tracker.update(
            SURVEY,
            "f1",
            T0 + 400,
            [detection(1, 505.0, 605.0, damage_class=DamageClass.ALLIGATOR_CRACK)],
        )
        tracks = tracker.finish(SURVEY)
        assert len(tracks) == 2
        assert {t.damage_class for t in tracks} == {
            DamageClass.POTHOLE,
            DamageClass.ALLIGATOR_CRACK,
        }

    def test_time_gap_closes_a_track(self):
        """Seeing a similar box 30 s later is a different defect, not the same one."""
        tracker = GreedyIouTracker(TrackingConfig(max_gap_s=2.0))
        tracker.update(SURVEY, "f0", T0, [detection(0, 500.0, 600.0)])
        tracker.update(SURVEY, "f1", T0 + 30_000, [detection(1, 500.0, 600.0)])
        assert len(tracker.finish(SURVEY)) == 2

    def test_greedy_matching_does_not_reuse_a_track(self):
        """Two overlapping detections in one frame must not both join one track."""
        tracker = GreedyIouTracker()
        tracker.update(SURVEY, "f0", T0, [detection(0, 500.0, 600.0)])
        tracker.update(
            SURVEY, "f1", T0 + 400, [detection(1, 505.0, 605.0), detection(2, 510.0, 610.0)]
        )
        tracks = tracker.finish(SURVEY)
        assert len(tracks) == 2
        assert sum(t.observation_count for t in tracks) == 3

    def test_brief_miss_is_tolerated(self):
        """A single missed frame (occlusion) must not split one defect in two."""
        tracker = GreedyIouTracker(TrackingConfig(max_missed_frames=2))
        tracker.update(SURVEY, "f0", T0, [detection(0, 500.0, 600.0)])
        tracker.update(SURVEY, "f1", T0 + 400, [])
        tracker.update(SURVEY, "f2", T0 + 800, [detection(2, 505.0, 605.0)])
        assert len(tracker.finish(SURVEY)) == 1


class TestTrackMetadata:
    def test_confidence_aggregates(self):
        tracker = GreedyIouTracker()
        tracker.update(SURVEY, "f0", T0, [detection(0, 500.0, 600.0, confidence=0.5)])
        tracker.update(SURVEY, "f1", T0 + 400, [detection(1, 505.0, 605.0, confidence=0.9)])
        track = tracker.finish(SURVEY)[0]
        assert track.max_confidence == 0.9
        assert track.mean_confidence == 0.7

    def test_time_bounds(self):
        tracker = GreedyIouTracker()
        tracker.update(SURVEY, "f0", T0, [detection(0, 500.0, 600.0)])
        tracker.update(SURVEY, "f1", T0 + 1200, [detection(1, 505.0, 605.0)])
        track = tracker.finish(SURVEY)[0]
        assert track.first_t_epoch_ms == T0
        assert track.last_t_epoch_ms == T0 + 1200
        assert track.first_frame_id == "f0"
        assert track.last_frame_id == "f1"

    def test_min_track_length_filters_noise(self):
        tracker = GreedyIouTracker(TrackingConfig(min_track_length=3))
        tracker.update(SURVEY, "f0", T0, [detection(0, 100.0, 100.0)])
        tracker.update(SURVEY, "f1", T0 + 400, [detection(1, 1500.0, 900.0)])
        assert tracker.finish(SURVEY) == []

    def test_tracks_are_returned_in_time_order(self):
        tracker = GreedyIouTracker()
        tracker.update(SURVEY, "f0", T0, [detection(0, 100.0, 100.0)])
        tracker.update(SURVEY, "f1", T0 + 5000, [detection(1, 1500.0, 900.0)])
        tracks = tracker.finish(SURVEY)
        assert tracks[0].first_t_epoch_ms <= tracks[1].first_t_epoch_ms

    def test_empty_survey_produces_no_tracks(self):
        """A clean road is a legitimate result, not an error."""
        tracker = GreedyIouTracker()
        for i in range(5):
            tracker.update(SURVEY, f"f{i}", T0 + i * 400, [])
        assert tracker.finish(SURVEY) == []


class TestBoundingBoxIou:
    def test_identical_boxes(self):
        box = BoundingBox(x1=0, y1=0, x2=10, y2=10)
        assert box.iou(box) == 1.0

    def test_disjoint_boxes(self):
        a = BoundingBox(x1=0, y1=0, x2=10, y2=10)
        b = BoundingBox(x1=100, y1=100, x2=110, y2=110)
        assert a.iou(b) == 0.0

    def test_half_overlap(self):
        a = BoundingBox(x1=0, y1=0, x2=10, y2=10)
        b = BoundingBox(x1=5, y1=0, x2=15, y2=10)
        # intersection 50, union 150
        assert a.iou(b) == 50 / 150

    def test_ground_contact_is_bottom_centre(self):
        """Ray-to-ground projection needs the near rim, not the box centre."""
        box = BoundingBox(x1=100, y1=200, x2=300, y2=400)
        assert box.ground_contact == (200.0, 400.0)
        assert box.center == (200.0, 300.0)
