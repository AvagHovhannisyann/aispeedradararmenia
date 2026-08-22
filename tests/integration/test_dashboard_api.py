"""Tests for what the municipal dashboard is served.

The dashboard's acceptance criterion is that a non-technical person understands it
unaided, which is not a thing a test can assert. What a test *can* pin is the set of
ways the data behind it could quietly mislead that person:

* a summary that reflects the active filter, so a filtered screen reads as the whole
  picture,
* probable and verified merged into one number,
* synthetic output presented without saying it is synthetic,
* warnings written in a language the reader may not have,
* a static route that can be walked out of into the survey data next door.
"""

from __future__ import annotations

import datetime as dt

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
from roadeye.domain.models import Defect, GeoPoint, Survey  # noqa: E402
from roadeye.storage.db import Database  # noqa: E402

NOW = dt.datetime(2026, 8, 18, 10, 42, 11, tzinfo=dt.UTC)


def make_defect(
    defect_id: str,
    *,
    status: DefectStatus = DefectStatus.PROBABLE,
    damage_class: DamageClass = DamageClass.POTHOLE,
    confidence: float = 0.8,
    severity: Severity = Severity.UNASSESSED,
    severity_source: SeveritySource = SeveritySource.OTHER,
    survey_id: str = "s1",
    model_id: str = "armenia_v001",
    lat: float = 40.185,
) -> Defect:
    return Defect(
        defect_id=defect_id,
        damage_class=damage_class,
        location=GeoPoint(
            lat=lat,
            lon=44.515,
            method=LocationMethod.INTERPOLATED_PHONE_GPS,
            uncertainty_m=8.0,
        ),
        confidence=confidence,
        severity=severity,
        severity_source=severity_source,
        status=status,
        first_seen=NOW,
        last_seen=NOW,
        survey_ids=[survey_id],
        observation_count=2,
        model_id=model_id,
    )


@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "d.db"
    with Database(db_path) as db:
        for survey_id in ("s1", "s2"):
            db.upsert_survey(
                Survey(
                    survey_id=survey_id,
                    started_at=NOW,
                    recording_start_epoch_ms=int(NOW.timestamp() * 1000),
                )
            )
        db.upsert_defects(
            [
                make_defect("d1", lat=40.1851),
                make_defect("d2", status=DefectStatus.VERIFIED, lat=40.1852),
                make_defect("d3", status=DefectStatus.REJECTED, lat=40.1853),
                make_defect(
                    "d4",
                    damage_class=DamageClass.ALLIGATOR_CRACK,
                    confidence=0.35,
                    lat=40.1854,
                ),
                make_defect(
                    "d5",
                    severity=Severity.HIGH,
                    severity_source=SeveritySource.HUMAN,
                    survey_id="s2",
                    lat=40.1855,
                ),
            ]
        )
    return TestClient(create_app(db_path))


def ids(payload) -> set[str]:
    return {f["properties"]["defect_id"] for f in payload["features"]}


class TestTheMapPayload:
    def test_it_is_plain_geojson(self, client):
        """Not a private format for a privileged client: the same bytes open in QGIS,
        which a municipality may well already own."""
        payload = client.get("/api/map").json()
        assert payload["type"] == "FeatureCollection"
        for feature in payload["features"]:
            assert feature["type"] == "Feature"
            assert feature["geometry"]["type"] == "Point"
            assert len(feature["geometry"]["coordinates"]) == 2
        # RoadEye's own metadata sits under a namespaced key, so a strict GeoJSON reader
        # ignores it rather than choking on it.
        assert set(payload) <= {"type", "features", "roadeye", "attribution"}

    def test_returns_every_status_by_default(self, client):
        """Unlike the review queue, which defaults to unreviewed. A map showing only
        probable defects would hide the reviewer's own completed work."""
        payload = client.get("/api/map").json()
        assert ids(payload) == {"d1", "d2", "d3", "d4", "d5"}

    def test_carries_what_the_panel_needs(self, client):
        properties = client.get("/api/map").json()["features"][0]["properties"]
        for key in (
            "defect_id",
            "damage_class",
            "status",
            "confidence",
            "severity",
            "severity_source",
            "location_uncertainty_m",
            "location_method",
            "road_name",
            "observation_count",
            "model_id",
            "processing_run_id",
        ):
            assert key in properties, key

    def test_coordinates_are_lon_lat(self, client):
        """GeoJSON is [longitude, latitude] — the reverse of how people say it, and the
        perennial source of maps that render in the ocean."""
        lon, lat = client.get("/api/map").json()["features"][0]["geometry"]["coordinates"]
        assert 44 < lon < 45
        assert 40 < lat < 41


class TestTheSummaryCannotMislead:
    def test_totals_are_over_everything_not_the_filter(self, client):
        """Otherwise a filtered screen reads as the whole picture: someone sees '1
        defect' on a map that is hiding four."""
        payload = client.get("/api/map?damage_class=alligator_crack").json()
        meta = payload["roadeye"]
        assert meta["shown"]["total"] == 1
        assert meta["totals"]["total"] == 5

    def test_probable_and_verified_stay_separate(self, client):
        """Conflating 'the AI found 5' with '5 defects exist' is the fastest way to lose
        a pilot, so no number ever contains both."""
        by_status = client.get("/api/map").json()["roadeye"]["totals"]["by_status"]
        assert by_status["probable"] == 3
        assert by_status["verified"] == 1
        assert by_status["rejected"] == 1

    def test_surveys_are_listed_for_the_filter(self, client):
        assert client.get("/api/map").json()["roadeye"]["surveys"] == ["s1", "s2"]


class TestFilters:
    def test_by_class(self, client):
        assert ids(client.get("/api/map?damage_class=alligator_crack").json()) == {"d4"}

    def test_by_status(self, client):
        assert ids(client.get("/api/map?status=verified").json()) == {"d2"}

    def test_by_survey(self, client):
        assert ids(client.get("/api/map?survey_id=s2").json()) == {"d5"}

    def test_by_confidence(self, client):
        assert "d4" not in ids(client.get("/api/map?min_confidence=0.5").json())

    def test_by_severity(self, client):
        assert ids(client.get("/api/map?severity=high").json()) == {"d5"}

    def test_an_unknown_filter_value_is_rejected_not_ignored(self, client):
        """Silently returning everything for a typo would show more than was asked
        for, which on this screen means showing defects a reader believes are filtered
        out."""
        assert client.get("/api/map?status=nonsense").status_code == 422


class TestProvenanceIsReported:
    def test_a_synthetic_detector_is_declared(self, tmp_path):
        """Synthetic markers on a real map of Yerevan look exactly like a working
        product. The CLI already warns; the dashboard is where it matters most."""
        db_path = tmp_path / "fake.db"
        with Database(db_path) as db:
            db.upsert_defects([make_defect("d1", model_id="fake-detector-v1")])
        meta = TestClient(create_app(db_path)).get("/api/map").json()["roadeye"]
        assert meta["provenance"]["synthetic"] is True
        assert any(w["code"] == "synthetic_detector" for w in meta["provenance"]["warnings"])

    def test_a_real_model_raises_no_synthetic_warning(self, client):
        provenance = client.get("/api/map").json()["roadeye"]["provenance"]
        assert provenance["synthetic"] is False
        assert not any(w["code"] == "synthetic_detector" for w in provenance["warnings"])

    def test_all_unverified_is_declared(self, tmp_path):
        db_path = tmp_path / "unver.db"
        with Database(db_path) as db:
            db.upsert_defects([make_defect("d1")])
        meta = TestClient(create_app(db_path)).get("/api/map").json()["roadeye"]
        assert any(w["code"] == "none_verified" for w in meta["provenance"]["warnings"])

    def test_warnings_are_codes_not_prose(self, tmp_path):
        """The dashboard's first language is Armenian. An English sentence built on the
        server arrives untranslatable — and the honesty warnings are the last thing that
        should fall back to a language the reader may not have."""
        db_path = tmp_path / "fake.db"
        with Database(db_path) as db:
            db.upsert_defects([make_defect("d1", model_id="fake-detector-v1")])
        warnings = (
            TestClient(create_app(db_path))
            .get("/api/map")
            .json()["roadeye"]["provenance"]["warnings"]
        )
        assert warnings
        for warning in warnings:
            assert isinstance(warning, dict)
            assert "code" in warning


class TestRoads:
    def test_no_network_configured_returns_an_empty_collection(self, client):
        """Not a 404: the dashboard treats streets as optional decoration and must not
        have to tell 'no roads file' apart from 'the request failed'."""
        payload = client.get("/api/roads").json()
        assert payload["features"] == []

    def test_a_network_is_served_as_lines_with_attribution(self, tmp_path):
        from roadeye.geolocation.geodesy import LatLon
        from roadeye.map_matching.network import (
            NetworkProvenance,
            RoadNetwork,
            RoadSegment,
        )

        network = RoadNetwork(
            segments=[
                RoadSegment(
                    segment_id="way/1#0",
                    way_id="way/1",
                    start=LatLon(40.185, 44.515),
                    end=LatLon(40.186, 44.515),
                    name="Mashtots Avenue",
                ),
                RoadSegment(
                    segment_id="way/1#1",
                    way_id="way/1",
                    start=LatLon(40.186, 44.515),
                    end=LatLon(40.187, 44.515),
                    name="Mashtots Avenue",
                ),
            ],
            provenance=NetworkProvenance(
                source="osm",
                license="ODbL-1.0",
                attribution="© OpenStreetMap contributors",
                retrieved_at=NOW,
            ),
        )
        roads_path = network.save(tmp_path / "roads.json")

        db_path = tmp_path / "d.db"
        with Database(db_path) as db:
            db.upsert_defects([make_defect("d1")])

        payload = TestClient(create_app(db_path, None, roads_path)).get("/api/roads").json()
        # One feature per way, not per segment: an order of magnitude fewer lines to draw.
        assert len(payload["features"]) == 1
        assert payload["features"][0]["geometry"]["type"] == "LineString"
        assert len(payload["features"][0]["geometry"]["coordinates"]) == 3
        assert "OpenStreetMap" in payload["attribution"]


class TestStaticRoutes:
    def test_the_dashboard_page_is_served(self, client):
        response = client.get("/dashboard")
        assert response.status_code == 200
        assert "dashboard.js" in response.text

    def test_its_assets_are_served(self, client):
        assert client.get("/static/dashboard.css").status_code == 200
        assert client.get("/static/dashboard.js").status_code == 200

    def test_unlisted_assets_are_refused(self, client):
        """Whitelisted by name rather than mounted as a directory. This app also serves
        an evidence directory of survey imagery, and a static mount is one
        misconfiguration away from serving the wrong tree."""
        assert client.get("/static/app.py").status_code == 404
        assert client.get("/static/nope.js").status_code == 404

    def test_the_review_queue_is_still_the_root(self, client):
        """The dashboard is an addition, not a replacement: review speed is the
        product's bottleneck and the keyboard UI is what serves it."""
        assert "review" in client.get("/").text.lower()
