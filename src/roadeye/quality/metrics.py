"""Image-quality gating before inference.

Purpose is not to throw data away — it is to *label* it. A detection made on a
motion-blurred frame at dusk is not the same evidence as one made on a sharp frame at
noon, and a system that forgets the difference cannot later explain its own failures.

Three outcomes (:class:`~roadeye.domain.enums.FrameQuality`):

* ``ACCEPTED``  — analyse normally.
* ``DEGRADED``  — analyse, but flag; detections inherit reduced trust.
* ``REJECTED``  — do not analyse; count it in the run summary.

``DEGRADED`` exists deliberately. A binary gate forces every marginal frame to be either
silently trusted or silently discarded, and both are ways of losing information you
later need to answer "why did we miss that pothole?"

numpy is optional throughout. When it is absent, or when a frame carries no pixels (the
synthetic source), scoring is skipped and the frame is ACCEPTED — the pipeline must
remain runnable on a bare machine.
"""

from __future__ import annotations

from dataclasses import dataclass

from roadeye.domain.enums import FrameQuality


@dataclass(frozen=True, slots=True)
class QualityConfig:
    """Thresholds for the quality gate. Recorded in the processing run.

    Defaults are **untuned starting points**, not measured values. They must be
    calibrated against real Yerevan footage during M4; until then, treat any quality
    statistic they produce as provisional.
    """

    #: Variance-of-Laplacian below this is treated as blurred. The classic threshold
    #: cited for this measure is ~100 on 8-bit greyscale, but it scales with image
    #: content and resolution, so it must be re-tuned on our own footage.
    blur_reject_below: float = 40.0
    blur_degrade_below: float = 100.0
    #: Mean luminance (0-255) outside these bounds is under/over exposed.
    dark_reject_below: float = 25.0
    dark_degrade_below: float = 50.0
    bright_reject_above: float = 235.0
    bright_degrade_above: float = 210.0

    def as_dict(self) -> dict[str, float]:
        return {
            "blur_reject_below": self.blur_reject_below,
            "blur_degrade_below": self.blur_degrade_below,
            "dark_reject_below": self.dark_reject_below,
            "dark_degrade_below": self.dark_degrade_below,
            "bright_reject_above": self.bright_reject_above,
            "bright_degrade_above": self.bright_degrade_above,
        }


@dataclass(frozen=True, slots=True)
class QualityResult:
    verdict: FrameQuality
    scores: dict[str, float]
    reasons: list[str]


def _to_grayscale_array(pixels: object):
    """Best-effort conversion to a 2-D float array. Returns ``None`` if unavailable."""
    try:
        import numpy as np
    except ImportError:
        return None
    if pixels is None:
        return None
    try:
        arr = np.asarray(pixels, dtype="float64")
    except (TypeError, ValueError):
        return None
    if arr.ndim == 3:
        # Rec. 601 luma. Good enough, and matches what most CV tooling assumes.
        arr = arr[..., 0] * 0.299 + arr[..., 1] * 0.587 + arr[..., 2] * 0.114
    if arr.ndim != 2 or arr.size == 0:
        return None
    return arr


def blur_score(pixels: object) -> float | None:
    """Variance of the Laplacian: higher means sharper.

    Implemented with plain array slicing rather than a convolution library so the only
    optional dependency is numpy itself.
    """
    arr = _to_grayscale_array(pixels)
    if arr is None or arr.shape[0] < 3 or arr.shape[1] < 3:
        return None
    import numpy as np

    # Discrete 4-neighbour Laplacian on the interior.
    lap = -4.0 * arr[1:-1, 1:-1] + arr[:-2, 1:-1] + arr[2:, 1:-1] + arr[1:-1, :-2] + arr[1:-1, 2:]
    return float(np.var(lap))


def brightness_score(pixels: object) -> float | None:
    """Mean luminance in 0-255."""
    arr = _to_grayscale_array(pixels)
    if arr is None:
        return None
    import numpy as np

    return float(np.mean(arr))


def assess(pixels: object, config: QualityConfig | None = None) -> QualityResult:
    """Score a frame and decide whether to analyse it.

    A frame with no pixels (synthetic source, or numpy unavailable) is ACCEPTED with an
    empty score set. Refusing to run in that case would make the pipeline untestable on
    a bare machine, which is a worse failure than skipping an optional check.
    """
    cfg = config or QualityConfig()
    scores: dict[str, float] = {}
    reasons: list[str] = []
    verdict = FrameQuality.ACCEPTED

    blur = blur_score(pixels)
    if blur is not None:
        scores["blur"] = round(blur, 3)
        if blur < cfg.blur_reject_below:
            verdict = FrameQuality.REJECTED
            reasons.append(
                f"blur variance {blur:.1f} below reject threshold {cfg.blur_reject_below}"
            )
        elif blur < cfg.blur_degrade_below:
            verdict = FrameQuality.DEGRADED
            reasons.append(
                f"blur variance {blur:.1f} below degrade threshold {cfg.blur_degrade_below}"
            )

    brightness = brightness_score(pixels)
    if brightness is not None:
        scores["brightness"] = round(brightness, 3)
        if brightness < cfg.dark_reject_below or brightness > cfg.bright_reject_above:
            verdict = FrameQuality.REJECTED
            reasons.append(f"mean luminance {brightness:.1f} outside usable range")
        elif (
            brightness < cfg.dark_degrade_below or brightness > cfg.bright_degrade_above
        ) and verdict is not FrameQuality.REJECTED:
            verdict = FrameQuality.DEGRADED
            reasons.append(f"mean luminance {brightness:.1f} marginal")

    return QualityResult(verdict=verdict, scores=scores, reasons=reasons)
