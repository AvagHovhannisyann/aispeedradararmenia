"""Timestamp synchronisation and GPS interpolation tests.

The failure mode this module guards against is silent: a broken time base produces
defects that all look plausible and are all in the wrong place. So these tests are
deliberately paranoid about the malformed-input paths as well as the happy one.
"""

from __future__ import annotations

import math

import pytest

from roadeye.domain.enums import LocationMethod
from roadeye.geolocation.timesync import (
    LocationSample,
    LocationTrack,
    video_time_to_epoch_ms,
)

T0 = 1_787_049_731_000
LAT, LON = 40.18231, 44.51491


def sample(offset_s: float, *, lat_off: float = 0.0, lon_off: float = 0.0, **kw) -> LocationSample:
    return LocationSample(
        t_epoch_ms=T0 + int(offset_s * 1000),
        lat=LAT + lat_off,
        lon=LON + lon_off,
        **kw,
    )


class TestVideoTimeConversion:
    def test_zero_offset(self):
        assert video_time_to_epoch_ms(T0, 0.0) == T0

    def test_known_offset(self):
        """The example from the design brief: 42.733 s into the video."""
        assert video_time_to_epoch_ms(T0, 42.733) == T0 + 42_733

    def test_sub_millisecond_rounds(self):
        assert video_time_to_epoch_ms(T0, 0.0004) == T0

    def test_rejects_negative(self):
        with pytest.raises(ValueError):
            video_time_to_epoch_ms(T0, -0.1)


class TestTrackCleaning:
    def test_sorts_out_of_order(self):
        track = LocationTrack.from_samples([sample(2), sample(0), sample(1)])
        assert [s.t_epoch_ms for s in track.samples] == [T0, T0 + 1000, T0 + 2000]
        assert track.stats.reordered is True

    def test_drops_nan_coordinates(self):
        bad = LocationSample(t_epoch_ms=T0, lat=float("nan"), lon=LON)
        track = LocationTrack.from_samples([bad, sample(1)])
        assert len(track) == 1
        assert track.stats.dropped_malformed == 1

    def test_drops_out_of_range_coordinates(self):
        bad = LocationSample(t_epoch_ms=T0, lat=999.0, lon=LON)
        track = LocationTrack.from_samples([bad, sample(1)])
        assert len(track) == 1
        assert track.stats.dropped_out_of_range == 1

    def test_drops_low_accuracy_fixes(self):
        track = LocationTrack.from_samples(
            [sample(0, accuracy_m=5.0), sample(1, accuracy_m=80.0)], max_accuracy_m=25.0
        )
        assert len(track) == 1
        assert track.stats.dropped_low_accuracy == 1

    def test_accuracy_filter_can_be_disabled(self):
        track = LocationTrack.from_samples(
            [sample(0, accuracy_m=5.0), sample(1, accuracy_m=80.0)], max_accuracy_m=None
        )
        assert len(track) == 2

    def test_deduplicates_keeping_most_accurate(self):
        track = LocationTrack.from_samples(
            [sample(0, accuracy_m=20.0), sample(0, accuracy_m=3.0)]
        )
        assert len(track) == 1
        assert track.samples[0].accuracy_m == 3.0
        assert track.stats.deduplicated == 1

    def test_empty_input_does_not_raise(self):
        """A survey with no GPS must degrade, not explode."""
        track = LocationTrack.from_samples([])
        assert len(track) == 0
        assert not track
        assert track.locate(T0) is None
        assert any("No usable GPS" in i for i in track.stats.issues)

    def test_stats_are_reported(self):
        track = LocationTrack.from_samples(
            [sample(0, accuracy_m=5.0), sample(1, accuracy_m=99.0), sample(2, accuracy_m=5.0)]
        )
        assert track.stats.total_input == 3
        assert track.stats.kept == 2
        assert track.stats.as_dict()["dropped_low_accuracy"] == 1


class TestInterpolation:
    def test_exact_midpoint(self):
        track = LocationTrack.from_samples(
            [sample(0, accuracy_m=5.0), sample(2, lon_off=0.000236, accuracy_m=5.0)]
        )
        loc = track.locate(T0 + 1000)
        assert loc is not None
        assert loc.position.lon == pytest.approx(LON + 0.000118, abs=1e-9)
        assert loc.method is LocationMethod.INTERPOLATED_PHONE_GPS
        assert loc.is_trustworthy

    def test_at_a_known_fix_returns_that_fix(self):
        track = LocationTrack.from_samples(
            [sample(0), sample(1, lon_off=0.000118), sample(2, lon_off=0.000236)]
        )
        loc = track.locate(T0 + 1000)
        assert loc.position.lon == pytest.approx(LON + 0.000118, abs=1e-9)

    def test_before_track_is_clamped_and_flagged(self):
        track = LocationTrack.from_samples([sample(10), sample(11, lon_off=0.000118)])
        loc = track.locate(T0)
        assert loc.method is LocationMethod.PHONE_GPS
        assert not loc.is_trustworthy
        assert "before first GPS fix" in loc.warning

    def test_after_track_is_clamped_and_flagged(self):
        track = LocationTrack.from_samples([sample(0), sample(1, lon_off=0.000118)])
        loc = track.locate(T0 + 30_000)
        assert not loc.is_trustworthy
        assert "after last GPS fix" in loc.warning

    def test_clamped_uncertainty_grows_with_drift(self):
        """Being 30 s past the last fix must be reported as far worse than 1 s past."""
        track = LocationTrack.from_samples([sample(0, speed_mps=10.0), sample(1, lon_off=0.000118, speed_mps=10.0)])
        near = track.locate(T0 + 1500)
        far = track.locate(T0 + 31_000)
        assert far.uncertainty_m > near.uncertainty_m * 5

    def test_large_gap_is_flagged_not_hidden(self):
        """Interpolating across a 40 s outage draws a confident line through buildings.
        We must return the estimate but mark it untrustworthy."""
        track = LocationTrack.from_samples([sample(0), sample(40, lon_off=0.005)])
        loc = track.locate(T0 + 20_000)
        assert loc is not None
        assert not loc.is_trustworthy
        assert "exceeds maximum" in loc.warning

    def test_gap_threshold_is_configurable(self):
        track = LocationTrack.from_samples([sample(0), sample(40, lon_off=0.005)], max_gap_s=60.0)
        assert track.locate(T0 + 20_000).is_trustworthy


class TestUncertainty:
    def test_reflects_fix_accuracy(self):
        good = LocationTrack.from_samples([sample(0, accuracy_m=3.0), sample(1, lon_off=1e-5, accuracy_m=3.0)])
        poor = LocationTrack.from_samples([sample(0, accuracy_m=20.0), sample(1, lon_off=1e-5, accuracy_m=20.0)])
        assert poor.locate(T0 + 500).uncertainty_m > good.locate(T0 + 500).uncertainty_m

    def test_peaks_between_fixes(self):
        """Interpolation error is zero at each anchor and worst in the middle."""
        track = LocationTrack.from_samples(
            [sample(0, accuracy_m=5.0), sample(4, lon_off=0.0005, accuracy_m=5.0)]
        )
        at_start = track.locate(T0 + 1)
        at_middle = track.locate(T0 + 2000)
        assert at_middle.uncertainty_m > at_start.uncertainty_m

    def test_never_negative(self):
        track = LocationTrack.from_samples([sample(0, accuracy_m=0.0), sample(1, accuracy_m=0.0)])
        assert track.locate(T0 + 500).uncertainty_m >= 0.0


class TestHeadingAndSpeed:
    def test_reported_heading_is_interpolated_circularly(self):
        track = LocationTrack.from_samples(
            [sample(0, heading_deg=350.0), sample(2, lon_off=1e-5, heading_deg=10.0)]
        )
        assert track.locate(T0 + 1000).heading_deg == pytest.approx(0.0, abs=1e-6)

    def test_falls_back_to_bearing_when_moving(self):
        track = LocationTrack.from_samples([sample(0), sample(1, lon_off=0.000118)])
        heading = track.locate(T0 + 500).heading_deg
        assert heading == pytest.approx(90.0, abs=1.0)

    def test_no_heading_when_stationary(self):
        """Two near-identical fixes carry no direction information — only GPS noise.
        Reporting a bearing from them would be inventing data."""
        track = LocationTrack.from_samples([sample(0), sample(1)])
        assert track.locate(T0 + 500).heading_deg is None

    def test_speed_derived_when_absent(self):
        track = LocationTrack.from_samples([sample(0), sample(1, lon_off=0.000118)])
        assert track.locate(T0 + 500).speed_mps == pytest.approx(10.0, abs=0.5)

    def test_reported_speed_preferred(self):
        track = LocationTrack.from_samples(
            [sample(0, speed_mps=8.0), sample(2, lon_off=0.000236, speed_mps=12.0)]
        )
        assert track.locate(T0 + 1000).speed_mps == pytest.approx(10.0)


class TestDistance:
    def test_total_distance(self):
        samples = [sample(i, lon_off=i * 0.000118) for i in range(11)]
        track = LocationTrack.from_samples(samples)
        assert track.total_distance_m() == pytest.approx(100.0, abs=1.0)

    def test_single_sample_has_no_distance(self):
        assert LocationTrack.from_samples([sample(0)]).total_distance_m() == 0.0

    def test_start_and_end(self):
        track = LocationTrack.from_samples([sample(0), sample(5)])
        assert track.start_epoch_ms == T0
        assert track.end_epoch_ms == T0 + 5000
        assert not math.isnan(track.total_distance_m())
