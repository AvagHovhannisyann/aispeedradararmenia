"""The detector seam.

Everything downstream of this module depends on the :class:`RoadDamageDetector`
Protocol and nothing else. Concrete adapters (torchvision, MMDetection, ONNX Runtime,
Core ML, LiteRT) live behind it and are interchangeable.

This is ADR-004, and it is the decision that makes the M0 licensing uncertainty
survivable: if RDD2022 turns out to be share-alike, or if we swap RTMDet for an
RT-DETR variant, or if inference moves onto the phone, the cost is one adapter — not a
rewrite of tracking, clustering, geolocation, storage and review.

Framework types must never cross this boundary. A ``torch.Tensor`` in a domain model is
a bug.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from roadeye.domain.enums import DamageClass


@dataclass(frozen=True, slots=True)
class FrameImage:
    """An image handed to a detector.

    ``pixels`` is intentionally untyped (``object``): it may be a numpy array, a PIL
    image, or ``None`` when a synthetic/fake detector needs no pixels at all. Each
    adapter knows what it wants and validates on entry. This keeps numpy out of the
    core dependency set — the whole test suite runs without it.
    """

    frame_id: str
    width: int
    height: int
    pixels: object | None = None
    image_path: str | None = None


@dataclass(frozen=True, slots=True)
class RawDetection:
    """A detector's output for one object, before it becomes a domain ``Detection``.

    Coordinates are absolute pixels in the source frame, matching
    :class:`roadeye.domain.models.BoundingBox`.
    """

    damage_class: DamageClass
    confidence: float
    x1: float
    y1: float
    x2: float
    y2: float
    mask: dict | None = None


@runtime_checkable
class RoadDamageDetector(Protocol):
    """Anything that can find road damage in an image.

    Implementations must be **stateless with respect to frame order**: the pipeline may
    call :meth:`predict` on frames in any order, or in parallel. Temporal reasoning
    belongs in :mod:`roadeye.tracking`, not in the detector.
    """

    @property
    def model_id(self) -> str:
        """Stable identifier recorded on every detection for provenance."""
        ...

    @property
    def classes(self) -> Sequence[DamageClass]:
        """Classes this detector can emit."""
        ...

    def predict(self, frame: FrameImage) -> list[RawDetection]:
        """Return detections for one frame. Must not raise on a valid image."""
        ...


class DetectorError(RuntimeError):
    """Raised when a detector cannot be constructed or loaded.

    Deliberately distinct from inference failure: a missing dependency at startup is a
    setup problem the founder can fix, while a mid-run failure on one frame should
    degrade that frame, not kill a 30-minute survey.
    """
