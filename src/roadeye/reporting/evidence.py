"""Save the image a reviewer needs to judge a defect.

A defect record points at a frame id, which is enough for provenance but useless to a
human. Review is the bottleneck in the whole product — a reviewer decides in seconds
only if the evidence is already on screen — so the pipeline extracts and stores a
picture per defect.

Two images are saved:

* **context** — the full frame with the detection outlined, so the reviewer can see
  *where on the road* it is. A tight crop of grey asphalt is unjudgeable.
* **crop** — a padded close-up, so small defects are actually visible without zooming.

**Privacy.** These images are extracted from public-road video and may contain faces,
licence plates and identifiable people. They are the *only* part of the defect database
that carries personal data, which is exactly why they are isolated in one directory
rather than scattered.

Pass an :class:`~roadeye.privacy.anonymizer.Anonymizer` to redact them. It runs **once,
on the full frame, before any of the three files is derived from it** — so there is no
ordering in which an unredacted copy gets written. Redacting each output separately
would be three chances to forget one, and the one forgotten would be the clean frame
that feeds training.

Without an anonymizer the images are written as-is, which is legitimate for local
processing and not for anything else: see ``docs/PRIVACY.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from roadeye.domain.models import BoundingBox

if TYPE_CHECKING:
    from roadeye.privacy.anonymizer import Anonymizer, RedactionReport

#: Padding around a detection when cropping, as a fraction of the box's larger side.
#: A pothole with no surrounding road is hard to judge — context is what tells a
#: reviewer whether it is a pothole or a manhole cover.
CROP_PADDING = 1.0

#: Minimum crop size in pixels. Small distant defects would otherwise produce a
#: thumbnail too small to judge.
MIN_CROP_PX = 160


@dataclass(frozen=True, slots=True)
class EvidencePaths:
    """Where a defect's images ended up, relative to the evidence directory."""

    #: The frame without the detection box drawn on it. **The only one usable for
    #: training** — and redacted, if an anonymizer was supplied.
    frame: str
    #: The frame with the detection outlined, for a human to look at.
    context: str
    #: A padded close-up, for judging small defects.
    crop: str
    #: What redaction was applied, or ``None`` if the images were written unredacted.
    redaction: RedactionReport | None = None


def evidence_dir_for(db_path: str | Path) -> Path:
    """Evidence lives beside the database, so the two travel together."""
    path = Path(db_path)
    return path.parent / f"{path.stem}_evidence"


def save_defect_evidence(
    defect_id: str,
    pixels: object,
    bbox: BoundingBox | None,
    output_dir: str | Path,
    *,
    anonymizer: Anonymizer | None = None,
) -> EvidencePaths | None:
    """Write frame, context and crop images for one defect.

    Returns ``None`` when the images cannot be written (no Pillow, unusable pixels)
    rather than raising: losing the picture for one defect must not fail a survey that
    otherwise processed correctly.

    A **redaction** failure is the opposite and propagates as
    :class:`~roadeye.privacy.base.RedactionError`. The two are different in kind: bad
    pixels affect one defect, whereas a broken redactor affects every image in the run.
    Swallowing that would produce a survey that finished cleanly, wrote no evidence, and
    said nothing about why — or worse, invited a later "just skip redaction if it
    fails". Stopping on the first one is the point.
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:  # pragma: no cover - depends on optional extra
        return None

    if pixels is None:
        return None

    try:
        import numpy as np

        array = np.asarray(pixels)
        if array.ndim != 3 or array.shape[2] != 3:
            return None
        array = array.astype("uint8")
    except (ImportError, ValueError, TypeError):
        return None

    # Redact once, here, before anything is derived. Every file below comes from this
    # array, so there is no ordering of the writes that leaks an unredacted copy.
    report = None
    if anonymizer is not None:
        array, report = anonymizer.redact(array)

    try:
        image = Image.fromarray(array, "RGB")
    except (ValueError, TypeError):
        return None

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # The box-free frame, saved separately and first. Training must never use the
    # context image: it has a red rectangle drawn on it, and a model trained on those
    # learns to find red rectangles. Keeping a box-free copy is the only way the review
    # images and the training images can be the same pixels without that contamination.
    frame_name = f"{defect_id}_frame.jpg"
    image.save(output_dir / frame_name, quality=90)

    context = image.copy()
    if bbox is not None:
        draw = ImageDraw.Draw(context)
        draw.rectangle([bbox.x1, bbox.y1, bbox.x2, bbox.y2], outline=(229, 57, 53), width=4)
    context_name = f"{defect_id}_context.jpg"
    context.save(output_dir / context_name, quality=85)

    crop_name = f"{defect_id}_crop.jpg"
    if bbox is None:
        image.save(output_dir / crop_name, quality=85)
    else:
        _crop_around(image, bbox).save(output_dir / crop_name, quality=88)

    return EvidencePaths(frame=frame_name, context=context_name, crop=crop_name, redaction=report)


def _crop_around(image, bbox: BoundingBox):
    """A padded square-ish crop centred on the detection, clamped to the image."""
    cx, cy = bbox.center
    half = max(bbox.width, bbox.height) * (0.5 + CROP_PADDING)
    half = max(half, MIN_CROP_PX / 2)

    left = max(0, int(cx - half))
    top = max(0, int(cy - half))
    right = min(image.width, int(cx + half))
    bottom = min(image.height, int(cy + half))

    # Clamping at an image edge can collapse the box; fall back to the whole frame
    # rather than saving a zero-size image.
    if right <= left or bottom <= top:
        return image
    return image.crop((left, top, right, bottom))
