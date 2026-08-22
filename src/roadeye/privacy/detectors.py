"""Detectors that find people and vehicles so they can be destroyed.

## Why people and vehicles, and not faces and plates

The obvious design is a face detector plus a plate detector. Both are rejected.

**Plates.** Every well-maintained open plate detector is YOLO-based, and Ultralytics is
AGPL-3.0 with a network clause that reaches a hosted municipal dashboard (ADR-009).
Beyond the licence, localising a plate is the first half of ALPR, and the half that is
useful for nothing else. Detecting the *vehicle* covers the plate without the system
ever having represented one — which is a stronger guarantee than a policy saying we do
not read them.

**Faces.** A face detector fails exactly where windscreen video needs it: someone facing
away, a head at 30 px, a figure behind a windscreen reflection. A person detector fires
on all three, and a box around a person contains that person's face by construction.

This is also what the commercial precedent does — Vaisala's RoadAI masks vehicles and
people, not plates and faces (``docs/PRIVACY.md``).

## The licence position

torchvision is BSD-3-Clause and already an optional dependency, so this adds nothing new
to audit on the code side. The COCO-pretrained checkpoint is a different question, and
an open one (L-6 in ``docs/LICENSE_AUDIT.md``): COCO annotations are CC BY 4.0 and its
images are subject to Flickr terms.

That question does not block this use. The checkpoint is used **locally, to destroy
data**, and is never redistributed, never shipped inside a product, and never used to
produce a defect that reaches a customer. Should L-6 resolve badly, the remedy is to
swap the detector behind this Protocol — which is why there is a Protocol.
"""

from __future__ import annotations

from typing import Any

from roadeye.privacy.base import RedactionError, Region, RegionKind

#: COCO categories that map onto something we must destroy. The mapping is deliberately
#: small: this is a redaction detector, not a scene-understanding model, and every extra
#: class is another chance to blur a pothole.
COCO_REDACTION_CLASSES: dict[str, RegionKind] = {
    "person": RegionKind.PERSON,
    "bicycle": RegionKind.VEHICLE,
    "car": RegionKind.VEHICLE,
    "motorcycle": RegionKind.VEHICLE,
    "bus": RegionKind.VEHICLE,
    "train": RegionKind.VEHICLE,
    "truck": RegionKind.VEHICLE,
}


class ScriptedRegionDetector:
    """Returns pre-set regions. For tests, and for reproducing a specific failure.

    The redaction properties worth testing — irreversibility, margins, fail-closed
    behaviour — are properties of the *pipeline*, not of any model's accuracy. Testing
    them against a scripted detector keeps the suite free of a 70 MB download and lets
    it assert exact pixel outcomes.
    """

    def __init__(self, regions: list[Region], *, detector_id: str = "scripted") -> None:
        self._regions = list(regions)
        self._detector_id = detector_id

    @property
    def detector_id(self) -> str:
        return self._detector_id

    def find(self, image: Any, *, width: int, height: int) -> list[Region]:
        return list(self._regions)


class NullRegionDetector:
    """Finds nothing at all.

    Exists to make a dangerous configuration explicit. Passing this says "I have decided
    these images need no redaction", which is auditable, unlike a missing detector that
    silently degraded into writing everything unredacted.
    """

    @property
    def detector_id(self) -> str:
        return "null"

    def find(self, image: Any, *, width: int, height: int) -> list[Region]:
        return []


class TorchvisionPersonVehicleDetector:
    """COCO-pretrained detector, restricted to people and vehicles.

    Runs on CPU. Evidence images number in the hundreds per survey rather than the tens
    of thousands, so a second CNN pass over them is seconds of work, not a reason to
    reach for a GPU.
    """

    def __init__(
        self,
        *,
        score_threshold: float = 0.35,
        architecture: str = "fasterrcnn_mobilenet_v3_large_fpn",
        device: str = "cpu",
    ) -> None:
        try:
            import torch
            import torchvision.models.detection as detection
        except ImportError as exc:  # pragma: no cover - exercised by the extras matrix
            raise RedactionError(
                "redaction needs the 'vision' extra (torch, torchvision). Install it, or "
                "pass an explicit detector. Writing unredacted evidence is not an option "
                "— see docs/PRIVACY.md."
            ) from exc

        builder = getattr(detection, architecture, None)
        if builder is None:
            raise RedactionError(f"unknown torchvision detection architecture: {architecture!r}")

        weights_enum = detection.__dict__.get(_weights_enum_name(architecture))
        if weights_enum is None:
            raise RedactionError(f"no pretrained weights enum for {architecture!r}")
        weights = weights_enum.DEFAULT

        # A low threshold *inside* the model, so the adapter's own threshold is the one
        # that decides. torchvision applies box_score_thresh before the caller sees
        # anything, and a threshold that silently does nothing has bitten this codebase
        # before (see the M3 notes in docs/TRAINING.md).
        self._model = builder(weights=weights, box_score_thresh=0.01)
        self._model.eval()
        self._model.to(device)
        self._torch = torch
        self._device = device
        self._categories: list[str] = list(weights.meta["categories"])
        self._score_threshold = score_threshold
        self._architecture = architecture
        self._weights_name = str(weights)

    @property
    def detector_id(self) -> str:
        return f"{self._architecture}@{self._score_threshold:.2f}"

    @property
    def weights_origin(self) -> str:
        return self._weights_name

    def find(self, image: Any, *, width: int, height: int) -> list[Region]:
        import numpy as np

        array = np.asarray(image, dtype="uint8")
        if array.ndim != 3 or array.shape[2] != 3:
            raise ValueError(f"expected an HxWx3 uint8 image, got shape {array.shape}")

        # A copy, not a view. An array handed in from PIL is often read-only, and torch
        # warns that writing through such a tensor is undefined behaviour — a warning
        # worth heeding rather than silencing, since the array we are told not to write
        # to is the caller's original frame.
        tensor = (
            self._torch.from_numpy(np.array(array, dtype="uint8", copy=True))
            .permute(2, 0, 1)
            .float()
            .div(255.0)
            .to(self._device)
        )
        with self._torch.no_grad():
            output = self._model([tensor])[0]

        regions: list[Region] = []
        for box, label, score in zip(
            output["boxes"].cpu().numpy(),
            output["labels"].cpu().numpy(),
            output["scores"].cpu().numpy(),
            strict=True,
        ):
            confidence = float(score)
            if confidence < self._score_threshold:
                continue
            name = self._categories[int(label)] if int(label) < len(self._categories) else ""
            kind = COCO_REDACTION_CLASSES.get(name)
            if kind is None:
                continue
            x1, y1, x2, y2 = (float(v) for v in box)
            regions.append(
                Region(
                    x1=max(0.0, x1),
                    y1=max(0.0, y1),
                    x2=min(float(width), x2),
                    y2=min(float(height), y2),
                    kind=kind,
                    confidence=confidence,
                )
            )
        return regions


def _weights_enum_name(architecture: str) -> str:
    """torchvision's naming convention: ``fasterrcnn_x`` -> ``FasterRCNN_X_Weights``."""
    special = {
        "fasterrcnn_mobilenet_v3_large_fpn": "FasterRCNN_MobileNet_V3_Large_FPN_Weights",
        "fasterrcnn_mobilenet_v3_large_320_fpn": "FasterRCNN_MobileNet_V3_Large_320_FPN_Weights",
        "fasterrcnn_resnet50_fpn": "FasterRCNN_ResNet50_FPN_Weights",
        "fasterrcnn_resnet50_fpn_v2": "FasterRCNN_ResNet50_FPN_V2_Weights",
        "retinanet_resnet50_fpn": "RetinaNet_ResNet50_FPN_Weights",
        "ssdlite320_mobilenet_v3_large": "SSDLite320_MobileNet_V3_Large_Weights",
    }
    return special.get(architecture, "")
