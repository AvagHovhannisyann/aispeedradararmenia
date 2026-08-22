"""Frame sampling: deciding which frames are worth analysing.

Recording is smooth (1080p30) but inference is not run on every frame. At 50 km/h a
vehicle covers 13.89 m/s, so 30 fps means a frame every 46 cm — hugely redundant. Worse,
*fixed-rate* sampling has a pathological failure: idling at a red light for 20 seconds
produces 600 near-identical images of the same tarmac, which then poison both the
training set (duplicates) and the defect count (the same crack detected 600 times).

Distance-based sampling fixes both. It is the preferred mode whenever GPS is usable,
and falls back to fixed-rate automatically when it is not.

Sampling is a *plan* — a list of timestamps — computed before any decoding. That keeps
this module pure and fully testable without ffmpeg, and lets the decoder seek directly
to the frames we want instead of walking the whole file.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from roadeye.domain.enums import SamplingMode
from roadeye.geolocation.geodesy import haversine_m
from roadeye.geolocation.timesync import LocationTrack, video_time_to_epoch_ms


@dataclass(frozen=True, slots=True)
class SamplingConfig:
    """How to choose frames. Recorded verbatim in the processing run for reproducibility."""

    mode: SamplingMode = SamplingMode.DISTANCE
    #: Target metres between analysed frames in DISTANCE mode.
    target_spacing_m: float = 2.5
    #: Frames per second in FIXED_FPS mode, and the fallback when GPS is unusable.
    fallback_fps: float = 4.0
    #: Seconds between frames in FIXED_INTERVAL mode.
    interval_s: float = 0.5
    #: Below this speed the vehicle is treated as stopped and no new frames are taken,
    #: regardless of mode. This is the red-light guard.
    min_speed_mps: float = 1.0
    #: Never emit two samples closer together than this, whatever the distance rule
    #: says. Protects against GPS jitter manufacturing spurious movement.
    min_time_delta_s: float = 0.1
    #: Safety valve for very long surveys.
    max_frames: int | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode.value,
            "target_spacing_m": self.target_spacing_m,
            "fallback_fps": self.fallback_fps,
            "interval_s": self.interval_s,
            "min_speed_mps": self.min_speed_mps,
            "min_time_delta_s": self.min_time_delta_s,
            "max_frames": self.max_frames,
        }


@dataclass(frozen=True, slots=True)
class SampledFrameRef:
    """One planned frame: where it is in the video and when it happened."""

    index: int
    video_time_s: float
    t_epoch_ms: int
    #: Metres travelled since the previous sampled frame. ``None`` in time-based modes.
    distance_from_previous_m: float | None = None


@dataclass
class SamplingPlan:
    """The frames to analyse, plus why the plan looks the way it does."""

    frames: list[SampledFrameRef] = field(default_factory=list)
    mode_used: SamplingMode = SamplingMode.DISTANCE
    #: True when DISTANCE was requested but GPS could not support it.
    fell_back: bool = False
    notes: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.frames)

    @property
    def video_times(self) -> list[float]:
        return [f.video_time_s for f in self.frames]


def _time_based_plan(
    duration_s: float,
    step_s: float,
    recording_start_epoch_ms: int,
    max_frames: int | None,
) -> list[SampledFrameRef]:
    if step_s <= 0:
        raise ValueError(f"sampling step must be positive, got {step_s}")
    out: list[SampledFrameRef] = []
    t = 0.0
    idx = 0
    while t <= duration_s + 1e-9:
        if max_frames is not None and len(out) >= max_frames:
            break
        out.append(
            SampledFrameRef(
                index=idx,
                video_time_s=round(t, 6),
                t_epoch_ms=video_time_to_epoch_ms(recording_start_epoch_ms, t),
            )
        )
        idx += 1
        t += step_s
    return out


def build_sampling_plan(
    *,
    duration_s: float,
    recording_start_epoch_ms: int,
    config: SamplingConfig,
    track: LocationTrack | None = None,
) -> SamplingPlan:
    """Compute which frames to analyse.

    ``duration_s`` is the video length. ``track`` is required for DISTANCE mode; if it
    is missing or too sparse the plan degrades to fixed-rate sampling and says so in
    :attr:`SamplingPlan.fell_back`, rather than failing or silently producing garbage.
    """
    if duration_s < 0:
        raise ValueError(f"duration_s must be non-negative, got {duration_s}")

    plan = SamplingPlan(mode_used=config.mode)

    if config.mode is SamplingMode.FIXED_FPS:
        plan.frames = _time_based_plan(
            duration_s, 1.0 / config.fallback_fps, recording_start_epoch_ms, config.max_frames
        )
        return plan

    if config.mode is SamplingMode.FIXED_INTERVAL:
        plan.frames = _time_based_plan(
            duration_s, config.interval_s, recording_start_epoch_ms, config.max_frames
        )
        return plan

    # ---- DISTANCE mode ----
    if track is None or len(track) < 2:
        plan.mode_used = SamplingMode.FIXED_FPS
        plan.fell_back = True
        plan.notes.append(
            "Distance sampling requested but fewer than two usable GPS fixes are "
            f"available; fell back to {config.fallback_fps} fps."
        )
        plan.frames = _time_based_plan(
            duration_s, 1.0 / config.fallback_fps, recording_start_epoch_ms, config.max_frames
        )
        return plan

    # Walk the video on a fine time grid and emit a frame whenever the vehicle has
    # travelled far enough. The grid is the fallback rate, which bounds how finely we
    # can honour the spacing target and keeps this loop O(duration * fps).
    step_s = 1.0 / config.fallback_fps
    frames: list[SampledFrameRef] = []
    last_position = None
    last_emit_t = None
    idx = 0
    stationary_skips = 0
    untrustworthy = 0

    t = 0.0
    while t <= duration_s + 1e-9:
        if config.max_frames is not None and len(frames) >= config.max_frames:
            plan.notes.append(f"Truncated at max_frames={config.max_frames}.")
            break

        epoch_ms = video_time_to_epoch_ms(recording_start_epoch_ms, t)
        loc = track.locate(epoch_ms)
        if loc is None:
            t += step_s
            continue
        if not loc.is_trustworthy:
            untrustworthy += 1

        # Red-light guard: a reported speed below threshold means don't emit.
        if loc.speed_mps is not None and loc.speed_mps < config.min_speed_mps:
            stationary_skips += 1
            t += step_s
            continue

        if last_position is None:
            emit, travelled = True, None
        else:
            travelled = haversine_m(last_position, loc.position)
            enough_distance = travelled >= config.target_spacing_m
            enough_time = (t - last_emit_t) >= config.min_time_delta_s
            emit = enough_distance and enough_time

        if emit:
            frames.append(
                SampledFrameRef(
                    index=idx,
                    video_time_s=round(t, 6),
                    t_epoch_ms=epoch_ms,
                    distance_from_previous_m=None if travelled is None else round(travelled, 3),
                )
            )
            idx += 1
            last_position = loc.position
            last_emit_t = t

        t += step_s

    plan.frames = frames
    if stationary_skips:
        plan.notes.append(
            f"Skipped {stationary_skips} grid points where speed was below "
            f"{config.min_speed_mps} m/s (stationary)."
        )
    if untrustworthy:
        plan.notes.append(
            f"{untrustworthy} grid points fell in low-confidence GPS regions; "
            "their frames carry elevated location uncertainty."
        )
    if not frames:
        plan.notes.append(
            "Distance sampling produced no frames — the vehicle may never have exceeded "
            "the minimum speed. Consider FIXED_FPS for this survey."
        )
    return plan


def estimated_spacing_m(speed_mps: float, fps: float) -> float:
    """Metres between frames at a given speed and analysis rate.

    A planning helper for the collection protocol: at 50 km/h (13.89 m/s) and 5 fps,
    frames land every 2.78 m.
    """
    if fps <= 0:
        raise ValueError(f"fps must be positive, got {fps}")
    return speed_mps / fps
