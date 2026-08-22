"""Tests for turning review decisions into training data.

The export is where human judgement becomes model improvement, so the properties that
matter are about *what gets learned*:

* an unreviewed defect must not become training data — that teaches the model to agree
  with itself and makes its errors permanent,
* a rejection must become a hard negative rather than being discarded,
* the *human's* class must win over the model's,
* the clean frame must be used, never the one with a box drawn on it.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

pytest.importorskip("PIL", reason="Pillow not installed")

from roadeye.domain.enums import (  # noqa: E402
    DamageClass,
    DefectStatus,
    LocationMethod,
)
from roadeye.domain.models import (  # noqa: E402
    BoundingBox,
    Defect,
    DefectObservation,
    Detection,
    Frame,
    GeoPoint,
    Survey,
)
from roadeye.reporting.training_export import export_reviewed_dataset  # noqa: E402
from roadeye.storage.db import Database  # noqa: E402

NOW = dt.datetime(2026, 8, 18, 10, 42, 11, tzinfo=dt.UTC)


def build_db(tmp_path, defects: list[tuple[str, DefectStatus, DamageClass, str]]):
    """Create a database + evidence for the given (id, status, class, survey) tuples."""
    from PIL import Image

    db_path = tmp_path / "r.db"
    evidence = tmp_path / "ev"
    evidence.mkdir(exist_ok=True)

    surveys_made: set[str] = set()
    with Database(db_path) as db:
        for i, (defect_id, status, damage_class, survey_id) in enumerate(defects, 1):
            if survey_id not in surveys_made:
                db.upsert_survey(
                    Survey(
                        survey_id=survey_id,
                        started_at=NOW,
                        recording_start_epoch_ms=int(NOW.timestamp() * 1000),
                    )
                )
                surveys_made.add(survey_id)

            frame_id = f"{survey_id}:f{i}"
            db.insert_frames(
                [
                    Frame(
                        frame_id=frame_id,
                        survey_id=survey_id,
                        video_time_s=float(i),
                        t_epoch_ms=int(NOW.timestamp() * 1000) + i * 1000,
                        width=120,
                        height=120,
                    )
                ]
            )
            db.insert_detections(
                [
                    Detection(
                        detection_id=f"det{i}",
                        frame_id=frame_id,
                        survey_id=survey_id,
                        # The MODEL always says pothole; the human may disagree.
                        damage_class=DamageClass.POTHOLE,
                        confidence=0.7,
                        bbox=BoundingBox(x1=10, y1=20, x2=60, y2=70),
                        model_id="m1",
                    )
                ]
            )
            db.upsert_defects(
                [
                    Defect(
                        defect_id=defect_id,
                        damage_class=damage_class,
                        location=GeoPoint(
                            lat=40.18 + i * 0.001,
                            lon=44.51,
                            method=LocationMethod.INTERPOLATED_PHONE_GPS,
                            uncertainty_m=6.0,
                        ),
                        confidence=0.7,
                        status=status,
                        first_seen=NOW,
                        last_seen=NOW,
                        survey_ids=[survey_id],
                        observation_count=1,
                        representative_frame_id=frame_id,
                        model_id="m1",
                    )
                ]
            )
            db.insert_observations(
                [
                    DefectObservation(
                        observation_id=f"obs{i}",
                        defect_id=defect_id,
                        survey_id=survey_id,
                        detection_ids=[f"det{i}"],
                        observed_at=NOW,
                        confidence=0.7,
                        location=GeoPoint(
                            lat=40.18 + i * 0.001,
                            lon=44.51,
                            method=LocationMethod.INTERPOLATED_PHONE_GPS,
                            uncertainty_m=6.0,
                        ),
                        representative_frame_id=frame_id,
                    )
                ]
            )
            # Clean frame is grey; the context image is deliberately bright red so a
            # test can prove which one the export used.
            Image.new("RGB", (120, 120), (70, 70, 70)).save(evidence / f"{defect_id}_frame.jpg")
            Image.new("RGB", (120, 120), (255, 0, 0)).save(evidence / f"{defect_id}_context.jpg")

    return db_path, evidence


def read_records(output) -> list[dict]:
    return [
        json.loads(line)
        for line in (output / "annotations.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class TestWhatGetsExported:
    def test_unreviewed_defects_are_excluded(self, tmp_path):
        """Training on the model's own unreviewed output teaches it to agree with
        itself, which makes its mistakes permanent."""
        db_path, evidence = build_db(
            tmp_path,
            [
                ("d1", DefectStatus.PROBABLE, DamageClass.POTHOLE, "s1"),
                ("d2", DefectStatus.VERIFIED, DamageClass.POTHOLE, "s1"),
            ],
        )
        stats = export_reviewed_dataset(db_path, tmp_path / "out", evidence_dir=evidence)
        assert stats.skipped_unreviewed == 1
        assert stats.images == 1

    def test_rejections_become_hard_negatives(self, tmp_path):
        """A rejection is the most informative label available: the model was
        confidently wrong about a manhole or a shadow."""
        db_path, evidence = build_db(
            tmp_path, [("d1", DefectStatus.REJECTED, DamageClass.POTHOLE, "s1")]
        )
        stats = export_reviewed_dataset(db_path, tmp_path / "out", evidence_dir=evidence)
        assert stats.negatives == 1

        records = read_records(tmp_path / "out")
        assert len(records) == 1
        assert records[0]["boxes"] == [], "a rejection must carry no boxes"

    def test_negatives_can_be_excluded(self, tmp_path):
        db_path, evidence = build_db(
            tmp_path, [("d1", DefectStatus.REJECTED, DamageClass.POTHOLE, "s1")]
        )
        stats = export_reviewed_dataset(
            db_path, tmp_path / "out", evidence_dir=evidence, include_negatives=False
        )
        assert stats.images == 0

    def test_the_humans_class_wins_over_the_models(self, tmp_path):
        """The detection says pothole; the human corrected the defect to alligator
        crack. Exporting the model's own label would discard the correction — the one
        piece of information the whole review loop exists to capture."""
        db_path, evidence = build_db(
            tmp_path, [("d1", DefectStatus.VERIFIED, DamageClass.ALLIGATOR_CRACK, "s1")]
        )
        export_reviewed_dataset(db_path, tmp_path / "out", evidence_dir=evidence)
        boxes = read_records(tmp_path / "out")[0]["boxes"]
        assert boxes[0]["damage_class"] == "alligator_crack"

    def test_uses_the_clean_frame_not_the_annotated_one(self, tmp_path):
        """The context image has a box drawn on it. Training on those teaches the model
        to find red rectangles."""
        from PIL import Image

        db_path, evidence = build_db(
            tmp_path, [("d1", DefectStatus.VERIFIED, DamageClass.POTHOLE, "s1")]
        )
        export_reviewed_dataset(db_path, tmp_path / "out", evidence_dir=evidence)

        with Image.open(tmp_path / "out" / "images" / "d1.jpg") as image:
            r, g, b = image.convert("RGB").getpixel((60, 60))
        assert r < 120 and g < 120 and b < 120, "exported the annotated image, not the frame"

    def test_missing_evidence_is_counted_not_fatal(self, tmp_path):
        db_path, evidence = build_db(
            tmp_path, [("d1", DefectStatus.VERIFIED, DamageClass.POTHOLE, "s1")]
        )
        (evidence / "d1_frame.jpg").unlink()
        stats = export_reviewed_dataset(db_path, tmp_path / "out", evidence_dir=evidence)
        assert stats.skipped_no_image == 1
        assert stats.images == 0


class TestSplitsAndManifest:
    def test_splits_are_survey_disjoint(self, tmp_path):
        """No survey may appear in two splits: frames from one drive are metres apart
        and often show the same defect (ADR-008)."""
        db_path, evidence = build_db(
            tmp_path,
            [(f"d{i}", DefectStatus.VERIFIED, DamageClass.POTHOLE, f"s{i}") for i in range(1, 7)],
        )
        export_reviewed_dataset(db_path, tmp_path / "out", evidence_dir=evidence)

        splits = json.loads((tmp_path / "out" / "splits.json").read_text())
        records = {r["image_id"]: r for r in read_records(tmp_path / "out")}
        seen: dict[str, str] = {}
        for split, ids in splits.items():
            for image_id in ids:
                # image_id is the defect id; d<N> came from s<N>.
                survey = "s" + image_id[1:]
                assert seen.setdefault(survey, split) == split, (
                    f"survey {survey} appears in more than one split"
                )

        assert records  # sanity

    def test_single_survey_warns_instead_of_faking_a_test_split(self, tmp_path):
        db_path, evidence = build_db(
            tmp_path,
            [
                ("d1", DefectStatus.VERIFIED, DamageClass.POTHOLE, "s1"),
                ("d2", DefectStatus.VERIFIED, DamageClass.POTHOLE, "s1"),
            ],
        )
        export_reviewed_dataset(db_path, tmp_path / "out", evidence_dir=evidence)

        manifest = json.loads((tmp_path / "out" / "manifest.json").read_text())
        splits = json.loads((tmp_path / "out" / "splits.json").read_text())
        assert splits["test"] == []
        assert "warning" in manifest
        assert "one survey" in manifest["warning"].lower()

    def test_manifest_marks_our_own_data_distributable(self, tmp_path):
        """Unlike anything RDD2022-derived. This is the point of collecting Armenian
        data: a model trained solely on it is unencumbered (BLOCKING-1)."""
        db_path, evidence = build_db(
            tmp_path, [("d1", DefectStatus.VERIFIED, DamageClass.POTHOLE, "s1")]
        )
        export_reviewed_dataset(db_path, tmp_path / "out", evidence_dir=evidence)
        manifest = json.loads((tmp_path / "out" / "manifest.json").read_text())
        assert manifest["distribution_allowed"] is True
        assert "RDD2022" in manifest["license_notes"]

    def test_manifest_has_a_content_hash(self, tmp_path):
        db_path, evidence = build_db(
            tmp_path, [("d1", DefectStatus.VERIFIED, DamageClass.POTHOLE, "s1")]
        )
        export_reviewed_dataset(db_path, tmp_path / "out", evidence_dir=evidence)
        manifest = json.loads((tmp_path / "out" / "manifest.json").read_text())
        assert len(manifest["content_hash"]) == 32

    def test_output_matches_the_rdd2022_format(self, tmp_path):
        """The training script must consume reviewed Armenian data and bootstrap data
        identically."""
        db_path, evidence = build_db(
            tmp_path, [("d1", DefectStatus.VERIFIED, DamageClass.POTHOLE, "s1")]
        )
        output = tmp_path / "out"
        export_reviewed_dataset(db_path, output, evidence_dir=evidence)

        assert (output / "annotations.jsonl").exists()
        assert (output / "splits.json").exists()
        assert (output / "manifest.json").exists()
        assert (output / "images" / "d1.jpg").exists()

        record = read_records(output)[0]
        for key in ("image_id", "file_name", "width", "height", "boxes"):
            assert key in record, key
        for key in ("damage_class", "xmin", "ymin", "xmax", "ymax"):
            assert key in record["boxes"][0], key
