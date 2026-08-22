"""Evaluate a detector honestly.

    python3 ml/evaluation/evaluate.py --model models/road_damage/rdd_bootstrap_v001 \
        --dataset data/datasets/rdd2022_czech --split test

Reports precision, recall, F1 and AP per class at a given IoU, plus the counts behind
them. Two things it deliberately will not do:

* **It will not evaluate on the training split.** Doing so measures memorisation. The
  request is refused rather than warned about, because a number that reaches a slide
  deck is never accompanied by the warning that produced it.
* **It will not hide the denominator.** Precision without the number of predictions,
  or recall without the number of ground-truth boxes, is unreadable — and small
  denominators are exactly where misleading percentages come from. A class with 3
  instances gets its 3 shown next to its score.

For calibration: the best multi-country ensemble at CRDDC'2022 reached roughly
**F1 0.76**. Road damage detection is hard. A first bootstrap model scoring far below
that is a normal starting point, not a broken pipeline.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "ml" / "training"))

from roadeye.domain.enums import DamageClass  # noqa: E402


def iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    """Intersection over union of two ``(x1, y1, x2, y2)`` boxes."""
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    inter = (ix2 - ix1) * (iy2 - iy1)
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


@dataclass
class ClassResult:
    """Per-class counts and the scores derived from them."""

    damage_class: str
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    #: (confidence, is_true_positive), for average precision.
    scored: list[tuple[float, int]] = field(default_factory=list)

    @property
    def predictions(self) -> int:
        return self.true_positives + self.false_positives

    @property
    def ground_truth(self) -> int:
        return self.true_positives + self.false_negatives

    @property
    def precision(self) -> float | None:
        return self.true_positives / self.predictions if self.predictions else None

    @property
    def recall(self) -> float | None:
        return self.true_positives / self.ground_truth if self.ground_truth else None

    @property
    def f1(self) -> float | None:
        p, r = self.precision, self.recall
        if p is None or r is None or p + r == 0:
            return None
        return 2 * p * r / (p + r)

    def average_precision(self) -> float | None:
        """Area under the precision-recall curve (all-points interpolation)."""
        if not self.ground_truth:
            return None
        ordered = sorted(self.scored, key=lambda s: s[0], reverse=True)
        tp = fp = 0
        points: list[tuple[float, float]] = []
        for _, is_tp in ordered:
            tp += is_tp
            fp += 1 - is_tp
            points.append((tp / self.ground_truth, tp / (tp + fp)))
        if not points:
            return 0.0
        # Make precision monotonically decreasing in recall, then integrate.
        best = 0.0
        for i in range(len(points) - 1, -1, -1):
            best = max(best, points[i][1])
            points[i] = (points[i][0], best)
        ap = 0.0
        previous_recall = 0.0
        for recall, precision in points:
            ap += (recall - previous_recall) * precision
            previous_recall = recall
        return ap

    def as_dict(self) -> dict[str, Any]:
        def rounded(v: float | None) -> float | None:
            return round(v, 4) if v is not None else None

        return {
            "class": self.damage_class,
            "ground_truth": self.ground_truth,
            "predictions": self.predictions,
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "false_negatives": self.false_negatives,
            "precision": rounded(self.precision),
            "recall": rounded(self.recall),
            "f1": rounded(self.f1),
            "average_precision": rounded(self.average_precision()),
        }


def match_frame(
    predictions: list[dict[str, Any]],
    ground_truth: list[dict[str, Any]],
    results: dict[str, ClassResult],
    *,
    iou_threshold: float,
) -> None:
    """Greedily match one image's predictions to its ground truth.

    Highest-confidence predictions claim boxes first, and each ground-truth box can be
    matched at most once — so two overlapping predictions of the same defect count as
    one hit and one false positive, which is the behaviour a municipality would expect.
    """
    unmatched = {c: list(range(len(ground_truth))) for c in {g["class"] for g in ground_truth}}
    used: set[int] = set()

    for pred in sorted(predictions, key=lambda p: p["score"], reverse=True):
        cls = pred["class"]
        result = results.setdefault(cls, ClassResult(cls))

        best_index, best_iou = -1, 0.0
        for gt_index in unmatched.get(cls, []):
            if gt_index in used:
                continue
            gt = ground_truth[gt_index]
            if gt["class"] != cls:
                continue
            score = iou(pred["box"], gt["box"])
            if score > best_iou:
                best_index, best_iou = gt_index, score

        if best_index >= 0 and best_iou >= iou_threshold:
            used.add(best_index)
            result.true_positives += 1
            result.scored.append((pred["score"], 1))
        else:
            result.false_positives += 1
            result.scored.append((pred["score"], 0))

    for index, gt in enumerate(ground_truth):
        if index not in used:
            result = results.setdefault(gt["class"], ClassResult(gt["class"]))
            result.false_negatives += 1


def evaluate(
    model_dir: Path,
    dataset_dir: Path,
    *,
    split: str = "test",
    iou_threshold: float = 0.5,
    score_threshold: float = 0.3,
    max_size: int = 384,
    limit: int | None = None,
) -> dict[str, Any]:
    if split == "train":
        raise SystemExit(
            "refusing to evaluate on the training split — that measures memorisation, "
            "not generalisation (see docs/ML_STRATEGY.md). Use --split val or test."
        )

    from train import RoadDamageDataset  # noqa: PLC0415 - path-injected module

    from roadeye.vision.base import FrameImage
    from roadeye.vision.torchvision_detector import TorchvisionDetector

    detector = TorchvisionDetector.from_registry(
        model_dir, score_threshold=score_threshold, device="cpu"
    )
    dataset = RoadDamageDataset(dataset_dir, split, max_size=max_size)
    if len(dataset) == 0:
        raise SystemExit(f"split {split!r} is empty")

    import numpy as np

    results: dict[str, ClassResult] = {}
    images_seen = 0
    total_predictions = 0

    count = len(dataset) if limit is None else min(limit, len(dataset))
    for i in range(count):
        tensor, target = dataset[i]
        array = (tensor.permute(1, 2, 0).numpy() * 255).astype(np.uint8)
        frame = FrameImage(
            frame_id=f"{split}-{i}", width=array.shape[1], height=array.shape[0], pixels=array
        )

        predictions = [
            {"class": d.damage_class.value, "score": d.confidence, "box": (d.x1, d.y1, d.x2, d.y2)}
            for d in detector.predict(frame)
        ]
        label_to_class = {i + 1: c.value for i, c in enumerate(DamageClass)}
        ground_truth = [
            {"class": label_to_class[int(label)], "box": tuple(float(v) for v in box)}
            for box, label in zip(target["boxes"].tolist(), target["labels"].tolist(), strict=True)
        ]

        match_frame(predictions, ground_truth, results, iou_threshold=iou_threshold)
        images_seen += 1
        total_predictions += len(predictions)
        if images_seen % 25 == 0:
            print(f"  {images_seen}/{count} images", flush=True)

    per_class = [results[c].as_dict() for c in sorted(results)]
    macro = [r["f1"] for r in per_class if r["f1"] is not None]
    maps = [r["average_precision"] for r in per_class if r["average_precision"] is not None]

    metadata_path = model_dir / "metadata.json"
    metadata = (
        json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
    )

    return {
        "model_id": metadata.get("model_id", model_dir.name),
        "model_dir": str(model_dir),
        "dataset": str(dataset_dir),
        "dataset_content_hash": metadata.get("dataset_content_hash"),
        "split": split,
        "images": images_seen,
        "iou_threshold": iou_threshold,
        "score_threshold": score_threshold,
        "total_predictions": total_predictions,
        "total_ground_truth": sum(r["ground_truth"] for r in per_class),
        "per_class": per_class,
        "macro_f1": round(sum(macro) / len(macro), 4) if macro else None,
        f"mAP@{iou_threshold}": round(sum(maps) / len(maps), 4) if maps else None,
        "reference": (
            "Best multi-country ensemble at CRDDC'2022 reached roughly F1 0.76. "
            "This is a hard problem; treat that as the bar, not 0.99."
        ),
        "caveat": (
            "Trained and evaluated on non-Armenian roads. Cross-country performance "
            "degrades substantially; these numbers do NOT predict Yerevan performance."
        ),
    }


def format_report(report: dict[str, Any]) -> str:
    lines = [
        "",
        f"Model    {report['model_id']}",
        f"Split    {report['split']}  ({report['images']} images)",
        f"IoU      {report['iou_threshold']}   score threshold {report['score_threshold']}",
        "",
        f"{'class':<22}{'GT':>6}{'pred':>7}{'TP':>6}{'FP':>6}{'FN':>6}"
        f"{'prec':>8}{'rec':>8}{'F1':>8}{'AP':>8}",
        "-" * 85,
    ]

    def cell(v: float | None) -> str:
        return f"{v:>8.3f}" if isinstance(v, float) else f"{'-':>8}"

    for row in report["per_class"]:
        lines.append(
            f"{row['class']:<22}{row['ground_truth']:>6}{row['predictions']:>7}"
            f"{row['true_positives']:>6}{row['false_positives']:>6}{row['false_negatives']:>6}"
            + cell(row["precision"])
            + cell(row["recall"])
            + cell(row["f1"])
            + cell(row["average_precision"])
        )
    lines += [
        "-" * 85,
        f"macro F1 {report['macro_f1']}    mAP@{report['iou_threshold']} "
        f"{report.get('mAP@' + str(report['iou_threshold']))}",
        "",
        report["reference"],
        report["caveat"],
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate a RoadEye detector.")
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--split", default="test", choices=["val", "test"])
    parser.add_argument("--iou", type=float, default=0.5)
    parser.add_argument("--score-threshold", type=float, default=0.3)
    parser.add_argument("--max-size", type=int, default=384)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()

    report = evaluate(
        args.model,
        args.dataset,
        split=args.split,
        iou_threshold=args.iou,
        score_threshold=args.score_threshold,
        max_size=args.max_size,
        limit=args.limit,
    )
    print(format_report(report))

    out = args.json or (args.model / f"evaluation_{args.split}.json")
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
