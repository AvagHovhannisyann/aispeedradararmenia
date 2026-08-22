"""Tests for the torchvision detector adapter.

Split deliberately in two:

* **Contract tests** run everywhere. They assert the things that must hold whether or
  not torch is installed — chiefly that a missing ML stack produces a clear
  ``DetectorError`` and never an ImportError at module import time. The core pipeline
  has to keep working on a machine with no ML dependencies at all.
* **Model tests** are marked ``requires_torch`` and skip when torch is absent.
"""

from __future__ import annotations

import json

import pytest

from roadeye.domain.enums import DamageClass
from roadeye.vision.base import DetectorError, FrameImage, RoadDamageDetector
from roadeye.vision.torchvision_detector import DEFAULT_CLASS_ORDER, TorchvisionDetector


def _has_torch() -> bool:
    try:
        import torch  # noqa: F401

        return True
    except ImportError:
        return False


requires_torch = pytest.mark.skipif(not _has_torch(), reason="torch not installed")


class TestImportSafety:
    def test_module_imports_without_torch_installed(self):
        """Importing the adapter must never require torch. If it did, the core
        pipeline would stop being runnable on a bare machine."""
        import roadeye.vision.torchvision_detector as module

        assert module.DEFAULT_CLASS_ORDER

    def test_class_order_covers_every_damage_class(self):
        """The model's label indices are positional. A class missing here silently
        relabels detections — potholes reported as cracks."""
        assert set(DEFAULT_CLASS_ORDER) == set(DamageClass)
        assert len(DEFAULT_CLASS_ORDER) == 4

    def test_missing_weights_raises_detector_error(self, tmp_path):
        with pytest.raises(DetectorError, match="weights not found"):
            TorchvisionDetector(tmp_path / "nope.pt")

    def test_registry_requires_metadata(self, tmp_path):
        """A model without provenance may not be used: a defect must be traceable to
        the model that produced it (docs/ML_STRATEGY.md)."""
        (tmp_path / "weights.pt").write_bytes(b"stub")
        with pytest.raises(DetectorError, match="metadata"):
            TorchvisionDetector.from_registry(tmp_path)


@pytest.fixture(scope="module")
def tiny_model_dir(tmp_path_factory):
    """An untrained model saved in the registry layout.

    Untrained on purpose: these tests check the *adapter*, not detection quality.
    Quality is measured by ml/evaluation/evaluate.py against a held-out split.
    Built once per module because constructing the network is the slow part.
    """
    torch = pytest.importorskip("torch")

    from roadeye.vision.torchvision_detector import build_model

    path = tmp_path_factory.mktemp("model")
    model = build_model(len(DEFAULT_CLASS_ORDER) + 1, pretrained_backbone=False)
    torch.save(
        {
            "model_state_dict": model.state_dict(),
            "classes": [c.value for c in DEFAULT_CLASS_ORDER],
            "model_id": "test-model-v0",
        },
        path / "weights.pt",
    )
    (path / "metadata.json").write_text(
        json.dumps(
            {
                "model_id": "test-model-v0",
                "weights_file": "weights.pt",
                "distribution_allowed": False,
            }
        ),
        encoding="utf-8",
    )
    return path


@requires_torch
class TestModel:
    def test_build_model_has_correct_head(self):
        from roadeye.vision.torchvision_detector import build_model

        model = build_model(len(DEFAULT_CLASS_ORDER) + 1, pretrained_backbone=False)
        # 4 damage classes + background.
        assert model.roi_heads.box_predictor.cls_score.out_features == 5

    def test_satisfies_the_protocol(self, tiny_model_dir):
        detector = TorchvisionDetector.from_registry(tiny_model_dir)
        assert isinstance(detector, RoadDamageDetector)
        assert detector.model_id == "test-model-v0"
        assert set(detector.classes) == set(DamageClass)

    def test_predict_returns_domain_types_only(self, tiny_model_dir):
        """No tensor may cross the seam into the domain (ADR-004)."""
        import numpy as np

        detector = TorchvisionDetector.from_registry(tiny_model_dir)
        pixels = np.zeros((64, 64, 3), dtype=np.uint8)
        frame = FrameImage(frame_id="f1", width=64, height=64, pixels=pixels)

        for det in detector.predict(frame):
            assert isinstance(det.damage_class, DamageClass)
            assert isinstance(det.confidence, float)
            assert isinstance(det.x1, float)
            assert det.x2 > det.x1 and det.y2 > det.y1

    def test_frame_without_pixels_yields_nothing(self, tiny_model_dir):
        """The synthetic frame source is a legitimate caller; it must not crash."""
        detector = TorchvisionDetector.from_registry(tiny_model_dir)
        assert detector.predict(FrameImage(frame_id="f", width=64, height=64)) == []

    def test_greyscale_frame_is_rejected_gracefully(self, tiny_model_dir):
        import numpy as np

        detector = TorchvisionDetector.from_registry(tiny_model_dir)
        frame = FrameImage(
            frame_id="f", width=64, height=64, pixels=np.zeros((64, 64), dtype=np.uint8)
        )
        assert detector.predict(frame) == []

    def test_class_order_is_read_from_the_checkpoint(self, tiny_model_dir):
        """Class order travels with the weights so a retrain that reorders classes
        cannot silently relabel every detection."""
        detector = TorchvisionDetector.from_registry(tiny_model_dir)
        assert list(detector.classes) == list(DEFAULT_CLASS_ORDER)

    def test_score_threshold_filters(self, tiny_model_dir):
        import numpy as np

        strict = TorchvisionDetector.from_registry(tiny_model_dir, score_threshold=0.99)
        pixels = np.random.randint(0, 255, (96, 96, 3), dtype=np.uint8)
        frame = FrameImage(frame_id="f", width=96, height=96, pixels=pixels)
        assert all(d.confidence >= 0.99 for d in strict.predict(frame))

    def test_mismatched_architecture_raises(self, tmp_path):
        import torch

        torch.save({"model_state_dict": {"bogus.weight": torch.zeros(3)}}, tmp_path / "w.pt")
        with pytest.raises(DetectorError, match="do not match"):
            TorchvisionDetector(tmp_path / "w.pt")
