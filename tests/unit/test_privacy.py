"""Tests for redaction and retention.

Redaction is the one operation in RoadEye that must not be undoable, and the one whose
failures are invisible: an unredacted evidence image looks exactly like a redacted one.
So these tests are less about "does it blur" and more about the properties that make a
silent failure impossible:

* information is genuinely destroyed, not merely obscured,
* every derived file is redacted — especially the one that feeds training,
* a redactor that cannot run stops the work rather than writing the image anyway,
* nothing here ever claims the result is anonymous.

No real face or plate appears in this suite. The detector is scripted, because the
properties under test belong to the pipeline, not to any model's accuracy.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

np = pytest.importorskip("numpy", reason="numpy not installed")

from roadeye.privacy.anonymizer import Anonymizer, RedactionReport  # noqa: E402
from roadeye.privacy.base import (  # noqa: E402
    RedactionError,
    Region,
    RegionDetector,
    RegionKind,
)
from roadeye.privacy.detectors import (  # noqa: E402
    NullRegionDetector,
    ScriptedRegionDetector,
)
from roadeye.privacy.redaction import (  # noqa: E402
    RedactionConfig,
    RedactionMethod,
    block_size_for,
    redact_regions,
)
from roadeye.privacy.retention import (  # noqa: E402
    DELETION_LOG,
    RetentionPolicy,
    apply_retention,
    find_expired,
)

NOW = dt.datetime(2026, 8, 22, 12, 0, tzinfo=dt.UTC)


def noisy_image(height: int = 200, width: int = 300, seed: int = 7):
    """High-entropy pixels, so a failure to destroy information is unmistakable."""
    return np.random.default_rng(seed).integers(0, 256, (height, width, 3), dtype="uint8")


def person_at(x1: float, y1: float, x2: float, y2: float, confidence: float = 0.9) -> Region:
    return Region(x1=x1, y1=y1, x2=x2, y2=y2, kind=RegionKind.PERSON, confidence=confidence)


def distinct_colours(patch) -> int:
    return len(np.unique(patch.reshape(-1, 3), axis=0))


class TestInformationIsDestroyed:
    def test_a_mosaicked_region_collapses_to_a_handful_of_colours(self):
        image = noisy_image()
        out = redact_regions(image, [person_at(100, 50, 180, 150)])
        grown = person_at(100, 50, 180, 150).expanded(0.15, width=300, height=200)
        box = slice(int(grown.y1), int(grown.y2)), slice(int(grown.x1), int(grown.x2))

        before = distinct_colours(image[box])
        after = distinct_colours(out[box])
        assert before > 1000, "test image is not noisy enough to prove anything"
        assert after < 100, f"{after} distinct colours survived; information was not destroyed"

    def test_every_pixel_in_a_block_is_identical(self):
        """This is what makes it irreversible rather than merely blurry: many pixels are
        genuinely mapped onto one value, in the arithmetic, not scrambled by a kernel
        that could be inverted."""
        config = RedactionConfig()
        out = redact_regions(noisy_image(), [person_at(100, 50, 180, 150)], config=config)
        grown = person_at(100, 50, 180, 150).expanded(config.margin_fraction, width=300, height=200)
        x1, y1 = int(grown.x1), int(grown.y1)
        block = block_size_for(int(grown.width), int(grown.height), config)

        tile = out[y1 : y1 + block, x1 : x1 + block]
        assert (tile == tile[0, 0]).all(), "block is not uniform; the mosaic did not average"

    def test_a_solid_region_is_exactly_one_colour(self):
        out = redact_regions(
            noisy_image(),
            [person_at(100, 50, 180, 150)],
            config=RedactionConfig(method=RedactionMethod.SOLID),
        )
        assert distinct_colours(out[60:140, 110:170]) == 1

    def test_pixels_outside_the_region_are_byte_identical(self):
        """Redaction must not quietly degrade the road surface — that is the part the
        whole product is about."""
        image = noisy_image()
        out = redact_regions(image, [person_at(100, 50, 180, 150)])
        assert np.array_equal(out[:40, :80], image[:40, :80])
        assert np.array_equal(out[180:, 250:], image[180:, 250:])

    def test_the_original_array_is_not_mutated(self):
        """The caller may still hold the frame for a legitimate local purpose. Mutating
        it in place is how an unredacted frame becomes the thing that got written."""
        image = noisy_image()
        original = image.copy()
        redact_regions(image, [person_at(100, 50, 180, 150)])
        assert np.array_equal(image, original)

    def test_a_tiny_distant_region_is_destroyed_too(self):
        """A pedestrian 12 px tall at the end of the street is the common case in
        windscreen video, and a fixed block size would leave them untouched."""
        image = noisy_image()
        config = RedactionConfig()
        region = person_at(40, 40, 52, 58)
        out = redact_regions(image, [region], config=config)

        grown = region.expanded(config.margin_fraction, width=300, height=200)
        x1, y1 = int(grown.x1), int(grown.y1)
        x2, y2 = int(round(grown.x2)), int(round(grown.y2))
        block = block_size_for(x2 - x1, y2 - y1, config)
        # A 16x24 region at an 8 px block floor is a 2x3 grid: at most six colours.
        expected = -(-(x2 - x1) // block) * -(-(y2 - y1) // block)

        patch = out[y1:y2, x1:x2]
        assert distinct_colours(image[y1:y2, x1:x2]) > 100, "region is not noisy enough"
        assert distinct_colours(patch) <= expected

    def test_a_one_pixel_block_is_refused_at_construction(self):
        """A 1x1 mosaic is the identity function: it would report success having
        redacted nothing at all."""
        with pytest.raises(ValueError, match="min_block_px"):
            RedactionConfig(min_block_px=1)


class TestRegionsAreExpanded:
    def test_the_box_grows_by_the_margin(self):
        """Detectors fit boxes to what they are sure of, which clips hair and bumpers. A
        partially redacted face is not a redacted face."""
        grown = person_at(100, 100, 200, 200).expanded(0.2, width=1000, height=1000)
        assert grown.x1 == pytest.approx(80.0)
        assert grown.x2 == pytest.approx(220.0)

    def test_expansion_clamps_to_the_image(self):
        grown = person_at(5, 5, 105, 105).expanded(0.5, width=120, height=120)
        assert grown.x1 == 0.0 and grown.y1 == 0.0
        assert grown.x2 == 120.0 and grown.y2 == 120.0

    def test_an_inverted_region_is_refused(self):
        with pytest.raises(ValueError, match="inverted"):
            Region(x1=100, y1=10, x2=20, y2=50, kind=RegionKind.PERSON, confidence=0.5)


class TestFailClosed:
    def test_a_detector_that_raises_stops_the_work(self):
        """There is no fallback that writes the image anyway. An unredacted evidence
        image is indistinguishable from a redacted one by inspection, so a silent
        degradation is permanent and undetectable."""

        class Broken:
            @property
            def detector_id(self) -> str:
                return "broken"

            def find(self, image, *, width, height):
                raise RuntimeError("model file is corrupt")

        with pytest.raises(RedactionError, match="Refusing to write it unredacted"):
            Anonymizer(Broken()).redact(noisy_image())

    def test_an_anonymizer_without_a_detector_is_refused(self):
        with pytest.raises(RedactionError, match="needs a detector"):
            Anonymizer(None)  # type: ignore[arg-type]

    def test_opting_out_is_explicit_not_accidental(self):
        """NullRegionDetector says 'I decided these need no redaction', which is
        auditable. A missing detector that silently degraded would not be."""
        anonymizer = Anonymizer(NullRegionDetector())
        image = noisy_image()
        out, report = anonymizer.redact(image)
        assert np.array_equal(out, image)
        assert report.detector_id == "null"
        assert report.region_count == 0


class TestTheReportDoesNotOverclaim:
    def test_it_records_the_detector_and_the_config(self):
        """When a detector is later found to miss a class of subject, the only way to
        know which images need reprocessing is to know which detector wrote them."""
        anonymizer = Anonymizer(
            ScriptedRegionDetector([person_at(10, 10, 60, 60)], detector_id="test-v1")
        )
        _, report = anonymizer.redact(noisy_image())
        payload = report.to_json()
        assert payload["detector_id"] == "test-v1"
        assert payload["config"]["method"] == "mosaic"
        assert payload["regions_redacted"] == 1
        assert payload["by_kind"] == {"person": 1}

    def test_it_never_claims_the_image_is_anonymous(self):
        """A detector that missed somebody has produced an image with somebody in it,
        whatever the label says."""
        _, report = Anonymizer(NullRegionDetector()).redact(noisy_image())
        text = json.dumps(report.to_json()).lower()
        assert "best-effort" in text
        assert "not a guarantee" in text
        assert "anonymous" not in text.replace("not a guarantee that no identifiable", "")

    def test_counts_are_broken_down_by_kind(self):
        regions = [
            person_at(10, 10, 60, 60),
            Region(x1=100, y1=20, x2=200, y2=90, kind=RegionKind.VEHICLE, confidence=0.8),
            Region(x1=210, y1=20, x2=280, y2=90, kind=RegionKind.VEHICLE, confidence=0.7),
        ]
        _, report = Anonymizer(ScriptedRegionDetector(regions)).redact(noisy_image())
        assert report.count_of(RegionKind.PERSON) == 1
        assert report.count_of(RegionKind.VEHICLE) == 2


class TestTheProtocolHolds:
    def test_the_supplied_detectors_satisfy_it(self):
        assert isinstance(NullRegionDetector(), RegionDetector)
        assert isinstance(ScriptedRegionDetector([]), RegionDetector)

    def test_regions_carry_no_identity(self):
        """The boundary in ADR-007: RoadEye detects in order to destroy. There is no
        field here that could hold an identity, an embedding or a track, because the
        only thing done with a region is to overwrite it."""
        fields = set(Region.__dataclass_fields__)
        assert fields == {"x1", "y1", "x2", "y2", "kind", "confidence"}
        assert {k.value for k in RegionKind} == {"person", "vehicle"}


class TestRetention:
    def make_tree(self, tmp_path):
        (tmp_path / "frames").mkdir()
        old_video = tmp_path / "old.mp4"
        new_video = tmp_path / "new.mp4"
        old_frame = tmp_path / "frames" / "f1.jpg"
        keep = tmp_path / "defects.db"
        for path in (old_video, new_video, old_frame, keep):
            path.write_bytes(b"x" * 1024)

        def age(path, days):
            stamp = (NOW - dt.timedelta(days=days)).timestamp()
            import os

            os.utime(path, (stamp, stamp))

        age(old_video, 60)
        age(new_video, 2)
        age(old_frame, 200)
        age(keep, 500)
        return old_video, new_video, old_frame, keep

    def test_only_expired_artefacts_are_due(self, tmp_path):
        old_video, new_video, old_frame, keep = self.make_tree(tmp_path)
        due = {c.path for c in find_expired(tmp_path, now=NOW)}
        assert old_video in due and old_frame in due
        assert new_video not in due
        assert keep not in due, "a database is not a retention artefact"

    def test_raw_video_expires_first(self):
        """docs/PRIVACY.md orders it deliberately: raw video is the highest-risk thing
        held, so it has the shortest retention of any artefact."""
        policy = RetentionPolicy()
        assert policy.raw_video_days < policy.frames_days

    def test_dry_run_deletes_nothing(self, tmp_path):
        old_video, _, old_frame, _ = self.make_tree(tmp_path)
        sweep = apply_retention(tmp_path, now=NOW)
        assert sweep.dry_run
        assert sweep.deleted_count == 0
        assert old_video.exists() and old_frame.exists()

    def test_delete_removes_only_what_was_due(self, tmp_path):
        old_video, new_video, old_frame, keep = self.make_tree(tmp_path)
        sweep = apply_retention(tmp_path, delete=True, now=NOW)
        assert sweep.deleted_count == 2
        assert not old_video.exists() and not old_frame.exists()
        assert new_video.exists() and keep.exists()

    def test_every_deletion_is_logged(self, tmp_path):
        """A policy in a document is not a control. A policy that runs and logs is."""
        old_video, _, _, _ = self.make_tree(tmp_path)
        apply_retention(tmp_path, delete=True, now=NOW)

        lines = (tmp_path / DELETION_LOG).read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        records = [json.loads(line) for line in lines]
        assert {r["kind"] for r in records} == {"raw_video", "frames"}
        assert all(r["deleted_at"].endswith("Z") for r in records)

    def test_the_log_records_no_content(self, tmp_path):
        """A deletion log that quotes what it deleted has recreated the thing it
        deleted."""
        (tmp_path / "secret.mp4").write_bytes(b"IDENTIFIABLE-PIXELS")
        import os

        stamp = (NOW - dt.timedelta(days=90)).timestamp()
        os.utime(tmp_path / "secret.mp4", (stamp, stamp))

        apply_retention(tmp_path, delete=True, now=NOW)
        log = (tmp_path / DELETION_LOG).read_text(encoding="utf-8")
        assert "IDENTIFIABLE-PIXELS" not in log
        assert json.loads(log.strip()).keys() == {
            "deleted_at",
            "path",
            "kind",
            "age_days",
            "size_bytes",
        }

    def test_the_log_appends_across_sweeps(self, tmp_path):
        """Append-only: a second sweep must not overwrite the first one's record."""
        import os

        for name in ("a.mp4", "b.mp4"):
            path = tmp_path / name
            path.write_bytes(b"x")
            stamp = (NOW - dt.timedelta(days=90)).timestamp()
            os.utime(path, (stamp, stamp))

        apply_retention(tmp_path, delete=True, now=NOW)
        (tmp_path / "c.mp4").write_bytes(b"x")
        stamp = (NOW - dt.timedelta(days=90)).timestamp()
        os.utime(tmp_path / "c.mp4", (stamp, stamp))
        apply_retention(tmp_path, delete=True, now=NOW)

        lines = (tmp_path / DELETION_LOG).read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 3

    def test_a_missing_directory_is_reported_not_a_crash(self, tmp_path):
        sweep = apply_retention(tmp_path / "nope", now=NOW)
        assert sweep.candidates == []
        assert any("does not exist" in s for s in sweep.skipped)


class TestReportVersioning:
    def test_a_report_states_its_version(self):
        """Reports are the audit trail for a legal obligation, not a debug log, so an
        old one must never be read as a new one."""
        _, report = Anonymizer(NullRegionDetector()).redact(noisy_image())
        assert isinstance(report, RedactionReport)
        assert report.to_json()["report_version"] >= 1
