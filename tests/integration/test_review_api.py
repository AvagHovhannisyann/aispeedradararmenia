"""Tests for the review API.

The review loop is where human judgement enters the system, so the failure modes are
about *losing* that judgement rather than crashing: a correction that reports success
but does not persist, a severity that lands without its source, evidence served from
outside its directory. Those are what these tests pin.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

pytest.importorskip("fastapi", reason="the 'api' extra is not installed")
pytest.importorskip("httpx", reason="httpx is needed by fastapi's TestClient")

from fastapi.testclient import TestClient  # noqa: E402
from services.api.app import create_app  # noqa: E402

from roadeye.domain.enums import (  # noqa: E402
    DamageClass,
    DefectStatus,
    LocationMethod,
    Severity,
    SeveritySource,
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
from roadeye.storage.db import Database  # noqa: E402

NOW = dt.datetime(2026, 8, 18, 10, 42, 11, tzinfo=dt.UTC)
LAT, LON = 40.18231, 44.51491


@pytest.fixture
def populated(tmp_path):
    """A database with three probable defects, each with evidence images."""
    db_path = tmp_path / "review.db"
    evidence = tmp_path / "evidence"
    evidence.mkdir()

    from PIL import Image

    with Database(db_path) as db:
        db.upsert_survey(
            Survey(
                survey_id="s1",
                started_at=NOW,
                recording_start_epoch_ms=int(NOW.timestamp() * 1000),
            )
        )
        for i in range(1, 4):
            frame_id = f"s1:f{i}"
            db.insert_frames(
                [
                    Frame(
                        frame_id=frame_id,
                        survey_id="s1",
                        video_time_s=float(i),
                        t_epoch_ms=int(NOW.timestamp() * 1000) + i * 1000,
                        width=200,
                        height=200,
                    )
                ]
            )
            db.insert_detections(
                [
                    Detection(
                        detection_id=f"det{i}",
                        frame_id=frame_id,
                        survey_id="s1",
                        damage_class=DamageClass.POTHOLE,
                        confidence=0.8,
                        bbox=BoundingBox(x1=20, y1=30, x2=90, y2=100),
                        model_id="m1",
                    )
                ]
            )
            defect_id = f"def_{i:05d}"
            db.upsert_defects(
                [
                    Defect(
                        defect_id=defect_id,
                        damage_class=DamageClass.POTHOLE,
                        location=GeoPoint(
                            lat=LAT + i * 0.001,
                            lon=LON,
                            method=LocationMethod.INTERPOLATED_PHONE_GPS,
                            uncertainty_m=6.0,
                        ),
                        confidence=0.8,
                        first_seen=NOW,
                        last_seen=NOW,
                        survey_ids=["s1"],
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
                        survey_id="s1",
                        detection_ids=[f"det{i}"],
                        observed_at=NOW,
                        confidence=0.8,
                        location=GeoPoint(
                            lat=LAT + i * 0.001,
                            lon=LON,
                            method=LocationMethod.INTERPOLATED_PHONE_GPS,
                            uncertainty_m=6.0,
                        ),
                        representative_frame_id=frame_id,
                    )
                ]
            )
            for kind in ("frame", "context", "crop"):
                Image.new("RGB", (200, 200), (60, 60, 60)).save(
                    evidence / f"{defect_id}_{kind}.jpg"
                )

    return db_path, evidence


@pytest.fixture
def client(populated):
    db_path, evidence = populated
    return TestClient(create_app(db_path, evidence))


class TestQueue:
    def test_returns_pending_defects_with_everything_the_ui_needs(self, client):
        data = client.get("/api/queue").json()
        assert data["pending"] == 3
        item = data["items"][0]
        for key in (
            "defect_id",
            "damage_class",
            "confidence",
            "uncertainty_m",
            "observation_count",
            "context_image",
            "crop_image",
            "model_id",
            "representative_frame_id",
        ):
            assert key in item, key

    def test_only_probable_defects_are_queued(self, client):
        client.post("/api/defects/def_00001/review", json={"action": "approve"})
        assert client.get("/api/queue").json()["pending"] == 2

    def test_ui_page_is_served(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "RoadEye review" in response.text


class TestEvidence:
    def test_serves_an_image(self, client):
        response = client.get("/api/evidence/def_00001_context.jpg")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"

    @pytest.mark.parametrize(
        "attempt",
        ["../../../etc/passwd", "..%2f..%2fetc%2fpasswd", "/etc/passwd", "....//etc/passwd"],
    )
    def test_path_traversal_is_refused(self, client, attempt):
        """Evidence sits beside a database of survey data; a filename from a URL is
        untrusted input."""
        assert client.get(f"/api/evidence/{attempt}").status_code == 404

    def test_non_image_is_refused(self, client, populated):
        _, evidence = populated
        (evidence / "secrets.txt").write_text("nope", encoding="utf-8")
        assert client.get("/api/evidence/secrets.txt").status_code == 404


class TestDecisions:
    def test_approve_verifies(self, client):
        response = client.post("/api/defects/def_00001/review", json={"action": "approve"})
        assert response.json()["status"] == "verified"

    def test_reject(self, client):
        response = client.post("/api/defects/def_00001/review", json={"action": "reject"})
        assert response.json()["status"] == "rejected"

    def test_class_correction_persists(self, client, populated):
        """Regression: the correction must survive the write.

        ``damage_class`` was missing from the storage upsert clause, so the API
        returned success and logged the change while the defect kept its old class —
        silently discarding the most valuable output of review.
        """
        client.post(
            "/api/defects/def_00001/review",
            json={"action": "change_class", "damage_class": "alligator_crack"},
        )
        db_path, _ = populated
        with Database(db_path) as db:
            assert db.get_defect("def_00001").damage_class is DamageClass.ALLIGATOR_CRACK

    def test_severity_always_records_its_source(self, client, populated):
        """A severity without a source is the false authority the domain model forbids;
        setting the two separately would briefly build an invalid defect."""
        response = client.post(
            "/api/defects/def_00001/review",
            json={"action": "change_severity", "severity": "high"},
        )
        assert response.status_code == 200
        db_path, _ = populated
        with Database(db_path) as db:
            defect = db.get_defect("def_00001")
            assert defect.severity is Severity.HIGH
            assert defect.severity_source is SeveritySource.HUMAN

    def test_location_correction_is_marked_manual(self, client, populated):
        client.post(
            "/api/defects/def_00001/review",
            json={"action": "adjust_location", "lat": 40.2, "lon": 44.6},
        )
        db_path, _ = populated
        with Database(db_path) as db:
            defect = db.get_defect("def_00001")
            assert defect.location.method is LocationMethod.MANUAL_CORRECTION
            assert defect.location.lat == pytest.approx(40.2)

    def test_reviews_are_append_only(self, client, populated):
        """Each decision adds a row; none replaces another. The defect shows current
        state, the log shows how it got there."""
        for payload in (
            {"action": "approve"},
            {"action": "change_severity", "severity": "medium"},
            {"action": "change_class", "damage_class": "pothole"},
        ):
            client.post("/api/defects/def_00001/review", json=payload)

        db_path, _ = populated
        with Database(db_path) as db:
            rows = db.reviews_for("def_00001")
        assert len(rows) == 3
        assert [r["action"] for r in rows] == ["approve", "change_severity", "change_class"]

    def test_review_records_before_and_after(self, client, populated):
        client.post(
            "/api/defects/def_00001/review",
            json={
                "action": "change_class",
                "damage_class": "transverse_crack",
                "note": "hairline, not a hole",
            },
        )
        db_path, _ = populated
        with Database(db_path) as db:
            row = db.reviews_for("def_00001")[0]
        assert json.loads(row["previous_value_json"])["damage_class"] == "pothole"
        assert json.loads(row["new_value_json"])["damage_class"] == "transverse_crack"
        assert row["note"] == "hairline, not a hole"


class TestValidation:
    def test_unknown_defect(self, client):
        assert (
            client.post("/api/defects/nope/review", json={"action": "approve"}).status_code == 404
        )

    def test_change_class_requires_a_class(self, client):
        response = client.post("/api/defects/def_00001/review", json={"action": "change_class"})
        assert response.status_code == 422

    def test_change_severity_requires_a_severity(self, client):
        response = client.post("/api/defects/def_00001/review", json={"action": "change_severity"})
        assert response.status_code == 422

    def test_unknown_fields_are_rejected(self, client):
        response = client.post(
            "/api/defects/def_00001/review", json={"action": "approve", "bogus": 1}
        )
        assert response.status_code == 422

    def test_invalid_coordinates_are_rejected(self, client):
        response = client.post(
            "/api/defects/def_00001/review",
            json={"action": "adjust_location", "lat": 999.0, "lon": 44.0},
        )
        assert response.status_code == 422

    def test_no_machine_path_writes_verified(self, client, populated):
        """Only a human decision may move a defect past PROBABLE (ADR-006). Reading
        the queue must never change state."""
        client.get("/api/queue")
        db_path, _ = populated
        with Database(db_path) as db:
            assert all(d.status is DefectStatus.PROBABLE for d in db.list_defects())
