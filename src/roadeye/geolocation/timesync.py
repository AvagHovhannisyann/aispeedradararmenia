"""Timestamp synchronisation: video time -> absolute time -> interpolated position.

This is the least glamorous and most load-bearing subsystem in RoadEye. A perfect
pothole detector attached to a broken time base produces a useless municipal map, and
the failure is silent — every defect lands somewhere plausible but wrong.

The chain is:

    video_time_seconds  (detector says "pothole at t=42.733s of video.mp4")
        + recording_start_epoch_ms                      -> absolute observation time
        -> bracketing GPS fixes                         -> interpolated position

Design rules encoded here:

* **File mtime is never the time base.** It reflects when the file finished writing,
  which is the *end* of the recording, and is mangled by any file copy. The collector
  must record an explicit ``recording_start_epoch_ms``.
* **A malformed GPS sample must never kill a survey.** Bad samples are dropped and
  counted, not raised.
* **Gaps are flagged, not silently bridged.** Interpolating across a 40-second GPS
  outage produces a confident straight line through buildings. We return the estimate
  *and* say it is untrustworthy, and let the caller decide.
* **Uncertainty is always returned.** A position without an uncertainty invites false
  precision.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from roadeye.domain.enums import LocationMethod
from roadeye.geolocation.geodesy import (
    LatLon,
    haversine_m,
    initial_bearing_deg,
    interpolate_bearing,
    interpolate_position,
)

#: Beyond this gap between bracketing fixes, an interpolated position is reported as
#: untrustworthy. At 50 km/h a vehicle covers ~139 m in 10 s, which is far more than a
#: municipal work order can tolerate.
DEFAULT_MAX_GAP_S = 10.0

#: Fixes worse than this are dropped before interpolation. Consumer GPS in an urban
#: canyon regularly reports 30-50 m; such fixes are worse than no fix for our purposes.
DEFAULT_MAX_ACCURACY_M = 25.0


@dataclass(frozen=True, slots=True)
class LocationSample:
    """One raw GPS fix as logged by the collector.

    ``t_epoch_ms`` is the *device* clock at the moment of the fix. All RoadEye time
    arithmetic happens in this single frame of reference.
    """

    t_epoch_ms: int
    lat: float
    lon: float
    accuracy_m: float | None = None
    speed_mps: float | None = None
    heading_deg: float | None = None
    altitude_m: float | None = None

    @property
    def position(self) -> LatLon:
        return LatLon(self.lat, self.lon)


@dataclass(frozen=True, slots=True)
class InterpolatedLocation:
    """A position estimate for an arbitrary instant, with honest error reporting."""

    t_epoch_ms: int
    position: LatLon
    method: LocationMethod
    uncertainty_m: float
    speed_mps: float | None = None
    heading_deg: float | None = None
    #: Seconds between the two fixes used. Large values mean a weak estimate.
    gap_s: float = 0.0
    #: True when the gap exceeded the configured maximum, or the instant fell outside
    #: the track and was clamped. Such positions must not drive municipal action.
    is_trustworthy: bool = True
    #: Human-readable reason when ``is_trustworthy`` is False.
    warning: str | None = None


@dataclass
class TrackStats:
    """What the loader had to throw away. Surfaced in the processing-run summary."""

    total_input: int = 0
    dropped_malformed: int = 0
    dropped_out_of_range: int = 0
    dropped_low_accuracy: int = 0
    deduplicated: int = 0
    reordered: bool = False
    kept: int = 0
    issues: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "total_input": self.total_input,
            "dropped_malformed": self.dropped_malformed,
            "dropped_out_of_range": self.dropped_out_of_range,
            "dropped_low_accuracy": self.dropped_low_accuracy,
            "deduplicated": self.deduplicated,
            "reordered": self.reordered,
            "kept": self.kept,
            "issues": list(self.issues),
        }


class LocationTrack:
    """A cleaned, sorted sequence of GPS fixes supporting time-indexed lookup.

    Construct via :meth:`from_samples`, which performs the defensive cleaning; the
    constructor itself assumes clean input.
    """

    __slots__ = ("_samples", "_times", "stats", "max_gap_s")

    def __init__(
        self,
        samples: Sequence[LocationSample],
        *,
        stats: TrackStats | None = None,
        max_gap_s: float = DEFAULT_MAX_GAP_S,
    ) -> None:
        self._samples = list(samples)
        self._times = [s.t_epoch_ms for s in self._samples]
        self.stats = stats or TrackStats(total_input=len(self._samples), kept=len(self._samples))
        self.max_gap_s = max_gap_s

    # ---------------------------------------------------------------- construction

    @classmethod
    def from_samples(
        cls,
        raw: Iterable[LocationSample],
        *,
        max_accuracy_m: float | None = DEFAULT_MAX_ACCURACY_M,
        max_gap_s: float = DEFAULT_MAX_GAP_S,
    ) -> LocationTrack:
        """Clean, filter and sort raw fixes.

        Handles, without raising: out-of-order records, duplicate timestamps,
        out-of-range coordinates, NaNs, and poor-accuracy fixes. Everything discarded
        is counted in :attr:`stats` so the processing run can report it honestly
        instead of quietly producing a shorter track.
        """
        stats = TrackStats()
        cleaned: list[LocationSample] = []

        previous_t: int | None = None
        for s in raw:
            stats.total_input += 1

            # NaN is not caught by range checks (all comparisons with NaN are False),
            # so test for self-inequality explicitly.
            if s.lat != s.lat or s.lon != s.lon:
                stats.dropped_malformed += 1
                continue
            if not (-90.0 <= s.lat <= 90.0 and -180.0 <= s.lon <= 180.0):
                stats.dropped_out_of_range += 1
                continue
            # NaN accuracy is treated as unusable, same as an out-of-range value.
            if (
                max_accuracy_m is not None
                and s.accuracy_m is not None
                and (s.accuracy_m != s.accuracy_m or s.accuracy_m > max_accuracy_m)
            ):
                stats.dropped_low_accuracy += 1
                continue

            if previous_t is not None and s.t_epoch_ms < previous_t:
                stats.reordered = True
            previous_t = s.t_epoch_ms
            cleaned.append(s)

        cleaned.sort(key=lambda s: s.t_epoch_ms)

        # Collapse duplicate timestamps, keeping the most accurate fix. Duplicates are
        # common when a fused location provider emits a corrected fix for an instant it
        # already reported.
        deduped: list[LocationSample] = []
        for s in cleaned:
            if deduped and deduped[-1].t_epoch_ms == s.t_epoch_ms:
                stats.deduplicated += 1
                prev = deduped[-1]
                prev_acc = prev.accuracy_m if prev.accuracy_m is not None else float("inf")
                new_acc = s.accuracy_m if s.accuracy_m is not None else float("inf")
                if new_acc < prev_acc:
                    deduped[-1] = s
                continue
            deduped.append(s)

        stats.kept = len(deduped)
        if stats.reordered:
            stats.issues.append("GPS samples were out of order and have been sorted.")
        if stats.dropped_low_accuracy:
            stats.issues.append(
                f"{stats.dropped_low_accuracy} fixes dropped for accuracy worse than {max_accuracy_m} m."
            )
        if stats.dropped_malformed or stats.dropped_out_of_range:
            stats.issues.append(
                f"{stats.dropped_malformed + stats.dropped_out_of_range} malformed fixes dropped."
            )
        if not deduped:
            stats.issues.append("No usable GPS fixes remain; positions cannot be estimated.")

        return cls(deduped, stats=stats, max_gap_s=max_gap_s)

    # ------------------------------------------------------------------- accessors

    def __len__(self) -> int:
        return len(self._samples)

    def __bool__(self) -> bool:
        return bool(self._samples)

    @property
    def samples(self) -> Sequence[LocationSample]:
        return tuple(self._samples)

    @property
    def start_epoch_ms(self) -> int | None:
        return self._samples[0].t_epoch_ms if self._samples else None

    @property
    def end_epoch_ms(self) -> int | None:
        return self._samples[-1].t_epoch_ms if self._samples else None

    def total_distance_m(self) -> float:
        """Sum of great-circle hops. A rough survey length, not an odometer reading."""
        return sum(
            haversine_m(a.position, b.position)
            for a, b in zip(self._samples, self._samples[1:], strict=False)
        )

    # --------------------------------------------------------------- interpolation

    def locate(self, t_epoch_ms: int) -> InterpolatedLocation | None:
        """Estimate the device position at ``t_epoch_ms``.

        Returns ``None`` only when the track is empty. Otherwise always returns an
        estimate, with ``is_trustworthy`` and ``warning`` describing any problem — the
        caller decides whether to keep it, which keeps policy out of this module.
        """
        if not self._samples:
            return None

        # Before/after the track: clamp to the nearest end and say so. This happens
        # routinely because recording starts before the first fix arrives.
        first, last = self._samples[0], self._samples[-1]
        if t_epoch_ms <= first.t_epoch_ms:
            drift_s = (first.t_epoch_ms - t_epoch_ms) / 1000.0
            return self._clamped(first, t_epoch_ms, drift_s, "before first GPS fix")
        if t_epoch_ms >= last.t_epoch_ms:
            drift_s = (t_epoch_ms - last.t_epoch_ms) / 1000.0
            return self._clamped(last, t_epoch_ms, drift_s, "after last GPS fix")

        lo, hi = self._bracket(t_epoch_ms)
        a, b = self._samples[lo], self._samples[hi]
        span_ms = b.t_epoch_ms - a.t_epoch_ms
        gap_s = span_ms / 1000.0

        alpha = 0.0 if span_ms == 0 else (t_epoch_ms - a.t_epoch_ms) / span_ms
        position = interpolate_position(a.position, b.position, alpha)

        heading = self._interpolated_heading(a, b, alpha)
        speed = self._interpolated_speed(a, b, alpha, span_ms)

        uncertainty = self._uncertainty(a, b, alpha, gap_s)

        trustworthy = gap_s <= self.max_gap_s
        warning = (
            None
            if trustworthy
            else (
                f"GPS gap of {gap_s:.1f}s exceeds maximum {self.max_gap_s:.1f}s; "
                "position is an unverified straight-line guess."
            )
        )

        return InterpolatedLocation(
            t_epoch_ms=t_epoch_ms,
            position=position,
            method=LocationMethod.INTERPOLATED_PHONE_GPS,
            uncertainty_m=uncertainty,
            speed_mps=speed,
            heading_deg=heading,
            gap_s=gap_s,
            is_trustworthy=trustworthy,
            warning=warning,
        )

    # -------------------------------------------------------------------- internals

    def _clamped(
        self, sample: LocationSample, t_epoch_ms: int, drift_s: float, reason: str
    ) -> InterpolatedLocation:
        """Position for an instant outside the track: use the nearest fix, penalised.

        The penalty assumes the vehicle could have been moving at its last known speed
        (or a 13.9 m/s ~ 50 km/h urban default) for the whole drift.
        """
        assumed_speed = sample.speed_mps if sample.speed_mps else 13.9
        base = sample.accuracy_m if sample.accuracy_m is not None else 10.0
        return InterpolatedLocation(
            t_epoch_ms=t_epoch_ms,
            position=sample.position,
            method=LocationMethod.PHONE_GPS,
            uncertainty_m=base + assumed_speed * drift_s,
            speed_mps=sample.speed_mps,
            heading_deg=sample.heading_deg,
            gap_s=drift_s,
            is_trustworthy=drift_s <= 1.0,
            warning=f"Requested time is {reason} by {drift_s:.1f}s; clamped to nearest fix.",
        )

    def _bracket(self, t_epoch_ms: int) -> tuple[int, int]:
        """Indices of the fixes immediately before and after ``t_epoch_ms``."""
        import bisect

        idx = bisect.bisect_left(self._times, t_epoch_ms)
        if idx <= 0:
            return 0, min(1, len(self._samples) - 1)
        if idx >= len(self._samples):
            return len(self._samples) - 2, len(self._samples) - 1
        return idx - 1, idx

    @staticmethod
    def _interpolated_heading(a: LocationSample, b: LocationSample, alpha: float) -> float | None:
        """Heading at the interpolated instant.

        Prefers reported headings (circularly interpolated). Falls back to the bearing
        between the two fixes, which is only meaningful if the vehicle actually moved —
        the bearing between two near-identical stationary fixes is pure GPS noise.
        """
        if a.heading_deg is not None and b.heading_deg is not None:
            return interpolate_bearing(a.heading_deg, b.heading_deg, alpha)
        if a.heading_deg is not None:
            return a.heading_deg
        if b.heading_deg is not None:
            return b.heading_deg
        if haversine_m(a.position, b.position) >= 1.0:
            return initial_bearing_deg(a.position, b.position)
        return None

    @staticmethod
    def _interpolated_speed(
        a: LocationSample, b: LocationSample, alpha: float, span_ms: int
    ) -> float | None:
        if a.speed_mps is not None and b.speed_mps is not None:
            return a.speed_mps + (b.speed_mps - a.speed_mps) * alpha
        if a.speed_mps is not None:
            return a.speed_mps
        if b.speed_mps is not None:
            return b.speed_mps
        if span_ms > 0:
            return haversine_m(a.position, b.position) / (span_ms / 1000.0)
        return None

    @staticmethod
    def _uncertainty(a: LocationSample, b: LocationSample, alpha: float, gap_s: float) -> float:
        """Position uncertainty in metres.

        Two contributions, added rather than combined in quadrature — deliberately
        pessimistic, because over-reporting confidence is the expensive mistake here:

        1. **Fix accuracy**: interpolated between the bracketing reported accuracies.
        2. **Interpolation error**: a straight line between fixes cuts corners. The
           further the fixes are apart, the more room for the true path to deviate. We
           charge a quarter of the inter-fix distance at the midpoint, tapering to zero
           at each end where we are anchored to a real fix.
        """
        acc_a = a.accuracy_m if a.accuracy_m is not None else 10.0
        acc_b = b.accuracy_m if b.accuracy_m is not None else 10.0
        fix_error = acc_a + (acc_b - acc_a) * alpha

        separation = haversine_m(a.position, b.position)
        # Peaks at alpha=0.5, zero at both endpoints.
        taper = 4.0 * alpha * (1.0 - alpha)
        interp_error = 0.25 * separation * taper

        return round(fix_error + interp_error, 3)


def video_time_to_epoch_ms(recording_start_epoch_ms: int, video_time_s: float) -> int:
    """Convert a video presentation timestamp to absolute device time.

    Trivial arithmetic, given its own named function because it is the exact seam where
    the two time bases meet — the place a sign error or a seconds/milliseconds mix-up
    would silently displace every defect in a survey. Naming it makes it testable and
    greppable.
    """
    if video_time_s < 0:
        raise ValueError(f"video_time_s must be non-negative, got {video_time_s}")
    return int(round(recording_start_epoch_ms + video_time_s * 1000.0))
