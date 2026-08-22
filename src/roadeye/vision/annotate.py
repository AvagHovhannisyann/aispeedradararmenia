"""Draw detections onto images, for human eyes.

Used by ``roadeye detect`` so a person can look at what the model actually found
rather than reading coordinates. This is a debugging and review aid, not the municipal
dashboard.

Pillow only — it arrives with torchvision, so this adds no new dependency.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from roadeye.domain.enums import DamageClass
from roadeye.vision.base import RawDetection

#: One colour per class, chosen to stay distinguishable in the desaturated greys and
#: browns of road imagery — and to remain separable for the most common form of colour
#: blindness, which is why red and green are not both used for adjacent classes.
CLASS_COLORS: dict[DamageClass, tuple[int, int, int]] = {
    DamageClass.POTHOLE: (229, 57, 53),  # red — the hero class, most urgent
    DamageClass.ALLIGATOR_CRACK: (255, 179, 0),  # amber
    DamageClass.LONGITUDINAL_CRACK: (30, 136, 229),  # blue
    DamageClass.TRANSVERSE_CRACK: (142, 36, 170),  # purple
}


def annotate_image(
    image_path: str | Path,
    detections: Sequence[RawDetection],
    output_path: str | Path,
    *,
    line_width: int = 3,
    show_labels: bool = True,
) -> Path:
    """Write a copy of the image with detection boxes drawn on it."""
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise RuntimeError(
            "Pillow is required to draw detections. Install with: pip install -e '.[vision]'"
        ) from exc

    image = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(image)

    for det in detections:
        color = CLASS_COLORS.get(det.damage_class, (255, 255, 255))
        draw.rectangle([det.x1, det.y1, det.x2, det.y2], outline=color, width=line_width)

        if not show_labels:
            continue
        label = f"{det.damage_class.value} {det.confidence:.2f}"
        # Filled backing strip: thin coloured text over grey asphalt is unreadable.
        try:
            box = draw.textbbox((0, 0), label)
            text_w, text_h = box[2] - box[0], box[3] - box[1]
        except AttributeError:  # pragma: no cover - very old Pillow
            text_w, text_h = 8 * len(label), 11

        # Put the label inside the box when it would otherwise run off the top edge.
        label_y = det.y1 - text_h - 4 if det.y1 - text_h - 4 >= 0 else det.y1 + 2
        draw.rectangle([det.x1, label_y, det.x1 + text_w + 6, label_y + text_h + 4], fill=color)
        draw.text((det.x1 + 3, label_y + 2), label, fill=(255, 255, 255))

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, quality=90)
    return output_path


def load_image_array(image_path: str | Path) -> Any:
    """Load an image as an HWC uint8 array, which is what detectors expect."""
    try:
        import numpy as np
        from PIL import Image
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "numpy and Pillow are required. Install with: pip install -e '.[vision]'"
        ) from exc

    with Image.open(image_path) as handle:
        return np.array(handle.convert("RGB"), dtype="uint8")


def iter_images(path: str | Path) -> list[Path]:
    """Every image under ``path``, or just it if it is a file."""
    path = Path(path)
    if path.is_file():
        return [path]
    suffixes = {".jpg", ".jpeg", ".png", ".bmp"}
    return sorted(p for p in path.rglob("*") if p.suffix.lower() in suffixes)
