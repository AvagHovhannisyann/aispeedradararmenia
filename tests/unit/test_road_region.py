"""Tests for the in-frame region where a road defect may be reported.

The failure modes are asymmetric and both matter:

* **Too loose** and the filter does nothing — the reviewer still opens every shadow on
  every wall, which is the cost it was built to cut.
* **Too tight** and it deletes real potholes, and the run looks like a clean street.

The second is far worse, and it is invisible: a rejected detection leaves no trace on a
map. So the region is off by default, its rejections are counted, and an empty region is
refused at construction rather than discovered in a report.
"""

from __future__ import annotations

import pytest

from roadeye.vision.road_region import WINDSCREEN_MOUNT, RoadRegion

W, H = 1920, 1080


def box(cx: float, bottom: float, *, size: float = 0.04, width: int = W, height: int = H):
    """A box in pixels whose bottom-centre sits at normalised ``(cx, bottom)``."""
    half = size * width / 2
    return (cx * width - half, bottom * height - size * height, cx * width + half, bottom * height)


class TestTheShapeItselfIsRefusedIfUseless:
    """A region that rejects everything looks exactly like a detector that found
    nothing, and nothing downstream can tell them apart."""

    def test_a_horizon_below_the_bonnet_is_refused(self):
        with pytest.raises(ValueError, match="reject every detection"):
            RoadRegion(horizon=0.96, bonnet=0.05)

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"horizon": -0.1},
            {"horizon": 1.4},
            {"bonnet": 2.0},
            {"near_left": -0.5},
        ],
    )
    def test_a_coordinate_outside_the_frame_is_refused(self, kwargs):
        with pytest.raises(ValueError, match="fraction of frame size"):
            RoadRegion(**kwargs)

    @pytest.mark.parametrize("kwargs", [{"near_left": 0.8, "near_right": 0.2}, {"far_left": 0.9}])
    def test_an_inverted_edge_is_refused(self, kwargs):
        with pytest.raises(ValueError, match="must be left of"):
            RoadRegion(**kwargs)


class TestWhatItKeepsOut:
    def test_the_sky_is_not_a_road(self):
        assert not WINDSCREEN_MOUNT.contains(0.5, 0.10)

    def test_the_bonnet_is_not_a_road(self):
        """The camera sees the car it is mounted in. A 'defect' there is a reflection."""
        assert not WINDSCREEN_MOUNT.contains(0.5, 0.99)

    def test_a_wall_beside_the_road_is_not_a_road(self):
        """Far from the centre and high in the frame is a building, not tarmac — this is
        the case that produces the shadow-shaped false positives."""
        assert not WINDSCREEN_MOUNT.contains(0.05, 0.50)
        assert not WINDSCREEN_MOUNT.contains(0.95, 0.50)


class TestWhatItLetsThrough:
    def test_the_road_straight_ahead(self):
        assert WINDSCREEN_MOUNT.contains(0.50, 0.80)
        assert WINDSCREEN_MOUNT.contains(0.50, 0.50)

    def test_the_near_edges_widen_with_perspective(self):
        """Close to the vehicle the road fills the frame; far away it narrows to a
        point. A region that did not widen would drop every defect beside the wheels."""
        assert WINDSCREEN_MOUNT.contains(0.10, 0.90), "near left is still road"
        assert not WINDSCREEN_MOUNT.contains(0.10, 0.47), "the same x at the horizon is not"


class TestBoxesAreTestedWhereTheyMeetTheGround:
    def test_a_pothole_low_in_the_frame_is_accepted(self):
        assert WINDSCREEN_MOUNT.accepts(*box(0.5, 0.85), W, H)

    def test_a_long_crack_running_away_from_the_camera_is_kept(self):
        """The case where base and centre genuinely disagree, and the reason the base
        wins.

        A longitudinal crack photographed from a dashcam is a tall, thin box: its far
        end is up near the horizon, its near end is close to the bumper. That box's
        *centre* lands above the horizon and a centre-based test throws the crack away —
        a real defect, of the most common class, deleted for being long.

        The assertion below pins both halves, because a test that agrees under either
        rule proves nothing about which rule is in use. An earlier version of this test
        used a van at the kerb, which both rules reject, and it survived the mutation
        that swapped them.
        """
        far, near = 0.25 * H, 0.60 * H
        crack = (0.48 * W, far, 0.52 * W, near)

        assert WINDSCREEN_MOUNT.accepts(*crack, W, H), "judged at its base: on the road"
        assert not WINDSCREEN_MOUNT.contains(0.5, (far + near) / 2 / H), (
            "and its centre is above the horizon — the two rules disagree here, "
            "which is what makes this case worth testing"
        )

    def test_a_van_at_the_kerb_is_rejected(self):
        """Not a discriminating case for base-versus-centre — both reject it — but it
        pins the thing the region is for: objects off the carriageway do not count."""
        assert not WINDSCREEN_MOUNT.accepts(0.02 * W, 0.50 * H, 0.20 * W, 0.62 * H, W, H)

    def test_an_unknown_frame_size_is_never_used_to_reject(self):
        """Refusing to guess. A frame with no recorded dimensions cannot be judged, and
        a filter that treats 'I don't know' as 'not road' deletes real defects."""
        assert WINDSCREEN_MOUNT.accepts(0, 0, 10, 10, 0, 0)
        assert WINDSCREEN_MOUNT.accepts(0, 0, 10, 10, -1, 100)

    @pytest.mark.parametrize("width,height", [(1920, 1080), (1280, 720), (3840, 2160)])
    @pytest.mark.parametrize("spot,expected", [((0.5, 0.85), True), ((0.5, 0.10), False)])
    def test_the_region_is_resolution_independent(self, width, height, spot, expected):
        """Normalised coordinates, so one region measured on the first drive survives a
        phone upgrade. The same place in the scene must give the same verdict whatever
        the sensor resolution."""
        cx, bottom = spot
        pixels = box(cx, bottom, width=width, height=height)

        assert WINDSCREEN_MOUNT.accepts(*pixels, width, height) is expected


class TestProvenance:
    def test_the_region_serialises_into_the_run_config(self):
        """Every tunable threshold is recorded in ProcessingRun.config, so a run is
        reproducible from its own record. A filter that changed results without
        appearing there would make two runs silently incomparable."""
        payload = RoadRegion(horizon=0.4).as_dict()

        assert payload["horizon"] == 0.4
        assert set(payload) == {
            "horizon",
            "bonnet",
            "near_left",
            "near_right",
            "far_left",
            "far_right",
        }
