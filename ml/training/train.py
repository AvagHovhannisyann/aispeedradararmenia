"""Train a road-damage detector, with the provenance a government product needs.

    python3 ml/training/train.py --dataset data/datasets/rdd2022_czech --epochs 10

Every run writes a complete record: git commit, dataset version and content hash,
architecture, hyperparameters, seed, hardware, and the **licences of the training
data**. There is deliberately no way to produce an unexplained ``best.pt`` — a defect
on a municipal map must be traceable to the model that found it and the data that
model learned from.

Two design points that are not decoration:

* **Checkpoint every epoch.** Free GPU services (Kaggle, Colab) evict sessions without
  warning; Colab explicitly guarantees nothing. A run that cannot resume is a run that
  loses a night's training.
* **``distribution_allowed`` is inherited from the dataset, not chosen.** RDD2022's
  licence is disputed, so anything trained on it is marked non-distributable
  automatically (``docs/LICENSE_AUDIT.md`` BLOCKING-1). Making that a manual flag would
  mean eventually forgetting it.
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "ml" / "datasets"))

from roadeye.vision.torchvision_detector import DEFAULT_CLASS_ORDER, build_model  # noqa: E402

#: Class -> model label. Index 0 is torchvision's background class.
CLASS_TO_LABEL = {cls.value: i + 1 for i, cls in enumerate(DEFAULT_CLASS_ORDER)}


def git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            cwd=REPO_ROOT,
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


class RoadDamageDataset:
    """Reads the normalized dataset format written by ``ml/datasets/rdd2022.py``."""

    def __init__(
        self,
        dataset_dir: str | Path,
        split: str,
        *,
        max_size: int = 512,
        drop_negatives: bool = False,
    ) -> None:
        import torch  # noqa: F401  (imported for the dependency check)

        self.root = Path(dataset_dir)
        self.images_dir = self.root / "images"
        self.max_size = max_size

        splits = json.loads((self.root / "splits.json").read_text(encoding="utf-8"))
        if split not in splits:
            raise ValueError(f"unknown split {split!r}; have {sorted(splits)}")
        wanted = set(splits[split])

        self.records: list[dict[str, Any]] = []
        with (self.root / "annotations.jsonl").open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                if record["image_id"] not in wanted:
                    continue
                if drop_negatives and not record["boxes"]:
                    continue
                if not (self.images_dir / f"{record['image_id']}.jpg").exists():
                    continue
                self.records.append(record)

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, index: int):
        import torch
        from PIL import Image

        record = self.records[index]
        path = self.images_dir / f"{record['image_id']}.jpg"
        image = Image.open(path).convert("RGB")

        # Uniform downscale keeps aspect ratio, so boxes scale by a single factor.
        # Distorting aspect ratio would teach the model shapes that do not occur.
        scale = min(self.max_size / image.width, self.max_size / image.height, 1.0)
        if scale < 1.0:
            image = image.resize(
                (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
                Image.Resampling.BILINEAR,
            )

        tensor = torch.from_numpy(_to_array(image)).permute(2, 0, 1).float() / 255.0

        boxes, labels = [], []
        for box in record["boxes"]:
            x1, y1 = box["xmin"] * scale, box["ymin"] * scale
            x2, y2 = box["xmax"] * scale, box["ymax"] * scale
            if x2 - x1 < 1.0 or y2 - y1 < 1.0:
                # Sub-pixel boxes after downscaling produce a zero-area target, which
                # makes the loss NaN several layers deep with an unhelpful message.
                continue
            boxes.append([x1, y1, x2, y2])
            labels.append(CLASS_TO_LABEL[box["damage_class"]])

        target = {
            "boxes": torch.as_tensor(boxes, dtype=torch.float32).reshape(-1, 4),
            "labels": torch.as_tensor(labels, dtype=torch.int64),
            "image_id": torch.tensor([index]),
        }
        return tensor, target


def _to_array(image):
    import numpy as np

    # np.array (not asarray) copies, giving torch a writable buffer. asarray hands
    # back PIL's read-only view, which torch accepts with a warning and undefined
    # write behaviour.
    return np.array(image, dtype="uint8")


def collate(batch):
    return tuple(zip(*batch, strict=True))


def train(
    dataset_dir: Path,
    output_dir: Path,
    *,
    epochs: int = 10,
    batch_size: int = 4,
    learning_rate: float = 0.005,
    max_size: int = 512,
    seed: int = 1337,
    device_name: str = "auto",
    limit_batches: int | None = None,
    resume: bool = True,
) -> dict[str, Any]:
    import torch
    from torch.utils.data import DataLoader

    random.seed(seed)
    torch.manual_seed(seed)

    if device_name == "auto":
        device_name = "cuda" if torch.cuda.is_available() else "cpu"
    device = torch.device(device_name)

    manifest_path = dataset_dir / "manifest.json"
    dataset_manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    )

    train_set = RoadDamageDataset(dataset_dir, "train", max_size=max_size)
    val_set = RoadDamageDataset(dataset_dir, "val", max_size=max_size)
    if len(train_set) == 0:
        raise SystemExit("training split is empty")

    # RoadDamageDataset deliberately does not subclass torch.utils.data.Dataset, because
    # torch is imported lazily so this module can be imported (and type-checked) without
    # it. DataLoader only needs __len__ and __getitem__ at runtime; its stubs ask for the
    # nominal base class, hence the cast.
    loader: DataLoader[Any] = DataLoader(
        cast("Any", train_set),
        batch_size=batch_size,
        shuffle=True,
        collate_fn=collate,
        num_workers=0,  # workers cost more than they save at this scale
    )

    model = build_model(len(DEFAULT_CLASS_ORDER) + 1, pretrained_backbone=True).to(device)
    params = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.SGD(params, lr=learning_rate, momentum=0.9, weight_decay=5e-4)

    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = output_dir / "checkpoint.pt"
    start_epoch = 0
    history: list[dict[str, Any]] = []

    # Resume support exists because free GPU sessions are evicted without warning.
    if resume and checkpoint_path.exists():
        state = torch.load(checkpoint_path, map_location=device, weights_only=False)
        model.load_state_dict(state["model_state_dict"])
        optimizer.load_state_dict(state["optimizer_state_dict"])
        start_epoch = state.get("epoch", 0)
        history = state.get("history", [])
        print(f"Resumed from epoch {start_epoch}")

    started = time.time()
    for epoch in range(start_epoch, epochs):
        model.train()
        epoch_loss = 0.0
        steps = 0
        epoch_start = time.time()

        for images, targets in loader:
            if limit_batches is not None and steps >= limit_batches:
                break
            images = [img.to(device) for img in images]
            targets = [{k: v.to(device) for k, v in t.items()} for t in targets]

            loss_dict = model(images, targets)
            loss = sum(loss_dict.values())
            if not torch.isfinite(loss):
                print(f"  non-finite loss at step {steps}, skipping batch")
                optimizer.zero_grad(set_to_none=True)
                continue

            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(params, max_norm=10.0)
            optimizer.step()

            epoch_loss += float(loss.detach())
            steps += 1
            if steps % 10 == 0:
                print(
                    f"  epoch {epoch + 1}/{epochs} step {steps}/{len(loader)} "
                    f"loss {epoch_loss / steps:.4f} ({time.time() - epoch_start:.0f}s)",
                    flush=True,
                )

        mean_loss = epoch_loss / max(1, steps)
        record = {
            "epoch": epoch + 1,
            "mean_loss": round(mean_loss, 5),
            "steps": steps,
            "seconds": round(time.time() - epoch_start, 1),
        }
        history.append(record)
        print(f"epoch {epoch + 1}: loss {mean_loss:.4f} in {record['seconds']}s", flush=True)

        torch.save(
            {
                "model_state_dict": model.state_dict(),
                "optimizer_state_dict": optimizer.state_dict(),
                "epoch": epoch + 1,
                "history": history,
                "classes": [c.value for c in DEFAULT_CLASS_ORDER],
            },
            checkpoint_path,
        )

    # Final weights, without the optimizer state, for inference.
    weights_path = output_dir / "weights.pt"
    torch.save(
        {
            "model_state_dict": model.state_dict(),
            "classes": [c.value for c in DEFAULT_CLASS_ORDER],
            "model_id": output_dir.name,
        },
        weights_path,
    )

    metadata = {
        "schema_version": 1,
        "model_id": output_dir.name,
        "name": output_dir.name,
        "architecture": "fasterrcnn_mobilenet_v3_large_fpn",
        "framework": "torchvision",
        "framework_version": _version("torchvision"),
        "torch_version": _version("torch"),
        "weights_file": "weights.pt",
        "weights_origin": "trained locally from an ImageNet-pretrained backbone",
        "backbone_weights_license": "BSD-3-Clause (torchvision); backbone pretrained on ImageNet",
        "classes": [c.value for c in DEFAULT_CLASS_ORDER],
        "dataset_dir": str(dataset_dir),
        "dataset_name": dataset_manifest.get("name"),
        "dataset_content_hash": dataset_manifest.get("content_hash"),
        "dataset_source": dataset_manifest.get("source"),
        "training_data_licenses": [
            dataset_manifest.get("license", "unknown"),
        ],
        "license_notes": dataset_manifest.get("license_notes"),
        # Inherited, never chosen: a model is only distributable if its data was.
        "distribution_allowed": bool(dataset_manifest.get("distribution_allowed", False)),
        "hyperparameters": {
            "epochs": epochs,
            "batch_size": batch_size,
            "learning_rate": learning_rate,
            "optimizer": "SGD(momentum=0.9, weight_decay=5e-4)",
            "max_image_size": max_size,
            "seed": seed,
            "limit_batches": limit_batches,
        },
        "train_images": len(train_set),
        "val_images": len(val_set),
        "device": device_name,
        "git_commit": git_commit(),
        "history": history,
        "total_seconds": round(time.time() - started, 1),
        "created_at": _utc_now(),
        "warning": (
            "Trained on non-Armenian data. Cross-country performance degrades "
            "substantially (see docs/ML_STRATEGY.md). Bootstrap only."
        ),
    }
    (output_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(f"\nWrote {weights_path} and metadata.json")
    if not metadata["distribution_allowed"]:
        print(
            "NOTE: distribution_allowed=false — this model inherits a disputed dataset "
            "licence and may NOT be shipped. See docs/LICENSE_AUDIT.md."
        )
    return metadata


def _version(module: str) -> str | None:
    try:
        return __import__(module).__version__
    except Exception:  # noqa: BLE001
        return None


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def main() -> int:
    parser = argparse.ArgumentParser(description="Train a RoadEye road-damage detector.")
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--lr", type=float, default=0.005)
    parser.add_argument("--max-size", type=int, default=512)
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--limit-batches", type=int, default=None)
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()

    output = args.output or REPO_ROOT / "models" / "road_damage" / f"{args.dataset.name}_v001"
    train(
        args.dataset,
        output,
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.lr,
        max_size=args.max_size,
        seed=args.seed,
        device_name=args.device,
        limit_batches=args.limit_batches,
        resume=not args.no_resume,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
