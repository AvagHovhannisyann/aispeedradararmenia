"""Turn human review decisions into a training dataset.

This closes the loop that makes RoadEye compound:

    AI detects -> human corrects -> **this module** -> dataset grows
        -> model retrained -> AI improves -> human work decreases

Three kinds of review decision become three kinds of training signal, and the second and
third matter more than they look:

* **Approved** — a confirmed defect. An ordinary positive example.
* **Class corrected** — a confirmed defect the model *misclassified*. Worth more than an
  approval: it is a case the model got wrong in a specific, learnable way.
* **Rejected** — a **hard negative**. The model saw a manhole, a shadow, a tar repair or
  a wet patch and called it damage. ``docs/ML_STRATEGY.md`` says a dataset of only
  beautiful potholes produces a model that has learned "dark irregular blob = pothole",
  which demos well and fails on the first real drive. Rejections are the cure, and they
  arrive free as a by-product of review.

Output matches the format written by ``ml/datasets/rdd2022.py``, so the training script
consumes reviewed Armenian data and bootstrap data identically.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from roadeye.domain.enums import DefectStatus, ReviewAction
from roadeye.storage.db import Database

#: Schema version of the exported dataset, matching the RDD2022 ingest output.
DATASET_SCHEMA_VERSION = 1


@dataclass
class ExportStats:
    """What the export produced, and what it had to skip."""

    positives: int = 0
    corrected: int = 0
    negatives: int = 0
    skipped_no_image: int = 0
    skipped_unreviewed: int = 0
    skipped_no_box: int = 0
    surveys: list[str] = field(default_factory=list)

    @property
    def images(self) -> int:
        return self.positives + self.negatives

    def summary(self) -> str:
        return (
            f"{self.images} images "
            f"({self.positives} positive, {self.negatives} hard negative, "
            f"of which {self.corrected} were class corrections); "
            f"skipped {self.skipped_no_image} without evidence, "
            f"{self.skipped_unreviewed} unreviewed, {self.skipped_no_box} without a box"
        )


def _survey_disjoint_splits(
    records: list[dict[str, Any]], *, train: float = 0.7, val: float = 0.15
) -> dict[str, list[str]]:
    """Split by **survey**, so no two images from one drive land in different splits.

    This is the real route-disjoint split that ADR-008 asks for and that RDD2022 could
    only approximate: here we know which drive each image came from. Frames from one
    survey are metres apart and often show the same defect, so splitting within a survey
    would leak near-duplicates into the test set and inflate every metric.

    With only one survey, everything goes to train and the caller is told — a single
    drive cannot produce an honest held-out set, and pretending otherwise would be
    worse than an empty test split.
    """
    by_survey: dict[str, list[str]] = {}
    for record in records:
        by_survey.setdefault(record["survey_id"] or "unknown", []).append(record["image_id"])

    surveys = sorted(by_survey)
    splits: dict[str, list[str]] = {"train": [], "val": [], "test": []}
    if len(surveys) == 1:
        splits["train"] = by_survey[surveys[0]]
        return splits

    train_end = max(1, int(len(surveys) * train))
    val_end = max(train_end, int(len(surveys) * (train + val)))
    for i, survey in enumerate(surveys):
        bucket = "train" if i < train_end else ("val" if i < val_end else "test")
        splits[bucket].extend(by_survey[survey])
    return splits


def export_reviewed_dataset(
    db_path: str | Path,
    output_dir: str | Path,
    *,
    evidence_dir: str | Path | None = None,
    name: str | None = None,
    include_negatives: bool = True,
) -> ExportStats:
    """Write a training dataset from reviewed defects.

    Only reviewed defects are exported. An unreviewed (``PROBABLE``) defect is the
    model's own opinion — training on it would teach the model to agree with itself,
    which is how a detector's errors become permanent.
    """
    db_path = Path(db_path)
    if evidence_dir is None:
        from roadeye.reporting.evidence import evidence_dir_for

        evidence_dir = evidence_dir_for(db_path)
    evidence_dir = Path(evidence_dir)

    output_dir = Path(output_dir)
    images_dir = output_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    stats = ExportStats()
    records: list[dict[str, Any]] = []

    with Database(db_path) as db:
        for defect in db.list_defects():
            if defect.status is DefectStatus.PROBABLE:
                stats.skipped_unreviewed += 1
                continue
            if defect.status not in (DefectStatus.VERIFIED, DefectStatus.REJECTED):
                continue
            if defect.status is DefectStatus.REJECTED and not include_negatives:
                continue

            # The clean frame, never the context image — that one has a box drawn on
            # it, and training on it teaches the model to find red rectangles.
            source = evidence_dir / f"{defect.defect_id}_frame.jpg"
            if not source.exists():
                stats.skipped_no_image += 1
                continue

            boxes: list[dict[str, Any]] = []
            if defect.status is DefectStatus.VERIFIED:
                detections = db.detections_for(defect.defect_id)
                frame_id = defect.representative_frame_id
                for det in detections:
                    if frame_id and det.frame_id != frame_id:
                        continue
                    boxes.append(
                        {
                            # The human's class, not the model's — that correction is
                            # the whole point of the export.
                            "rdd_code": "",
                            "damage_class": defect.damage_class.value,
                            "xmin": int(det.bbox.x1),
                            "ymin": int(det.bbox.y1),
                            "xmax": int(det.bbox.x2),
                            "ymax": int(det.bbox.y2),
                        }
                    )
                if not boxes:
                    stats.skipped_no_box += 1
                    continue

            image_id = defect.defect_id
            shutil.copy(source, images_dir / f"{image_id}.jpg")

            width, height = _image_size(source)
            survey_id = defect.survey_ids[0] if defect.survey_ids else "unknown"
            records.append(
                {
                    "image_id": image_id,
                    "file_name": f"{image_id}.jpg",
                    "width": width,
                    "height": height,
                    "country": "Armenia",
                    "index": len(records),
                    "survey_id": survey_id,
                    "boxes": boxes,
                    "source_status": defect.status.value,
                }
            )
            if survey_id not in stats.surveys:
                stats.surveys.append(survey_id)

            if defect.status is DefectStatus.VERIFIED:
                stats.positives += 1
                if _was_class_corrected(db, defect.defect_id):
                    stats.corrected += 1
            else:
                stats.negatives += 1

    with (output_dir / "annotations.jsonl").open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps({k: v for k, v in record.items() if k != "survey_id"}) + "\n")

    splits = _survey_disjoint_splits(records)
    (output_dir / "splits.json").write_text(json.dumps(splits, indent=2), encoding="utf-8")

    manifest = {
        "schema_version": DATASET_SCHEMA_VERSION,
        "name": name or f"armenia_reviewed_{datetime.now(UTC):%Y%m%d}",
        "source": "RoadEye human review",
        "source_db": str(db_path),
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        # Ours, unlike RDD2022 — which is the point of building this dataset at all.
        "license": "Proprietary (RoadEye)",
        "license_notes": (
            "Derived from RoadEye surveys and human review. Not encumbered by the "
            "RDD2022 licence dispute; a model trained solely on this data is "
            "distributable. See docs/LICENSE_AUDIT.md BLOCKING-1."
        ),
        "distribution_allowed": True,
        "image_count": len(records),
        "annotation_count": sum(len(r["boxes"]) for r in records),
        "negative_image_count": stats.negatives,
        "corrected_count": stats.corrected,
        "surveys": stats.surveys,
        "split_strategy": "survey-disjoint (ADR-008)",
        "split_sizes": {k: len(v) for k, v in splits.items()},
        "skipped": {
            "no_evidence_image": stats.skipped_no_image,
            "unreviewed": stats.skipped_unreviewed,
            "no_bounding_box": stats.skipped_no_box,
        },
        "content_hash": _content_hash(records),
    }
    if len(stats.surveys) < 2:
        manifest["warning"] = (
            "Only one survey. Everything is in the train split — a single drive cannot "
            "produce an honest held-out set. Collect more routes before reporting any "
            "evaluation metric from this dataset."
        )
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return stats


def _was_class_corrected(db: Database, defect_id: str) -> bool:
    return any(
        row["action"] == ReviewAction.CHANGE_CLASS.value for row in db.reviews_for(defect_id)
    )


def _image_size(path: Path) -> tuple[int, int]:
    try:
        from PIL import Image

        with Image.open(path) as handle:
            return handle.width, handle.height
    except Exception:  # noqa: BLE001 - a missing size must not abort an export
        return 0, 0


def _content_hash(records: list[dict[str, Any]]) -> str:
    digest = hashlib.blake2b(digest_size=16)
    for record in sorted(records, key=lambda r: r["image_id"]):
        digest.update(record["image_id"].encode())
        for box in record["boxes"]:
            digest.update(
                f"{box['damage_class']}{box['xmin']},{box['ymin']},"
                f"{box['xmax']},{box['ymax']}".encode()
            )
    return digest.hexdigest()
