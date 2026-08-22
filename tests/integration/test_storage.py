"""Storage integration tests: SQLite schema, R*Tree index, provenance round-trips."""

from __future__ import annotations

import datetime as dt

import pytest

from roadeye.domain.enums import (
    DamageClass,
    DefectStatus,
    LocationMethod,
    ReviewAction,
    Severity,
    SeveritySource,
)
from roadeye.domain.models import (
    BoundingBox,
    Defect,
    DefectObservation,
    Detection,
    Frame,
    GeoPoint,
    ModelVersion,
    ProcessingRun,
    Review,
    Survey,
)
from roadeye.geolocation.geodesy import LatLon, destination_point
from roadeye.storage.db import Database

NOW = dt.datetime(2026, 8, 18, 10, 42, 11, tzinfo=dt.timezone.utc)
ORIGIN = LatLon(40.18231, 44.51491)


@pytest.fixture
def db():
    with Database(":memory:") as database:
        yield database


def make_defect(defect_id: str = "d1", *, metres_east: float = 0.0, **kw) -> Defect:
    point = destination_point(ORIGIN, 90.0, metres_east) if metres_east else ORIGIN
    params = {
        "defect_id": defect_id,
        "damage_class": DamageClass.POTHOLE,
        "location": GeoPoint(
            lat=point.lat,
            lon=point.lon,
            method=LocationMethod.INTERPOLATED_PHONE_GPS,
            uncertainty_m=7.5,
        ),
        "confidence": 0.91,
        "first_seen": NOW,
        "last_seen": NOW,
        "survey_ids": ["s1"],
        "observation_count": 3,
    }
    params.update(kw)
    return Defect(**params)


def make_survey(survey_id: str = "s1") -> Survey:
    return Survey(
        survey_id=survey_id,
        started_at=NOW,
        ended_at=NOW + dt.timedelta(minutes=20),
        recording_start_epoch_ms=int(NOW.timestamp() * 1000),
        device={"model": "test"},
    )


class TestSchema:
    def test_tables_created(self, db: Database):
        for table in ("surveys", "frames", "detections", "defects", "reviews"):
            assert db.count(table) == 0

    def test_rejects_unknown_table(self, db: Database):
        with pytest.raises(ValueError, match="unknown table"):
            db.count("robert'); DROP TABLE defects;--")

    def test_foreign_keys_enforced(self, db: Database):
        """Off by default in SQLite; without this, cascading deletes silently do
        nothing and orphaned evidence piles up."""
        import sqlite3

        with pytest.raises(sqlite3.IntegrityError):
            db.append_review(
                Review(
                    review_id="r1",
                    defect_id="does-not-exist",
                    action=ReviewAction.APPROVE,
                    reviewer="tester",
                )
            )


class TestDefectRoundTrip:
    def test_insert_and_read(self, db: Database):
        db.upsert_defects([make_defect()])
        loaded = db.get_defect("d1")
        assert loaded is not None
        assert loaded.damage_class is DamageClass.POTHOLE
        assert loaded.location.uncertainty_m == pytest.approx(7.5)
        assert loaded.location.method is LocationMethod.INTERPOLATED_PHONE_GPS
        assert loaded.survey_ids == ["s1"]

    def test_upsert_updates_in_place(self, db: Database):
        db.upsert_defects([make_defect()])
        db.upsert_defects(
            [make_defect(status=DefectStatus.VERIFIED, severity=Severity.HIGH,
                         severity_source=SeveritySource.HUMAN)]
        )
        assert db.count("defects") == 1
        loaded = db.get_defect("d1")
        assert loaded.status is DefectStatus.VERIFIED
        assert loaded.severity is Severity.HIGH

    def test_missing_defect_returns_none(self, db: Database):
        assert db.get_defect("nope") is None

    def test_representative_frame_survives(self, db: Database):
        db.upsert_defects([make_defect(representative_frame_id="s1:f12.500")])
        assert db.get_defect("d1").representative_frame_id == "s1:f12.500"


class TestFiltering:
    def test_by_class_and_status(self, db: Database):
        db.upsert_defects(
            [
                make_defect("d1", damage_class=DamageClass.POTHOLE),
                make_defect("d2", metres_east=100, damage_class=DamageClass.ALLIGATOR_CRACK),
                make_defect("d3", metres_east=200, status=DefectStatus.VERIFIED),
            ]
        )
        assert len(db.list_defects(damage_class=DamageClass.POTHOLE)) == 2
        assert len(db.list_defects(status=DefectStatus.VERIFIED)) == 1

    def test_by_confidence(self, db: Database):
        db.upsert_defects(
            [make_defect("d1", confidence=0.3), make_defect("d2", metres_east=100, confidence=0.95)]
        )
        assert len(db.list_defects(min_confidence=0.5)) == 1

    def test_by_survey(self, db: Database):
        db.upsert_defects(
            [
                make_defect("d1", survey_ids=["aug18"]),
                make_defect("d2", metres_east=100, survey_ids=["sep08"]),
            ]
        )
        assert [d.defect_id for d in db.list_defects(survey_id="aug18")] == ["d1"]

    def test_limit(self, db: Database):
        db.upsert_defects([make_defect(f"d{i}", metres_east=i * 100) for i in range(5)])
        assert len(db.list_defects(limit=2)) == 2


class TestSpatialIndex:
    def test_finds_nearby_defect(self, db: Database):
        db.upsert_defects([make_defect()])
        results = db.defects_near(ORIGIN.lat, ORIGIN.lon, 50.0)
        assert len(results) == 1
        assert results[0][1] == pytest.approx(0.0, abs=1.0)

    def test_excludes_distant_defects(self, db: Database):
        db.upsert_defects([make_defect("far", metres_east=5000)])
        assert db.defects_near(ORIGIN.lat, ORIGIN.lon, 100.0) == []

    def test_results_sorted_by_distance(self, db: Database):
        db.upsert_defects(
            [
                make_defect("near", metres_east=10),
                make_defect("mid", metres_east=50),
                make_defect("far", metres_east=90),
            ]
        )
        found = db.defects_near(ORIGIN.lat, ORIGIN.lon, 200.0)
        assert [d.defect_id for d, _ in found] == ["near", "mid", "far"]
        assert found[0][1] < found[1][1] < found[2][1]

    def test_radius_boundary_is_exact_not_boxy(self, db: Database):
        """The R*Tree returns a bounding box; true distance must then filter the
        corners off, or a 'within 50 m' query silently returns points at 70 m."""
        db.upsert_defects([make_defect("corner", metres_east=0)])
        # Place a defect diagonally so it sits inside the box but outside the circle.
        diag = destination_point(ORIGIN, 45.0, 60.0)
        db.upsert_defects(
            [
                make_defect(
                    "diagonal",
                    location=GeoPoint(
                        lat=diag.lat,
                        lon=diag.lon,
                        method=LocationMethod.PHONE_GPS,
                        uncertainty_m=5.0,
                    ),
                )
            ]
        )
        found = {d.defect_id for d, _ in db.defects_near(ORIGIN.lat, ORIGIN.lon, 50.0)}
        assert found == {"corner"}

    def test_reindexes_on_move(self, db: Database):
        """A manual location correction must move the spatial index entry too."""
        db.upsert_defects([make_defect("d1")])
        moved = destination_point(ORIGIN, 90.0, 500.0)
        db.upsert_defects(
            [
                make_defect(
                    "d1",
                    location=GeoPoint(
                        lat=moved.lat,
                        lon=moved.lon,
                        method=LocationMethod.MANUAL_CORRECTION,
                        uncertainty_m=1.0,
                    ),
                )
            ]
        )
        assert db.defects_near(ORIGIN.lat, ORIGIN.lon, 100.0) == []
        assert len(db.defects_near(moved.lat, moved.lon, 100.0)) == 1


class TestProvenanceChain:
    def test_full_chain_persists(self, db: Database):
        """Defect -> observation -> detection -> frame -> survey must survive storage,
        or the auditability claim is unsupported."""
        db.upsert_survey(make_survey())
        frame = Frame(
            frame_id="f1",
            survey_id="s1",
            video_time_s=42.733,
            t_epoch_ms=int(NOW.timestamp() * 1000) + 42_733,
            width=1920,
            height=1080,
        )
        db.insert_frames([frame])
        detection = Detection(
            detection_id="det1",
            frame_id="f1",
            survey_id="s1",
            damage_class=DamageClass.POTHOLE,
            confidence=0.9,
            bbox=BoundingBox(x1=100, y1=600, x2=300, y2=800),
            model_id="m1",
            track_id="trk1",
        )
        db.insert_detections([detection])
        db.upsert_defects([make_defect()])
        db.insert_observations(
            [
                DefectObservation(
                    observation_id="obs1",
                    defect_id="d1",
                    survey_id="s1",
                    track_id="trk1",
                    detection_ids=["det1"],
                    observed_at=NOW,
                    confidence=0.9,
                    location=GeoPoint(
                        lat=ORIGIN.lat,
                        lon=ORIGIN.lon,
                        method=LocationMethod.INTERPOLATED_PHONE_GPS,
                        uncertainty_m=6.0,
                    ),
                    representative_frame_id="f1",
                )
            ]
        )

        observations = db.observations_for("d1")
        assert len(observations) == 1
        assert observations[0].detection_ids == ["det1"]
        assert observations[0].representative_frame_id == "f1"

    def test_model_version_records_licence_position(self, db: Database):
        db.upsert_model_version(
            ModelVersion(
                model_id="rdd_bootstrap_v001",
                name="RDD bootstrap",
                architecture="fasterrcnn",
                framework="torchvision",
                training_data_licenses=["RDD2022: CC BY 4.0 / CC BY-SA 4.0 (disputed)"],
                distribution_allowed=False,
                classes=[DamageClass.POTHOLE],
                notes="Quarantined pending BLOCKING-1 in docs/LICENSE_AUDIT.md",
            )
        )
        assert db.count("model_versions") == 1

    def test_processing_run_records_config(self, db: Database):
        db.upsert_processing_run(
            ProcessingRun(
                run_id="run1",
                survey_id="s1",
                config={"sampling": {"mode": "distance", "target_spacing_m": 2.5}},
                git_commit="abc123",
                defects=17,
            )
        )
        assert db.count("processing_runs") == 1


class TestReviewsAreAppendOnly:
    def test_history_accumulates(self, db: Database):
        """Human corrections are evidence. A later review must not erase an earlier
        one — a system selling auditability cannot quietly overwrite decisions."""
        db.upsert_defects([make_defect()])
        for i, action in enumerate(
            [ReviewAction.APPROVE, ReviewAction.CHANGE_SEVERITY, ReviewAction.ADJUST_LOCATION]
        ):
            db.append_review(
                Review(
                    review_id=f"r{i}",
                    defect_id="d1",
                    action=action,
                    reviewer="inspector",
                    reviewed_at=NOW + dt.timedelta(minutes=i),
                )
            )
        history = db.reviews_for("d1")
        assert len(history) == 3
        assert [r["action"] for r in history] == [
            "approve",
            "change_severity",
            "adjust_location",
        ]

    def test_records_before_and_after(self, db: Database):
        db.upsert_defects([make_defect()])
        db.append_review(
            Review(
                review_id="r1",
                defect_id="d1",
                action=ReviewAction.CHANGE_CLASS,
                reviewer="inspector",
                previous_value={"damage_class": "pothole"},
                new_value={"damage_class": "alligator_crack"},
                note="manhole surround, not a pothole",
            )
        )
        row = db.reviews_for("d1")[0]
        assert "pothole" in row["previous_value_json"]
        assert "alligator_crack" in row["new_value_json"]
        assert row["note"].startswith("manhole")


class TestPersistence:
    def test_survives_reopen(self, tmp_path):
        path = tmp_path / "roadeye.db"
        with Database(path) as first:
            first.upsert_defects([make_defect()])
        with Database(path) as second:
            assert second.get_defect("d1") is not None
            assert len(second.defects_near(ORIGIN.lat, ORIGIN.lon, 50.0)) == 1
