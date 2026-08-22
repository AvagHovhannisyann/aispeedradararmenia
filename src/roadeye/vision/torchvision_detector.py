"""Real road-damage detector, backed by torchvision.

This is the first concrete implementation of :class:`~roadeye.vision.base.RoadDamageDetector`.
Everything above it — tracking, clustering, geolocation, storage, review — is unchanged
by its existence, which is the whole point of ADR-004.

**torchvision, not Ultralytics**, because Ultralytics is AGPL-3.0 and that licence
covers the models its training code produces, with a network clause that reaches a
hosted municipal dashboard (ADR-009). torchvision is BSD-3-Clause and installs cleanly
on a CPU-only machine, which is what a zero-budget founder actually has.

torch is an **optional dependency**. Importing this module without it raises a clear
`DetectorError` at construction, never an ImportError at startup — the core pipeline
and the whole test suite must keep running on a machine with no ML stack installed.

    pip install -e '.[vision]'
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from roadeye.domain.enums import DamageClass
from roadeye.vision.base import DetectorError, FrameImage, RawDetection

#: Model output index -> class. Index 0 is torchvision's reserved background class, so
#: real classes start at 1. Getting this off by one silently relabels every detection,
#: so the mapping lives here as data rather than being reconstructed at each call site.
DEFAULT_CLASS_ORDER: tuple[DamageClass, ...] = (
    DamageClass.LONGITUDINAL_CRACK,
    DamageClass.TRANSVERSE_CRACK,
    DamageClass.ALLIGATOR_CRACK,
    DamageClass.POTHOLE,
)


def build_model(num_classes: int, *, pretrained_backbone: bool = True) -> Any:
    """Construct a Faster R-CNN with a MobileNetV3 backbone.

    Chosen over a ResNet-50 backbone deliberately: it trains in a fraction of the time
    on CPU, and the founder has no GPU. The architecture is a placeholder for the
    eventual shipping model (RTMDet remains the candidate) — the point of the Protocol
    is that swapping it costs one adapter.

    ``num_classes`` **includes** the background class.
    """
    try:
        import torchvision
        from torchvision.models.detection.faster_rcnn import FastRCNNPredictor
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise DetectorError(
            "torchvision is required. Install with: pip install -e '.[vision]'"
        ) from exc

    weights = "DEFAULT" if pretrained_backbone else None
    model = torchvision.models.detection.fasterrcnn_mobilenet_v3_large_fpn(
        weights=None, weights_backbone=weights
    )
    in_features = model.roi_heads.box_predictor.cls_score.in_features
    model.roi_heads.box_predictor = FastRCNNPredictor(in_features, num_classes)
    return model


class TorchvisionDetector:
    """Runs a trained torchvision detection model behind the domain Protocol.

    Framework types never escape: :meth:`predict` takes a :class:`FrameImage` and
    returns plain :class:`RawDetection` objects. No tensor reaches a domain model.
    """

    def __init__(
        self,
        weights_path: str | Path,
        *,
        model_id: str | None = None,
        classes: Sequence[DamageClass] = DEFAULT_CLASS_ORDER,
        score_threshold: float = 0.3,
        device: str = "cpu",
    ) -> None:
        try:
            import torch
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise DetectorError(
                "torch is required. Install with: pip install -e '.[vision]'"
            ) from exc

        self._torch = torch
        self.weights_path = Path(weights_path)
        if not self.weights_path.exists():
            raise DetectorError(f"weights not found: {self.weights_path}")

        self._classes = tuple(classes)
        self.score_threshold = score_threshold
        self.device = torch.device(device)

        checkpoint = torch.load(self.weights_path, map_location=self.device, weights_only=False)
        state_dict = checkpoint.get("model_state_dict", checkpoint)

        # Class order is stored *with* the weights when available. Relying on the
        # caller to remember it is how a model silently starts reporting potholes as
        # transverse cracks after a retrain with reordered classes.
        saved = checkpoint.get("classes")
        if saved:
            try:
                self._classes = tuple(DamageClass(c) for c in saved)
            except ValueError as exc:
                raise DetectorError(f"checkpoint has unrecognised classes: {saved}") from exc

        self._model = build_model(len(self._classes) + 1, pretrained_backbone=False)
        try:
            self._model.load_state_dict(state_dict)
        except (RuntimeError, KeyError) as exc:
            raise DetectorError(f"weights do not match the model architecture: {exc}") from exc
        self._model.to(self.device).eval()

        # torchvision applies its OWN score threshold (default 0.05) inside roi_heads,
        # before anything this class sees. Left alone it is a second, hidden threshold:
        # a caller asking for score_threshold=0.01 would silently still get 0.05-filtered
        # results and conclude the model found nothing.
        #
        # This was not hypothetical — it masked an undertrained model whose top score
        # was 0.037, making it look like the detector returned nothing at all rather
        # than returning weak detections. Point both at the same number so the
        # threshold the caller sets is the threshold that applies.
        self._model.roi_heads.score_thresh = min(
            self._model.roi_heads.score_thresh, score_threshold
        )

        self._model_id = model_id or checkpoint.get("model_id") or self.weights_path.stem

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def classes(self) -> Sequence[DamageClass]:
        return self._classes

    def predict(self, frame: FrameImage) -> list[RawDetection]:
        """Detect road damage in one frame.

        Returns an empty list rather than raising when the frame carries no pixels —
        a synthetic frame source is a legitimate caller, and a 30-minute survey should
        not die because one frame failed to decode.
        """
        pixels = frame.pixels
        if pixels is None:
            return []

        torch = self._torch
        try:
            import numpy as np
        except ImportError as exc:  # pragma: no cover
            raise DetectorError("numpy is required for inference") from exc

        array = np.asarray(pixels)
        if array.ndim != 3 or array.shape[2] != 3:
            return []

        # HWC uint8 -> CHW float in [0, 1], which is what torchvision detectors expect.
        tensor = torch.from_numpy(np.ascontiguousarray(array)).permute(2, 0, 1)
        if tensor.dtype != torch.float32:
            tensor = tensor.float() / 255.0
        tensor = tensor.to(self.device)

        with torch.inference_mode():
            outputs = self._model([tensor])[0]

        results: list[RawDetection] = []
        for box, label, score in zip(
            outputs["boxes"].cpu().tolist(),
            outputs["labels"].cpu().tolist(),
            outputs["scores"].cpu().tolist(),
            strict=True,
        ):
            if score < self.score_threshold:
                continue
            # Label 0 is background; real classes are 1-indexed.
            index = int(label) - 1
            if not 0 <= index < len(self._classes):
                continue
            x1, y1, x2, y2 = box
            if x2 <= x1 or y2 <= y1:
                continue
            results.append(
                RawDetection(
                    damage_class=self._classes[index],
                    confidence=float(score),
                    x1=float(x1),
                    y1=float(y1),
                    x2=float(x2),
                    y2=float(y2),
                )
            )
        return results

    @classmethod
    def from_registry(cls, model_dir: str | Path, **kwargs: Any) -> TorchvisionDetector:
        """Load a model from a registry directory containing ``metadata.json``.

        Refuses a model whose metadata does not exist, because a detector without
        recorded provenance cannot be traced from a defect back to its training data —
        which is the auditability claim the whole system rests on.
        """
        model_dir = Path(model_dir)
        metadata_path = model_dir / "metadata.json"
        if not metadata_path.exists():
            raise DetectorError(
                f"no metadata.json in {model_dir}; a model without provenance may not "
                "be used (see docs/ML_STRATEGY.md)"
            )
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        weights = model_dir / metadata.get("weights_file", "weights.pt")
        return cls(weights, model_id=metadata.get("model_id"), **kwargs)
