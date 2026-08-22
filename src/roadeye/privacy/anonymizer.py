"""Detect, expand, destroy — and record exactly what was claimed.

The class is small because the interesting decisions are about failure, not about
success.

**Failure is loud.** Every path that cannot redact raises :class:`RedactionError`. There
is no fallback that writes the image anyway, no warning-and-continue, no "redaction
unavailable, proceeding". An unredacted evidence image is indistinguishable from a
redacted one by inspection, so a silent degradation is permanent and undetectable — the
worst shape a privacy bug can take.

**Success is qualified.** A :class:`RedactionReport` records which detector ran, at what
threshold, with what configuration, and how many regions it destroyed. It does not
record that the image is anonymous, because nothing here can know that: a detector that
missed somebody has produced an image with somebody in it. What the report supports is
the question that actually gets asked after a detector is found wanting — *which images
were written by that detector, so we can reprocess them?*
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from roadeye.privacy.base import RedactionError, Region, RegionDetector, RegionKind
from roadeye.privacy.redaction import RedactionConfig, redact_regions

#: Bumped when the meaning of a report changes, so an old report is never read as a new
#: one. Reports are the audit trail for a legal obligation, not a debug log.
REDACTION_REPORT_VERSION = 1


@dataclass(frozen=True)
class RedactionReport:
    """What was done to one image, and by what."""

    detector_id: str
    config: RedactionConfig
    regions: tuple[Region, ...] = field(default=())
    report_version: int = REDACTION_REPORT_VERSION

    @property
    def region_count(self) -> int:
        return len(self.regions)

    def count_of(self, kind: RegionKind) -> int:
        return sum(1 for r in self.regions if r.kind is kind)

    def to_json(self) -> dict[str, Any]:
        return {
            "report_version": self.report_version,
            "detector_id": self.detector_id,
            "config": self.config.to_json(),
            "regions_redacted": self.region_count,
            "by_kind": {
                kind.value: self.count_of(kind) for kind in RegionKind if self.count_of(kind)
            },
            "regions": [r.to_json() for r in self.regions],
            "notice": (
                "Redaction is best-effort. This records what a named detector found and "
                "destroyed; it is not a guarantee that no identifiable person remains."
            ),
        }


def summarise_reports(reports: list[RedactionReport]) -> dict[str, Any]:
    """Roll many per-image reports into one record for a whole run.

    Totals, not the last report's numbers. A manifest saying ``regions_redacted: 1``
    beside ``images_written: 22`` reads as "22 images, one region found in total", which
    would be a false claim about how much was destroyed — in the one document whose
    purpose is to be accurate about that.
    """
    per_kind: dict[str, int] = {}
    for report in reports:
        for kind in RegionKind:
            count = report.count_of(kind)
            if count:
                per_kind[kind.value] = per_kind.get(kind.value, 0) + count

    detectors = sorted({r.detector_id for r in reports})
    total = sum(r.region_count for r in reports)
    return {
        "report_version": REDACTION_REPORT_VERSION,
        # Normally one. More than one means the run changed redactor mid-way, which is
        # worth seeing rather than silently collapsing to the first.
        "detector_id": detectors[0] if len(detectors) == 1 else detectors,
        "config": reports[0].config.to_json() if reports else {},
        "images_redacted": len(reports),
        "images_with_no_regions": sum(1 for r in reports if r.region_count == 0),
        "regions_redacted": total,
        "by_kind": per_kind,
        "notice": (
            "Redaction is best-effort. This records what a named detector found and "
            "destroyed; it is not a guarantee that no identifiable person remains."
        ),
    }


class Anonymizer:
    """Runs a :class:`RegionDetector` over an image and destroys what it finds."""

    def __init__(
        self,
        detector: RegionDetector,
        *,
        config: RedactionConfig | None = None,
    ) -> None:
        if detector is None:
            raise RedactionError(
                "an Anonymizer needs a detector. Pass NullRegionDetector() to state "
                "deliberately that these images need no redaction — see docs/PRIVACY.md."
            )
        self.detector = detector
        self.config = config or RedactionConfig()

    @property
    def detector_id(self) -> str:
        return self.detector.detector_id

    def redact(self, image: Any) -> tuple[Any, RedactionReport]:
        """Return a redacted copy of ``image`` and a report of what was destroyed.

        Any failure inside the detector becomes a :class:`RedactionError`. Callers must
        not catch it and write the image regardless — that is the one thing this module
        exists to make impossible to do by accident.
        """
        import numpy as np

        array = np.asarray(image, dtype="uint8")
        if array.ndim != 3 or array.shape[2] != 3:
            raise ValueError(f"expected an HxWx3 uint8 image, got shape {array.shape}")
        height, width = array.shape[:2]

        try:
            regions = self.detector.find(array, width=width, height=height)
        except RedactionError:
            raise
        except Exception as exc:
            raise RedactionError(
                f"detector {self.detector.detector_id!r} failed on a "
                f"{width}x{height} image: {exc}. Refusing to write it unredacted."
            ) from exc

        redacted = redact_regions(array, list(regions), config=self.config)
        return redacted, RedactionReport(
            detector_id=self.detector.detector_id,
            config=self.config,
            regions=tuple(regions),
        )

    def redact_crop(
        self, image: Any, crop_box: tuple[int, int, int, int]
    ) -> tuple[Any, RedactionReport]:
        """Redact a crop by detecting on the **full** image, then cropping.

        Detecting on the crop instead would be worse in both directions: a person's legs
        alone rarely fire a person detector, and a face filling a close-up crop of a
        pothole is not what a model trained on whole scenes expects. Running on the full
        frame and then cutting keeps the detector in the distribution it was trained for,
        and a region straddling the crop boundary is still destroyed on the part that
        survives.
        """
        import numpy as np

        redacted, report = self.redact(image)
        x1, y1, x2, y2 = crop_box
        array = np.asarray(redacted)
        return array[y1:y2, x1:x2], report
