"""Deterministic fake detectors for tests and pipeline development.

These exist so the entire pipeline — sampling, tracking, clustering, geolocation,
storage, export — can be developed and regression-tested on a laptop with **no GPU, no
model weights, no ffmpeg and no network**. That is not a testing convenience; it is
what lets the pipeline be correct before the neural network is good, which is the order
the milestones require.

Determinism is achieved with a seeded hash of the frame id, never ``random`` module
global state, so tests do not interfere with each other and results are reproducible
across processes and machines.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from roadeye.domain.enums import DamageClass
from roadeye.vision.base import FrameImage, RawDetection


def _stable_unit_float(*parts: object) -> float:
    """A deterministic float in [0, 1) derived from the given parts.

    Uses blake2b rather than :func:`hash`, because Python's string hashing is salted
    per-process and would make "deterministic" tests flaky across runs.
    """
    digest = hashlib.blake2b(
        "|".join(str(p) for p in parts).encode("utf-8"), digest_size=8
    ).digest()
    return int.from_bytes(digest, "big") / float(1 << 64)


class FakeDetector:
    """Emits pseudo-random but reproducible detections.

    Useful for exercising the plumbing at realistic volumes. Not useful for judging
    detection quality — it knows nothing about roads.
    """

    def __init__(
        self,
        *,
        model_id: str = "fake-detector-v1",
        detections_per_frame: int = 1,
        classes: Sequence[DamageClass] | None = None,
        min_confidence: float = 0.4,
        seed: str = "roadeye",
    ) -> None:
        self._model_id = model_id
        self._n = max(0, detections_per_frame)
        self._classes = tuple(classes) if classes else (DamageClass.POTHOLE,)
        self._min_conf = min_confidence
        self._seed = seed

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def classes(self) -> Sequence[DamageClass]:
        return self._classes

    def predict(self, frame: FrameImage) -> list[RawDetection]:
        out: list[RawDetection] = []
        for i in range(self._n):
            r1 = _stable_unit_float(self._seed, frame.frame_id, i, "x")
            r2 = _stable_unit_float(self._seed, frame.frame_id, i, "y")
            r3 = _stable_unit_float(self._seed, frame.frame_id, i, "c")
            r4 = _stable_unit_float(self._seed, frame.frame_id, i, "k")

            w = frame.width * 0.08
            h = frame.height * 0.06
            # Keep boxes in the lower 60% of the frame, where road surface actually is.
            x1 = r1 * (frame.width - w)
            y1 = frame.height * 0.4 + r2 * (frame.height * 0.6 - h)
            cls = self._classes[int(r4 * len(self._classes)) % len(self._classes)]
            conf = self._min_conf + r3 * (1.0 - self._min_conf)

            out.append(
                RawDetection(
                    damage_class=cls,
                    confidence=round(conf, 4),
                    x1=round(x1, 2),
                    y1=round(y1, 2),
                    x2=round(x1 + w, 2),
                    y2=round(y1 + h, 2),
                )
            )
        return out


class ScriptedDetector:
    """Returns exactly the detections it was told to, keyed by frame id.

    The workhorse for pipeline tests: it lets a test state "these three frames each show
    the same pothole drifting down the image" and then assert that tracking and
    clustering collapse them into exactly one defect. Frames with no script entry
    return no detections.
    """

    def __init__(
        self,
        script: dict[str, list[RawDetection]],
        *,
        model_id: str = "scripted-detector-v1",
        classes: Sequence[DamageClass] | None = None,
    ) -> None:
        self._script = script
        self._model_id = model_id
        self._classes = tuple(classes) if classes else tuple(DamageClass)

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def classes(self) -> Sequence[DamageClass]:
        return self._classes

    def predict(self, frame: FrameImage) -> list[RawDetection]:
        return list(self._script.get(frame.frame_id, []))


class NullDetector:
    """Finds nothing. Used to prove the pipeline handles empty surveys gracefully.

    A clean road is a legitimate and common result. A pipeline that crashes or produces
    a malformed report when a drive finds zero defects would fail on exactly the roads
    a municipality is proudest of.
    """

    @property
    def model_id(self) -> str:
        return "null-detector-v1"

    @property
    def classes(self) -> Sequence[DamageClass]:
        return ()

    def predict(self, frame: FrameImage) -> list[RawDetection]:
        return []
