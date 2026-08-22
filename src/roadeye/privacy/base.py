"""The redaction seam: what gets blurred, and what finds it.

This mirrors ``vision/base.py`` deliberately. Both are places where a model plugs into
the pipeline, and both are places where the model will be replaced — so the pipeline
depends on a Protocol, never on a framework.

**The boundary that matters most is what this does not do.** RoadEye detects regions in
order to destroy them. It does not recognise faces, re-identify people, read plates, or
match anything against a database. Those are a different product with a different legal
analysis (``docs/PRIVACY.md``, ADR-007), and the distinction is not a matter of
configuration: there is no code here that extracts, encodes or compares an identity,
because the only thing done with a detected region is to overwrite it.

That is also why regions carry a coarse :class:`RegionKind` rather than an identity, a
track, or an embedding. A pedestrian is ``PERSON``; the next frame's pedestrian is also
``PERSON``, with nothing linking the two.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, runtime_checkable


class RedactionError(RuntimeError):
    """Raised when redaction cannot be performed.

    Deliberately an error rather than a fallback. Writing an unredacted image because
    the redactor was unavailable is the exact failure ``docs/PRIVACY.md`` exists to
    prevent, and it is silent: the file looks like every other evidence image.
    """


class RegionKind(str, Enum):
    """What kind of thing a region is — coarse on purpose.

    Coarse enough to decide "blur this", never fine enough to describe a person.
    """

    #: A human figure. The whole figure, not a face: a box around the person covers the
    #: face even when the face itself was never localised, and covers it at distances
    #: where a face detector would have found nothing.
    PERSON = "person"
    #: A vehicle. Blurred whole, which is how the plate is covered without ever looking
    #: for a plate — see ADR-007's prohibition on ALPR.
    VEHICLE = "vehicle"


@dataclass(frozen=True)
class Region:
    """A rectangle to destroy, in pixel coordinates of the source image."""

    x1: float
    y1: float
    x2: float
    y2: float
    kind: RegionKind
    confidence: float

    def __post_init__(self) -> None:
        if self.x2 < self.x1 or self.y2 < self.y1:
            raise ValueError(f"inverted region: {self}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence out of range: {self.confidence}")

    @property
    def width(self) -> float:
        return self.x2 - self.x1

    @property
    def height(self) -> float:
        return self.y2 - self.y1

    def expanded(self, margin_fraction: float, *, width: int, height: int) -> Region:
        """Grow the box by a fraction of its size, clamped to the image.

        Detectors fit boxes to what they are confident about, which for a person tends
        to exclude hair and for a vehicle tends to clip a bumper. A tight box that is
        slightly wrong leaves an ear, a hairline or a plate corner showing, and a
        partially redacted face is not a redacted face.
        """
        if margin_fraction < 0:
            raise ValueError(f"margin must be non-negative, got {margin_fraction}")
        dx = self.width * margin_fraction
        dy = self.height * margin_fraction
        return Region(
            x1=max(0.0, self.x1 - dx),
            y1=max(0.0, self.y1 - dy),
            x2=min(float(width), self.x2 + dx),
            y2=min(float(height), self.y2 + dy),
            kind=self.kind,
            confidence=self.confidence,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "confidence": round(self.confidence, 4),
            "box": [round(self.x1, 1), round(self.y1, 1), round(self.x2, 1), round(self.y2, 1)],
        }


@runtime_checkable
class RegionDetector(Protocol):
    """Finds regions that must be destroyed before an image is retained.

    Implementations must be **offline**. A redactor that calls a network service has
    uploaded the very frame it was supposed to protect (``docs/PRIVACY.md``, rule 2).
    """

    @property
    def detector_id(self) -> str:
        """Stable identifier recorded alongside every redacted image.

        Recorded because redaction is best-effort. When a detector is later found to
        miss a class of subject, the only way to know which images need reprocessing is
        to know which detector wrote them.
        """
        ...

    def find(self, image: Any, *, width: int, height: int) -> list[Region]:
        """Return regions to redact. ``image`` is an HxWx3 uint8 array."""
        ...
