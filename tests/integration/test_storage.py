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

NOW = dt.datetime(2026, 8, 18, 10, 42, 11, tzinfo=dt.UTC)
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
            [
                make_defect(
                    status=DefectStatus.VERIFIED,
                    severity=Severity.HIGH,
                    severity_source=SeveritySource.HUMAN,
                )
            ]
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


class TestUpsertPersistsEveryMutableField:
    """Regression: a column missing from the ON CONFLICT clause is silently dropped.

    ``damage_class`` was absent, so a reviewer correcting a misclassified defect got a
    success response and an entry in the append-only review log — while the defect kept
    its original class. That silently discards the single most valuable output of the
    whole review loop, and nothing surfaces the loss.

    This test walks every mutable field rather than checking one, so the next column
    added to the schema and forgotten in the update clause fails here.
    """

    def test_damage_class_survives_an_update(self, db: Database):
        db.upsert_defects([make_defect(damage_class=DamageClass.POTHOLE)])
        db.upsert_defects([make_defect(damage_class=DamageClass.ALLIGATOR_CRACK)])
        assert db.get_defect("d1").damage_class is DamageClass.ALLIGATOR_CRACK

    def test_every_mutable_field_survives_an_update(self, db: Database):
        from roadeye.domain.enums import DefectTrend
        from roadeye.domain.models import RoadSegmentRef

        db.upsert_defects([make_defect()])

        moved = destination_point(ORIGIN, 270.0, 400.0)
        updated = make_defect(
            damage_class=DamageClass.TRANSVERSE_CRACK,
            location=GeoPoint(
                lat=moved.lat,
                lon=moved.lon,
                method=LocationMethod.MANUAL_CORRECTION,
                uncertainty_m=1.5,
            ),
            road=RoadSegmentRef(
                source="osm",
                segment_id="way/12345",
                name="Abovyan St",
                match_distance_m=4.25,
                heading_delta_deg=7.5,
            ),
            confidence=0.42,
            severity=Severity.HIGH,
            severity_source=SeveritySource.HUMAN,
            status=DefectStatus.VERIFIED,
            trend=DefectTrend.WORSENING,
            observation_count=9,
            survey_ids=["aug18", "sep08"],
            representative_frame_id="s1:f9.5",
            representative_image_path="d1_context.jpg",
            model_id="armenia_v001",
            processing_run_id="run_zzz",
        )
        db.upsert_defects([updated])

        stored = db.get_defect("d1")
        assert stored.damage_class is DamageClass.TRANSVERSE_CRACK
        assert stored.location.method is LocationMethod.MANUAL_CORRECTION
        assert stored.location.uncertainty_m == pytest.approx(1.5)
        assert stored.road is not None and stored.road.segment_id == "way/12345"
        assert stored.road.name == "Abovyan St"
        # Match quality, not just the match. "On Abovyan St" and "19 m from Abovyan St
        # and nothing else was near" are different claims; dropping these on write would
        # make every match look equally good.
        assert stored.road.match_distance_m == pytest.approx(4.25)
        assert stored.road.heading_delta_deg == pytest.approx(7.5)
        assert stored.confidence == pytest.approx(0.42)
        assert stored.severity is Severity.HIGH
        assert stored.severity_source is SeveritySource.HUMAN
        assert stored.status is DefectStatus.VERIFIED
        assert stored.trend is DefectTrend.WORSENING
        assert stored.observation_count == 9
        assert stored.survey_ids == ["aug18", "sep08"]
        assert stored.representative_frame_id == "s1:f9.5"
        assert stored.representative_image_path == "d1_context.jpg"
        assert stored.model_id == "armenia_v001"
        assert stored.processing_run_id == "run_zzz"

    def test_first_seen_does_not_move_when_seen_again(self, db: Database):
        """first_seen records when a defect was FIRST observed. A later survey seeing
        it again must not rewrite its history."""
        original = make_defect(first_seen=NOW, last_seen=NOW)
        db.upsert_defects([original])

        later = NOW + dt.timedelta(days=21)
        db.upsert_defects([make_defect(first_seen=later, last_seen=later)])

        stored = db.get_defect("d1")
        assert stored.first_seen == NOW, "first_seen must not move"
        assert stored.last_seen == later, "last_seen must advance"


class TestHeadingsForMatching:
    def test_representative_headings_reads_the_frame(self, db: Database):
        """Map matching needs to know which way the vehicle was pointing; at a
        crossroads the nearest centreline is often the street nobody drove."""
        from roadeye.domain.models import Frame

        db.upsert_survey(make_survey())
        db.insert_frames(
            [
                Frame(
                    frame_id="s1:f1",
                    survey_id="s1",
                    video_time_s=1.0,
                    t_epoch_ms=int(NOW.timestamp() * 1000),
                    heading_deg=87.5,
                ),
                Frame(
                    frame_id="s1:f2",
                    survey_id="s1",
                    video_time_s=2.0,
                    t_epoch_ms=int(NOW.timestamp() * 1000) + 1000,
                ),
            ]
        )
        db.upsert_defects(
            [
                make_defect("with_heading", representative_frame_id="s1:f1"),
                make_defect("without_heading", representative_frame_id="s1:f2"),
                make_defect("no_frame"),
            ]
        )

        headings = db.representative_headings()
        assert headings == {"with_heading": 87.5}, (
            "a defect whose frame recorded no heading must be absent, not defaulted — "
            "a made-up heading would silently drive the match"
        )


class TestSchemaMigration:
    """CREATE TABLE IF NOT EXISTS does nothing to a table that already exists, so a new
    column reaches an existing database only through an explicit ALTER. Without one, a
    pilot database from last month crashes on the first write."""

    def test_a_v1_database_gains_the_new_columns(self, tmp_path):
        import sqlite3

        path = tmp_path / "legacy.db"
        with Database(path) as database:
            database.upsert_defects([make_defect()])

        # Rewind to v1: drop the columns added in v2 and reset the version marker.
        conn = sqlite3.connect(path)
        conn.execute("ALTER TABLE defects DROP COLUMN road_match_distance_m")
        conn.execute("ALTER TABLE defects DROP COLUMN road_heading_delta_deg")
        conn.execute("UPDATE meta SET value = '1' WHERE key = 'schema_version'")
        conn.commit()
        conn.close()

        with Database(path) as reopened:
            assert reopened.get_defect("d1") is not None
            columns = {
                row[1]
                for row in reopened._conn.execute("PRAGMA table_info(defects)")  # noqa: SLF001
            }
            assert {"road_match_distance_m", "road_heading_delta_deg"} <= columns
            version = reopened._conn.execute(  # noqa: SLF001
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()["value"]
            assert version == "2"

    def test_a_newer_database_is_refused_rather_than_guessed_at(self, tmp_path):
        import sqlite3

        path = tmp_path / "future.db"
        with Database(path):
            pass
        conn = sqlite3.connect(path)
        conn.execute("UPDATE meta SET value = '999' WHERE key = 'schema_version'")
        conn.commit()
        conn.close()

        with pytest.raises(RuntimeError, match="newer than this build"):
            Database(path)
