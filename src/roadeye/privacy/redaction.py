"""Destroying pixels, and being honest about how thoroughly.

Redaction is the one operation in RoadEye that must not be undoable. Everything else in
the system is designed so a decision can be traced and revisited; this is designed so a
face cannot be.

## Why mosaic rather than blur

A Gaussian blur *looks* destructive and is not. It is a convolution — a linear operator
— so with the kernel known, deconvolution recovers a great deal, and the kernel is
knowable because it is in this file. A blurred face is obscured, not erased.

Block averaging genuinely maps many pixels onto one value. The information is gone in
the arithmetic, not merely scrambled.

**The honest caveat:** mosaic is not unconditionally safe either. Where the content has
low entropy — text, and a licence plate is text — an attacker can enumerate candidate
strings, mosaic each one, and compare. That attack is why RoadEye redacts *whole
vehicles* rather than plates: the block size is then large relative to any text inside
it, and the candidate space is the space of vehicles, not the space of six-character
strings. :attr:`RedactionMethod.SOLID` exists for when certainty matters more than the
image still looking like a road.

Nothing here claims to produce an "anonymised" image. It produces a *redacted* one, by
a named method, over regions a named detector found — and a detector that missed
somebody has produced an image with somebody in it, whatever the label says.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from roadeye.privacy.base import Region


class RedactionMethod(str, Enum):
    #: Block-average the region. Irreversible arithmetic; the picture still reads as a
    #: street scene, which matters when a human is reviewing a defect behind a car.
    MOSAIC = "mosaic"
    #: Replace the region with a flat colour. Total destruction, no residual structure
    #: at all. Use where certainty outweighs the image remaining legible.
    SOLID = "solid"


@dataclass(frozen=True)
class RedactionConfig:
    """Every knob that decides how thoroughly a region is destroyed.

    Recorded in the redaction report and in ``ProcessingRun.config``, because "these
    images were redacted" is not a reproducible claim without it.
    """

    method: RedactionMethod = RedactionMethod.MOSAIC

    #: How many blocks span the region's shorter side. Four means a face becomes a 4x4
    #: grid — far below what any recognition needs, and below what a human needs.
    blocks_across: int = 4

    #: Floor on block size in pixels, so a small region is not mosaicked at 1x1 (which
    #: is the identity function, and would silently redact nothing).
    min_block_px: int = 8

    #: Grow each detected box by this fraction before destroying it. Detectors fit boxes
    #: to what they are sure of, which clips hair and bumpers; a partially redacted face
    #: is not a redacted face.
    margin_fraction: float = 0.15

    #: Flat colour used by :attr:`RedactionMethod.SOLID`, as RGB.
    solid_color: tuple[int, int, int] = (16, 16, 16)

    def __post_init__(self) -> None:
        if self.blocks_across < 1:
            raise ValueError(f"blocks_across must be >= 1, got {self.blocks_across}")
        if self.min_block_px < 2:
            # A 1-pixel block is the identity function. Refusing beats redacting nothing
            # while reporting success.
            raise ValueError(f"min_block_px must be >= 2, got {self.min_block_px}")

    def to_json(self) -> dict[str, Any]:
        return {
            "method": self.method.value,
            "blocks_across": self.blocks_across,
            "min_block_px": self.min_block_px,
            "margin_fraction": self.margin_fraction,
        }


def block_size_for(region_width: int, region_height: int, config: RedactionConfig) -> int:
    """Block size in pixels for a region of this size.

    Scaled to the region rather than fixed, so a pedestrian 12 px tall at the end of the
    street is destroyed as thoroughly as one filling the frame. A fixed block would
    leave the distant one untouched — and the distant one is the more common case in
    windscreen video.
    """
    shorter = max(1, min(region_width, region_height))
    return max(config.min_block_px, -(-shorter // config.blocks_across))


def redact_regions(
    image: Any,
    regions: list[Region],
    *,
    config: RedactionConfig | None = None,
) -> Any:
    """Return a copy of ``image`` with every region destroyed.

    A copy, never in place: the caller may still hold the original for a legitimate
    local purpose, and silently mutating it is how an unredacted frame ends up being the
    thing that was written.
    """
    import numpy as np

    cfg = config or RedactionConfig()
    out = np.array(image, dtype="uint8", copy=True)
    if out.ndim != 3 or out.shape[2] != 3:
        raise ValueError(f"expected an HxWx3 uint8 image, got shape {out.shape}")

    height, width = out.shape[:2]
    for region in regions:
        grown = region.expanded(cfg.margin_fraction, width=width, height=height)
        x1, y1 = int(grown.x1), int(grown.y1)
        x2, y2 = min(width, int(round(grown.x2))), min(height, int(round(grown.y2)))
        if x2 - x1 < 1 or y2 - y1 < 1:
            continue

        patch = out[y1:y2, x1:x2]
        if cfg.method is RedactionMethod.SOLID:
            patch[:, :, :] = np.array(cfg.solid_color, dtype="uint8")
        else:
            out[y1:y2, x1:x2] = _mosaic(patch, block_size_for(x2 - x1, y2 - y1, cfg))

    return out


def _mosaic(patch: Any, block: int) -> Any:
    """Replace each ``block``x``block`` tile with its mean colour."""
    import numpy as np

    height, width = patch.shape[:2]
    tall = -(-height // block)
    wide = -(-width // block)

    # Pad up to a whole number of blocks by repeating the edge, so the reshape is exact.
    # Edge padding biases a partial block toward its outer pixels, which is visually
    # irrelevant and arithmetically just as lossy.
    padded = np.pad(
        patch, ((0, tall * block - height), (0, wide * block - width), (0, 0)), mode="edge"
    )
    means = padded.reshape(tall, block, wide, block, 3).mean(axis=(1, 3))
    grid = np.repeat(np.repeat(means, block, axis=0), block, axis=1)
    return grid[:height, :width].astype("uint8")
