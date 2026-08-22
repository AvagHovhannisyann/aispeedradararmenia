"""Redaction where it actually has to hold: the files written to disk.

`test_privacy.py` covers the primitive. This covers the integration, which is where the
mistake would really be made — redacting the picture a human looks at, and forgetting the
one that feeds training.
"""

from __future__ import annotations

import pytest

np = pytest.importorskip("numpy", reason="numpy not installed")
pytest.importorskip("PIL", reason="Pillow not installed")

from PIL import Image  # noqa: E402

from roadeye.domain.models import BoundingBox  # noqa: E402
from roadeye.privacy.anonymizer import Anonymizer  # noqa: E402
from roadeye.privacy.base import RedactionError, Region, RegionKind  # noqa: E402
from roadeye.privacy.detectors import ScriptedRegionDetector  # noqa: E402
from roadeye.reporting.evidence import save_defect_evidence  # noqa: E402

#: A person standing well away from the defect, so a crop centred on the defect does not
#: happen to exclude them for the wrong reason.
PERSON = Region(x1=10, y1=10, x2=90, y2=110, kind=RegionKind.PERSON, confidence=0.95)

#: The defect, far from the person, near the bottom-right.
DEFECT_BOX = BoundingBox(x1=240, y1=150, x2=290, y2=190)


def noisy(seed: int = 3):
    return np.random.default_rng(seed).integers(0, 256, (220, 320, 3), dtype="uint8")


def colours_in(path) -> int:
    with Image.open(path) as image:
        array = np.asarray(image.convert("RGB"))
    return len(np.unique(array.reshape(-1, 3), axis=0))


class TestEveryDerivedFileIsRedacted:
    def test_the_training_frame_is_redacted(self, tmp_path):
        """The single most important assertion in this file.

        ``_frame.jpg`` is the one the dataset export copies, so an unredacted frame here
        does not merely sit on a laptop — it is packaged into a training set and is
        exactly the artefact intended to leave the machine.
        """
        anonymizer = Anonymizer(ScriptedRegionDetector([PERSON]))
        paths = save_defect_evidence("d1", noisy(), DEFECT_BOX, tmp_path, anonymizer=anonymizer)
        assert paths is not None

        with Image.open(tmp_path / paths.frame) as image:
            array = np.asarray(image.convert("RGB"))
        # Sample well inside the person box, clear of JPEG ringing at its edges.
        patch = array[30:90, 30:70]
        assert len(np.unique(patch.reshape(-1, 3), axis=0)) < 400, (
            "the training frame still carries the person's pixels"
        )

    def test_the_context_image_is_redacted(self, tmp_path):
        anonymizer = Anonymizer(ScriptedRegionDetector([PERSON]))
        paths = save_defect_evidence("d1", noisy(), DEFECT_BOX, tmp_path, anonymizer=anonymizer)
        assert paths is not None
        with Image.open(tmp_path / paths.context) as image:
            array = np.asarray(image.convert("RGB"))
        assert len(np.unique(array[30:90, 30:70].reshape(-1, 3), axis=0)) < 400

    def test_redaction_happens_once_before_anything_is_derived(self, tmp_path):
        """Redacting each output separately would be three chances to forget one. The
        observable consequence of doing it once, up front, is that the detector runs
        exactly once per defect."""
        calls = []

        class Counting(ScriptedRegionDetector):
            def find(self, image, *, width, height):
                calls.append((width, height))
                return super().find(image, width=width, height=height)

        anonymizer = Anonymizer(Counting([PERSON]))
        save_defect_evidence("d1", noisy(), DEFECT_BOX, tmp_path, anonymizer=anonymizer)
        assert len(calls) == 1
        assert calls[0] == (320, 220), "the detector must see the full frame, not a crop"

    def test_the_report_comes_back_with_the_paths(self, tmp_path):
        anonymizer = Anonymizer(ScriptedRegionDetector([PERSON], detector_id="scripted-v2"))
        paths = save_defect_evidence("d1", noisy(), DEFECT_BOX, tmp_path, anonymizer=anonymizer)
        assert paths is not None and paths.redaction is not None
        assert paths.redaction.detector_id == "scripted-v2"
        assert paths.redaction.region_count == 1


class TestFailureBehaviour:
    def test_a_broken_redactor_writes_nothing_at_all(self, tmp_path):
        """Not 'writes an unredacted image', and not 'returns None and carries on'. A
        broken redactor affects every image in the run, so it stops the run."""

        class Broken:
            @property
            def detector_id(self) -> str:
                return "broken"

            def find(self, image, *, width, height):
                raise RuntimeError("weights missing")

        with pytest.raises(RedactionError):
            save_defect_evidence(
                "d1", noisy(), DEFECT_BOX, tmp_path, anonymizer=Anonymizer(Broken())
            )
        assert list(tmp_path.glob("*.jpg")) == []

    def test_bad_pixels_still_only_lose_one_defect(self, tmp_path):
        """The other failure mode is unchanged: one unusable frame must not fail a
        survey that otherwise processed correctly."""
        assert save_defect_evidence("d1", None, DEFECT_BOX, tmp_path) is None
        assert save_defect_evidence("d2", np.zeros((5, 5)), DEFECT_BOX, tmp_path) is None


class TestWithoutAnAnonymizer:
    def test_images_are_written_unredacted(self, tmp_path):
        """Legitimate for local-only processing. The pipeline attaches a warning to the
        run when this happens, which is tested in the pipeline suite."""
        image = noisy()
        paths = save_defect_evidence("d1", image, DEFECT_BOX, tmp_path)
        assert paths is not None
        assert paths.redaction is None
        # JPEG is lossy, so this cannot be byte-exact; it can be nowhere near mosaicked.
        assert colours_in(tmp_path / paths.frame) > 5000
