"""Tests for reading a folder of images as a survey's frames.

This is the path that lets a real detector see real pixels on a machine with no
ffmpeg — so if it is wrong, the only end-to-end coverage left is synthetic, which
cannot catch a detector integration bug.
"""

from __future__ import annotations

import pytest

from roadeye.video.decoder import FrameSource, ImageSequenceFrameSource

pytest.importorskip("PIL", reason="Pillow not installed")


@pytest.fixture
def image_dir(tmp_path):
    """Six small images, deliberately not in lexicographic-by-accident order."""
    from PIL import Image

    directory = tmp_path / "frames"
    directory.mkdir()
    for i in range(6):
        Image.new("RGB", (64, 48), color=(i * 40, 10, 20)).save(directory / f"f{i:03d}.jpg")
    return directory


class TestConstruction:
    def test_satisfies_the_protocol(self, image_dir):
        assert isinstance(ImageSequenceFrameSource(image_dir), FrameSource)

    def test_reports_real_image_dimensions(self, image_dir):
        info = ImageSequenceFrameSource(image_dir).info()
        assert (info.width, info.height) == (64, 48)
        assert info.frame_count == 6

    def test_spreads_images_across_the_survey_duration(self, image_dir):
        """Timestamps must span the drive, so the existing GPS interpolation places
        each image without special-casing this source."""
        source = ImageSequenceFrameSource(image_dir, duration_s=10.0)
        times = [t for t, _ in source.frames_at([0, 2, 4, 6, 8, 10])]
        assert min(times) == pytest.approx(0.0)
        assert max(times) == pytest.approx(10.0)

    def test_single_image_does_not_divide_by_zero(self, tmp_path):
        from PIL import Image

        directory = tmp_path / "one"
        directory.mkdir()
        Image.new("RGB", (32, 32)).save(directory / "only.jpg")
        source = ImageSequenceFrameSource(directory, duration_s=5.0)
        assert [t for t, _ in source.frames_at([0.0])] == [0.0]

    def test_missing_directory(self, tmp_path):
        with pytest.raises(NotADirectoryError):
            ImageSequenceFrameSource(tmp_path / "nope")

    def test_empty_directory(self, tmp_path):
        (tmp_path / "empty").mkdir()
        with pytest.raises(FileNotFoundError, match="no images"):
            ImageSequenceFrameSource(tmp_path / "empty")

    def test_ignores_non_images(self, image_dir):
        (image_dir / "notes.txt").write_text("not an image", encoding="utf-8")
        assert ImageSequenceFrameSource(image_dir).info().frame_count == 6


class TestFrames:
    def test_frames_carry_real_pixels(self, image_dir):
        """The whole point: a real detector needs real pixel data, not None."""
        source = ImageSequenceFrameSource(image_dir, duration_s=5.0)
        _, frame = next(iter(source.frames_at([0.0])))
        assert frame.pixels is not None
        assert frame.pixels.shape == (48, 64, 3)
        assert frame.image_path is not None

    def test_frame_ids_are_unique_and_traceable(self, image_dir):
        source = ImageSequenceFrameSource(image_dir, duration_s=5.0, survey_id="s1")
        ids = [f.frame_id for _, f in source.frames_at([0, 1, 2, 3, 4, 5])]
        assert len(set(ids)) == len(ids)
        assert all(i.startswith("s1:") for i in ids)

    def test_no_image_is_used_twice(self, image_dir):
        """A sampling plan denser than the images must not duplicate frames.

        Feeding the same photo through twice would manufacture a second observation
        of a defect that was only seen once — inflating the observation count that a
        municipality reads as corroborating evidence.
        """
        source = ImageSequenceFrameSource(image_dir, duration_s=5.0)
        requested = [i * 0.25 for i in range(21)]  # 21 requests, 6 images
        frames = list(source.frames_at(requested))
        assert len(frames) == 6
        assert len({f.image_path for _, f in frames}) == 6

    def test_returns_frames_in_time_order(self, image_dir):
        source = ImageSequenceFrameSource(image_dir, duration_s=5.0)
        times = [t for t, _ in source.frames_at([5, 0, 3, 1, 4, 2])]
        assert times == sorted(times)

    def test_sparse_plan_picks_nearest_images(self, image_dir):
        source = ImageSequenceFrameSource(image_dir, duration_s=5.0)
        frames = list(source.frames_at([0.0, 5.0]))
        assert len(frames) == 2
        assert frames[0][0] == pytest.approx(0.0)
        assert frames[1][0] == pytest.approx(5.0)


class TestBundleIntegration:
    def test_pipeline_picks_up_a_frames_directory(self, tmp_path):
        """A bundle with frames/ but no video must still feed real pixels through."""
        import datetime as dt

        from PIL import Image

        from roadeye.geolocation.timesync import LocationSample
        from roadeye.ingest.bundle import load_bundle, write_bundle_skeleton
        from roadeye.pipeline import open_frame_source

        start = dt.datetime(2026, 8, 18, 10, 0, 0, tzinfo=dt.UTC)
        t0 = int(start.timestamp() * 1000)
        path = write_bundle_skeleton(
            tmp_path / "survey",
            survey_id="withframes",
            started_at=start,
            recording_start_epoch_ms=t0,
            samples=[
                LocationSample(
                    t_epoch_ms=t0 + i * 1000,
                    lat=40.18231,
                    lon=44.51491 + i * 0.000118,
                    accuracy_m=5.0,
                    speed_mps=10.0,
                )
                for i in range(5)
            ],
            ended_at=start + dt.timedelta(seconds=4),
        )
        frames = path / "frames"
        frames.mkdir()
        for i in range(3):
            Image.new("RGB", (48, 48), color=(i * 60, 0, 0)).save(frames / f"{i}.jpg")

        bundle = load_bundle(path)
        source = open_frame_source(bundle)
        assert isinstance(source, ImageSequenceFrameSource)
        assert source.info().frame_count == 3
        # frames/ is a known part of the format, not an unexpected file.
        assert not any("frames" in w for w in bundle.warnings)

    def test_bundle_without_frames_or_video_has_no_source(self, tmp_path):
        import datetime as dt

        from roadeye.geolocation.timesync import LocationSample
        from roadeye.ingest.bundle import load_bundle, write_bundle_skeleton
        from roadeye.pipeline import open_frame_source

        start = dt.datetime(2026, 8, 18, tzinfo=dt.UTC)
        t0 = int(start.timestamp() * 1000)
        path = write_bundle_skeleton(
            tmp_path / "bare",
            survey_id="bare001",
            started_at=start,
            recording_start_epoch_ms=t0,
            samples=[
                LocationSample(t_epoch_ms=t0 + i * 1000, lat=40.1, lon=44.5, accuracy_m=5.0)
                for i in range(3)
            ],
        )
        assert open_frame_source(load_bundle(path)) is None
