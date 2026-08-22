"""Video decoding, behind an interface.

ffmpeg is **not** present in every environment RoadEye must run in (it is absent from
the current development container), and forcing it into the core would make the whole
pipeline untestable there. So decoding is a capability, not an assumption:

* :class:`FrameSource` is the interface the pipeline depends on.
* :class:`SyntheticFrameSource` produces frames with no pixels — enough to exercise
  sampling, tracking, clustering, geolocation, storage and export end to end.
* :class:`PyAvFrameSource` is the real decoder, imported lazily so that a missing
  optional dependency is a clear error at construction, not an ImportError at startup.

Install the real decoder with ``pip install 'roadeye[video]'``.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from roadeye.vision.base import FrameImage


@dataclass(frozen=True, slots=True)
class VideoInfo:
    """What we need to know about a video before planning a sampling run."""

    duration_s: float
    width: int
    height: int
    fps: float | None = None
    frame_count: int | None = None


@runtime_checkable
class FrameSource(Protocol):
    """Yields images at requested video timestamps."""

    def info(self) -> VideoInfo:
        """Container metadata. Must not require decoding the whole file."""
        ...

    def frames_at(self, video_times_s: Sequence[float]) -> Iterator[tuple[float, FrameImage]]:
        """Yield ``(video_time_s, image)`` for each requested timestamp.

        Implementations should yield in ascending time order and may skip timestamps
        beyond the end of the file rather than raising — a sampling plan built from a
        slightly optimistic duration estimate is a normal occurrence, not an error.
        """
        ...


class SyntheticFrameSource:
    """A frame source with no pixels, for tests and pipeline development.

    Returns :class:`FrameImage` objects carrying identity and dimensions but
    ``pixels=None``. Paired with :class:`~roadeye.vision.fake.ScriptedDetector` this
    exercises every stage of the pipeline deterministically, on any machine, in
    milliseconds — which is what keeps the test suite honest about the parts of RoadEye
    that are *not* the neural network.
    """

    def __init__(
        self,
        *,
        duration_s: float,
        width: int = 1920,
        height: int = 1080,
        fps: float = 30.0,
        survey_id: str = "synthetic",
    ) -> None:
        self._info = VideoInfo(
            duration_s=duration_s,
            width=width,
            height=height,
            fps=fps,
            frame_count=int(duration_s * fps),
        )
        self._survey_id = survey_id

    def info(self) -> VideoInfo:
        return self._info

    def frames_at(self, video_times_s: Sequence[float]) -> Iterator[tuple[float, FrameImage]]:
        for t in video_times_s:
            if t > self._info.duration_s:
                continue
            yield (
                t,
                FrameImage(
                    frame_id=f"{self._survey_id}:f{t:.3f}",
                    width=self._info.width,
                    height=self._info.height,
                    pixels=None,
                ),
            )


class ImageSequenceFrameSource:
    """Treats a folder of images as if it were the survey video.

    Two reasons this exists rather than being a testing shortcut:

    * **ffmpeg is not everywhere.** Without it there is no way to run a *real*
      detector over *real* pixels, so the only end-to-end path would be synthetic —
      which cannot catch a detector integration bug.
    * **Some surveys are photographs.** A phone taking timed stills, or frames already
      extracted by another tool, is a legitimate input. The rest of the pipeline does
      not care where a frame came from.

    Images are ordered by filename and spread evenly across ``duration_s``, so the
    existing timestamp-to-GPS machinery places them without special cases.
    """

    def __init__(
        self,
        directory: str | Path,
        *,
        duration_s: float | None = None,
        fps: float = 1.0,
        survey_id: str | None = None,
    ) -> None:
        self.directory = Path(directory)
        if not self.directory.is_dir():
            raise NotADirectoryError(f"not a directory: {self.directory}")

        suffixes = {".jpg", ".jpeg", ".png", ".bmp"}
        self.paths = sorted(p for p in self.directory.iterdir() if p.suffix.lower() in suffixes)
        if not self.paths:
            raise FileNotFoundError(f"no images found in {self.directory}")

        self.survey_id = survey_id or self.directory.name
        self._duration = (
            duration_s if duration_s is not None else max(0.0, (len(self.paths) - 1) / fps)
        )
        # Evenly spaced timestamps. With one image the whole survey collapses to t=0,
        # which is correct rather than a division by zero.
        step = self._duration / (len(self.paths) - 1) if len(self.paths) > 1 else 0.0
        self._times = [round(i * step, 6) for i in range(len(self.paths))]

        width, height = self._probe_size(self.paths[0])
        self._info = VideoInfo(
            duration_s=self._duration,
            width=width,
            height=height,
            fps=(len(self.paths) / self._duration) if self._duration > 0 else None,
            frame_count=len(self.paths),
        )

    @staticmethod
    def _probe_size(path: Path) -> tuple[int, int]:
        try:
            from PIL import Image
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "Pillow is required to read image sequences. "
                "Install with: pip install -e '.[vision]'"
            ) from exc
        with Image.open(path) as handle:
            return handle.width, handle.height

    def info(self) -> VideoInfo:
        return self._info

    def frames_at(self, video_times_s: Sequence[float]) -> Iterator[tuple[float, FrameImage]]:
        """Yield the image nearest each requested timestamp.

        Nearest rather than exact: the sampling plan is built from GPS distance and
        will ask for times that fall between images. Each image is used at most once,
        so a plan denser than the images does not duplicate frames into the pipeline —
        which would manufacture defect observations that never existed.
        """
        from roadeye.vision.annotate import load_image_array

        used: set[int] = set()
        for target in sorted(video_times_s):
            index = min(
                (i for i in range(len(self.paths)) if i not in used),
                key=lambda i: abs(self._times[i] - target),
                default=None,
            )
            if index is None:
                break
            used.add(index)
            path = self.paths[index]
            pixels = load_image_array(path)
            yield (
                self._times[index],
                FrameImage(
                    frame_id=f"{self.survey_id}:{path.stem}",
                    width=pixels.shape[1],
                    height=pixels.shape[0],
                    pixels=pixels,
                    image_path=str(path),
                ),
            )


class PyAvFrameSource:
    """Real decoding via PyAV, imported lazily.

    Seeks to each requested timestamp rather than walking the file, because a 30-minute
    1080p30 survey holds ~54,000 frames of which we analyse a few hundred. Decoding all
    of them to discard 99% would dominate the run time.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(f"video not found: {self.path}")
        try:
            import av  # noqa: F401
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "Video decoding requires the optional 'video' extra. "
                "Install it with: pip install 'roadeye[video]'"
            ) from exc
        self._info: VideoInfo | None = None

    def info(self) -> VideoInfo:  # pragma: no cover - requires a real video file
        if self._info is not None:
            return self._info
        import av

        with av.open(str(self.path)) as container:
            stream = container.streams.video[0]
            duration = float(container.duration / 1_000_000) if container.duration else 0.0
            fps = float(stream.average_rate) if stream.average_rate else None
            self._info = VideoInfo(
                duration_s=duration,
                width=stream.codec_context.width,
                height=stream.codec_context.height,
                fps=fps,
                frame_count=stream.frames or None,
            )
        return self._info

    def frames_at(  # pragma: no cover - requires a real video file
        self, video_times_s: Sequence[float]
    ) -> Iterator[tuple[float, FrameImage]]:
        import av

        info = self.info()
        with av.open(str(self.path)) as container:
            stream = container.streams.video[0]
            stream.thread_type = "AUTO"
            for t in sorted(video_times_s):
                if t > info.duration_s:
                    continue
                target = int(t / float(stream.time_base))
                container.seek(target, stream=stream, any_frame=False, backward=True)
                for frame in container.decode(stream):
                    if frame.pts is None:
                        continue
                    if float(frame.pts * stream.time_base) + 1e-6 < t:
                        continue
                    yield (
                        t,
                        FrameImage(
                            frame_id=f"{self.path.stem}:f{t:.3f}",
                            width=frame.width,
                            height=frame.height,
                            pixels=frame.to_ndarray(format="rgb24"),
                        ),
                    )
                    break


def open_video(path: str | Path) -> FrameSource:
    """Open a real video file. Raises a clear error if the extra is not installed."""
    return PyAvFrameSource(path)


def frame_times(source: Iterable[tuple[float, FrameImage]]) -> list[float]:
    """Small helper for tests and logging."""
    return [t for t, _ in source]
