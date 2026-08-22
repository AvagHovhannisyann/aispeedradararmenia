"""Frame sampling tests.

The behaviour that matters most: sitting at a red light must not generate hundreds of
near-identical frames. That single property protects both the defect count and the
training set from being poisoned by duplicates.
"""

from __future__ import annotations

import pytest

from roadeye.domain.enums import SamplingMode
from roadeye.geolocation.timesync import LocationSample, LocationTrack
from roadeye.video.sampling import (
    SamplingConfig,
    build_sampling_plan,
    estimated_spacing_m,
)

T0 = 1_787_049_731_000
LAT, LON = 40.18231, 44.51491
LON_PER_10M = 0.000118


def moving_track(seconds: int = 60, speed_mps: float = 10.0) -> LocationTrack:
    """A steady drive due east."""
    metres_per_s = speed_mps
    return LocationTrack.from_samples(
        [
            LocationSample(
                t_epoch_ms=T0 + i * 1000,
                lat=LAT,
                lon=LON + (i * metres_per_s / 10.0) * LON_PER_10M,
                accuracy_m=5.0,
                speed_mps=speed_mps,
                heading_deg=90.0,
            )
            for i in range(seconds + 1)
        ]
    )


def stationary_track(seconds: int = 30) -> LocationTrack:
    """Stopped at a red light: position barely changes, reported speed ~0."""
    return LocationTrack.from_samples(
        [
            LocationSample(
                t_epoch_ms=T0 + i * 1000,
                lat=LAT,
                lon=LON,
                accuracy_m=5.0,
                speed_mps=0.0,
                heading_deg=90.0,
            )
            for i in range(seconds + 1)
        ]
    )


class TestFixedRateModes:
    def test_fixed_fps_count(self):
        plan = build_sampling_plan(
            duration_s=10.0,
            recording_start_epoch_ms=T0,
            config=SamplingConfig(mode=SamplingMode.FIXED_FPS, fallback_fps=5.0),
        )
        assert len(plan) == 51  # inclusive of t=0 and t=10
        assert plan.frames[1].video_time_s == pytest.approx(0.2)

    def test_fixed_interval(self):
        plan = build_sampling_plan(
            duration_s=10.0,
            recording_start_epoch_ms=T0,
            config=SamplingConfig(mode=SamplingMode.FIXED_INTERVAL, interval_s=2.0),
        )
        assert [f.video_time_s for f in plan.frames] == [0.0, 2.0, 4.0, 6.0, 8.0, 10.0]

    def test_absolute_times_are_anchored_to_recording_start(self):
        plan = build_sampling_plan(
            duration_s=2.0,
            recording_start_epoch_ms=T0,
            config=SamplingConfig(mode=SamplingMode.FIXED_INTERVAL, interval_s=1.0),
        )
        assert [f.t_epoch_ms for f in plan.frames] == [T0, T0 + 1000, T0 + 2000]

    def test_max_frames_truncates(self):
        plan = build_sampling_plan(
            duration_s=100.0,
            recording_start_epoch_ms=T0,
            config=SamplingConfig(mode=SamplingMode.FIXED_FPS, fallback_fps=10.0, max_frames=25),
        )
        assert len(plan) == 25

    def test_rejects_negative_duration(self):
        with pytest.raises(ValueError):
            build_sampling_plan(
                duration_s=-1.0, recording_start_epoch_ms=T0, config=SamplingConfig()
            )


class TestDistanceMode:
    def test_spacing_matches_target(self):
        track = moving_track(seconds=60, speed_mps=10.0)
        plan = build_sampling_plan(
            duration_s=60.0,
            recording_start_epoch_ms=T0,
            config=SamplingConfig(target_spacing_m=10.0, fallback_fps=10.0),
            track=track,
        )
        # 600 m at 10 m spacing.
        assert 55 <= len(plan) <= 65
        gaps = [f.distance_from_previous_m for f in plan.frames[1:]]
        assert all(g >= 9.0 for g in gaps)

    def test_slower_driving_yields_fewer_frames_per_second(self):
        """Distance sampling is the point: frame count should track distance, not time."""
        fast = build_sampling_plan(
            duration_s=60.0,
            recording_start_epoch_ms=T0,
            config=SamplingConfig(target_spacing_m=5.0, fallback_fps=10.0),
            track=moving_track(60, speed_mps=20.0),
        )
        slow = build_sampling_plan(
            duration_s=60.0,
            recording_start_epoch_ms=T0,
            config=SamplingConfig(target_spacing_m=5.0, fallback_fps=10.0),
            track=moving_track(60, speed_mps=5.0),
        )
        assert len(fast) > len(slow)

    def test_red_light_produces_almost_no_frames(self):
        """The headline property. A 30 s stop at 10 fps would otherwise yield 300
        near-identical images of the same tarmac."""
        plan = build_sampling_plan(
            duration_s=30.0,
            recording_start_epoch_ms=T0,
            config=SamplingConfig(target_spacing_m=2.5, fallback_fps=10.0),
            track=stationary_track(30),
        )
        assert len(plan) == 0
        assert any("stationary" in note for note in plan.notes)

    def test_min_speed_threshold_is_respected(self):
        plan = build_sampling_plan(
            duration_s=30.0,
            recording_start_epoch_ms=T0,
            config=SamplingConfig(target_spacing_m=2.5, fallback_fps=10.0, min_speed_mps=25.0),
            track=moving_track(30, speed_mps=10.0),
        )
        assert len(plan) == 0


class TestFallback:
    def test_falls_back_without_gps(self):
        plan = build_sampling_plan(
            duration_s=10.0,
            recording_start_epoch_ms=T0,
            config=SamplingConfig(mode=SamplingMode.DISTANCE, fallback_fps=2.0),
            track=None,
        )
        assert plan.fell_back
        assert plan.mode_used is SamplingMode.FIXED_FPS
        assert len(plan) == 21
        assert any("fell back" in note for note in plan.notes)

    def test_falls_back_with_single_fix(self):
        track = LocationTrack.from_samples(
            [LocationSample(t_epoch_ms=T0, lat=LAT, lon=LON, accuracy_m=5.0)]
        )
        plan = build_sampling_plan(
            duration_s=5.0,
            recording_start_epoch_ms=T0,
            config=SamplingConfig(mode=SamplingMode.DISTANCE, fallback_fps=2.0),
            track=track,
        )
        assert plan.fell_back

    def test_fallback_is_reported_not_silent(self):
        """Silently degrading to time-based sampling would make a survey's spacing
        inexplicable later."""
        plan = build_sampling_plan(
            duration_s=5.0,
            recording_start_epoch_ms=T0,
            config=SamplingConfig(mode=SamplingMode.DISTANCE),
            track=None,
        )
        assert plan.notes


class TestSpacingHelper:
    def test_brief_example(self):
        """50 km/h = 13.89 m/s; at 5 fps that is 2.78 m per frame."""
        assert estimated_spacing_m(13.89, 5.0) == pytest.approx(2.778, abs=0.01)

    def test_rejects_zero_fps(self):
        with pytest.raises(ValueError):
            estimated_spacing_m(10.0, 0.0)
