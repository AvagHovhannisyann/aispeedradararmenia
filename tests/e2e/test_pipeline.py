"""End-to-end pipeline tests.

Runs the whole chain — bundle -> sampling -> detection -> tracking -> geolocation ->
clustering -> storage -> export — with no GPU, no ffmpeg, no model weights and no
network. Everything is synthetic and deterministic.

This is the test that would catch a regression in the product's central claim: that a
drive past one pothole produces one defect on a map, with evidence attached.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from roadeye.domain.enums import DamageClass, DefectStatus, Severity
from roadeye.geolocation.timesync import LocationSample
from roadeye.ingest.bundle import load_bundle, write_bundle_skeleton
from roadeye.pipeline import PipelineConfig, process_bundle, process_survey
from roadeye.reporting.export import summarize, to_csv, to_geojson
from roadeye.storage.db import Database
from roadeye.video.decoder import SyntheticFrameSource
from roadeye.video.sampling import SamplingConfig
from roadeye.vision.base import RawDetection
from roadeye.vision.fake import FakeDetector, NullDetector, ScriptedDetector

START = dt.datetime(2026, 8, 18, 10, 42, 11, tzinfo=dt.UTC)
T0 = int(START.timestamp() * 1000)
LAT, LON = 40.18231, 44.51491
LON_PER_10M = 0.000118


def drive(seconds: int = 30, speed_mps: float = 10.0) -> list[LocationSample]:
    return [
        LocationSample(
            t_epoch_ms=T0 + i * 1000,
            lat=LAT,
            lon=LON + (i * speed_mps / 10.0) * LON_PER_10M,
            accuracy_m=5.0,
            speed_mps=speed_mps,
            heading_deg=90.0,
        )
        for i in range(seconds + 1)
    ]


@pytest.fixture
def bundle(tmp_path: Path):
    path = write_bundle_skeleton(
        tmp_path / "survey_e2e",
        survey_id="e2e001",
        started_at=START,
        recording_start_epoch_ms=T0,
        samples=drive(30),
        ended_at=START + dt.timedelta(seconds=30),
        device={"model": "synthetic", "os": "test"},
    )
    return load_bundle(path)


class TestHappyPath:
    def test_pipeline_completes(self, bundle):
        result = process_bundle(bundle, FakeDetector())
        assert result.run.finished_at is not None
        assert result.run.frames_sampled > 0
        assert result.run.duration_s is not None
        assert not result.run.errors

    def test_every_frame_gets_a_position(self, bundle):
        """If a frame cannot be placed, its detections cannot become map defects."""
        result = process_bundle(bundle, FakeDetector())
        assert result.frames
        assert all(f.observation_location is not None for f in result.frames)
        assert all(f.observation_location.uncertainty_m > 0 for f in result.frames)

    def test_frame_times_are_anchored_to_the_recording_start(self, bundle):
        result = process_bundle(bundle, FakeDetector())
        for frame in result.frames:
            assert frame.t_epoch_ms == pytest.approx(T0 + frame.video_time_s * 1000, abs=1)

    def test_run_records_full_provenance(self, bundle):
        """A defect must be able to answer 'why do you believe this exists?'."""
        result = process_bundle(bundle, FakeDetector())
        assert result.run.model_id == "fake-detector-v1"
        assert result.run.config["sampling"]["mode"] == "distance"
        assert result.run.config["clustering"]["max_cluster_extent_m"] > 0
        for defect in result.defects:
            assert defect.processing_run_id == result.run.run_id
            assert defect.model_id == "fake-detector-v1"


class TestDeduplication:
    def test_one_pothole_seen_repeatedly_becomes_one_defect(self, bundle):
        """The product's central claim, end to end.

        A pothole is scripted into consecutive frames, growing and drifting downward
        exactly as it would as a car approaches. The pipeline must report ONE defect.
        """
        plan_times = [round(i * 0.25, 3) for i in range(12)]
        script = {
            f"e2e001:f{t:.3f}": [
                RawDetection(
                    damage_class=DamageClass.POTHOLE,
                    confidence=0.6 + i * 0.03,
                    x1=900.0 + i * 8,
                    y1=700.0 + i * 20,
                    x2=1000.0 + i * 20,
                    y2=800.0 + i * 32,
                )
            ]
            for i, t in enumerate(plan_times)
        }
        source = SyntheticFrameSource(duration_s=3.0, survey_id="e2e001")
        result = process_bundle(
            bundle,
            ScriptedDetector(script),
            frame_source=source,
            config=PipelineConfig(sampling=SamplingConfig(target_spacing_m=2.5, fallback_fps=4.0)),
        )
        assert result.run.detections >= 10
        assert len(result.defects) == 1, (
            f"{result.run.detections} detections of ONE pothole produced "
            f"{len(result.defects)} defects"
        )
        assert result.defects[0].damage_class is DamageClass.POTHOLE

    def test_defect_retains_links_to_its_evidence(self, bundle):
        plan_times = [round(i * 0.25, 3) for i in range(8)]
        script = {
            f"e2e001:f{t:.3f}": [
                RawDetection(
                    damage_class=DamageClass.POTHOLE,
                    confidence=0.8,
                    x1=900.0 + i * 6,
                    y1=700.0 + i * 15,
                    x2=1010.0 + i * 6,
                    y2=810.0 + i * 15,
                )
            ]
            for i, t in enumerate(plan_times)
        }
        result = process_bundle(
            bundle,
            ScriptedDetector(script),
            frame_source=SyntheticFrameSource(duration_s=2.0, survey_id="e2e001"),
            config=PipelineConfig(sampling=SamplingConfig(target_spacing_m=2.5, fallback_fps=4.0)),
        )
        assert result.observations
        observation = result.observations[0]
        assert observation.defect_id == result.defects[0].defect_id
        assert observation.detection_ids
        assert observation.representative_frame_id is not None

    def test_defects_never_exceed_detections(self, bundle):
        result = process_bundle(bundle, FakeDetector(detections_per_frame=2))
        assert len(result.defects) <= result.run.detections


class TestHonestOutputs:
    def test_machine_output_is_only_ever_probable(self, bundle):
        """No automated path may mark a defect verified. A municipality acting on
        unverified output is the failure mode that ends the pilot."""
        result = process_bundle(bundle, FakeDetector())
        assert all(d.status is DefectStatus.PROBABLE for d in result.defects)
        assert all(d.severity is Severity.UNASSESSED for d in result.defects)

    def test_uncertainty_is_never_fabricated(self, bundle):
        """Combining correlated GPS fixes must not produce sub-metre confidence."""
        result = process_bundle(bundle, FakeDetector())
        for defect in result.defects:
            assert defect.location.uncertainty_m >= 5.0

    def test_clean_road_produces_a_valid_empty_result(self, bundle):
        """A road with no defects is a legitimate and common outcome. The pipeline and
        every export must handle it without crashing or emitting a malformed file."""
        result = process_bundle(bundle, NullDetector())
        assert result.defects == []
        assert result.run.detections == 0
        assert not result.run.errors
        assert summarize(result.defects)["total"] == 0
        assert to_geojson(result.defects)["features"] == []

    def test_survey_without_gps_refuses_to_invent_positions(self, tmp_path: Path):
        """Better to emit nothing than to place defects on a map by guesswork."""
        path = write_bundle_skeleton(
            tmp_path / "nogps",
            survey_id="nogps01",
            started_at=START,
            recording_start_epoch_ms=T0,
            samples=[
                LocationSample(t_epoch_ms=T0 + i * 1000, lat=LAT, lon=LON, accuracy_m=900.0)
                for i in range(10)
            ],
        )
        result = process_bundle(load_bundle(path), FakeDetector())
        assert result.defects == []
        assert any("no usable GPS" in e for e in result.run.errors)


class TestStorageAndExport:
    def test_process_survey_persists_everything(self, tmp_path: Path, bundle):
        db = Database(tmp_path / "roadeye.db")
        try:
            result = process_survey(bundle.path, FakeDetector(), db=db)
            assert db.count("surveys") == 1
            assert db.count("frames") == len(result.frames)
            assert db.count("detections") == len(result.detections)
            assert db.count("defects") == len(result.defects)
            assert db.count("processing_runs") == 1

            stored = db.list_defects()
            assert len(stored) == len(result.defects)
            if stored:
                assert db.defects_near(stored[0].location.lat, stored[0].location.lon, 50.0)
        finally:
            db.close()

    def test_csv_export_has_uncertainty_and_status(self, tmp_path: Path, bundle):
        """An export that omits these looks authoritative and is not."""
        result = process_bundle(bundle, FakeDetector())
        out = to_csv(result.defects, tmp_path / "defects.csv")
        text = out.read_text(encoding="utf-8")
        assert "location_uncertainty_m" in text
        assert "status" in text
        assert "severity_source" in text
        assert "model_id" in text

    def test_geojson_is_valid_and_lon_lat_ordered(self, tmp_path: Path, bundle):
        """GeoJSON is [lon, lat]. Getting it backwards renders Yerevan in the ocean."""
        result = process_bundle(bundle, FakeDetector())
        path = tmp_path / "defects.geojson"
        to_geojson(result.defects, path, attribution="© OpenStreetMap contributors")
        data = json.loads(path.read_text(encoding="utf-8"))

        assert data["type"] == "FeatureCollection"
        assert data["attribution"]
        assert "not been verified" in data["roadeye"]["notice"].lower()
        for feature in data["features"]:
            lon, lat = feature["geometry"]["coordinates"]
            assert 44.0 < lon < 45.0, "longitude and latitude appear swapped"
            assert 40.0 < lat < 41.0

    def test_reprocessing_is_idempotent(self, tmp_path: Path, bundle):
        """Running the same survey twice must not double the defect count."""
        db = Database(tmp_path / "roadeye.db")
        try:
            config = PipelineConfig()
            process_survey(bundle.path, FakeDetector(), db=db, config=config)
            first = db.count("defects")
            process_survey(bundle.path, FakeDetector(), db=db, config=config)
            assert db.count("defects") == first
        finally:
            db.close()


class TestDeterminism:
    def test_same_input_gives_same_defects(self, bundle):
        """Provenance is worthless if a rerun produces different answers."""
        a = process_bundle(bundle, FakeDetector())
        b = process_bundle(bundle, FakeDetector())
        assert [d.defect_id for d in a.defects] == [d.defect_id for d in b.defects]
        assert [round(d.location.lat, 9) for d in a.defects] == [
            round(d.location.lat, 9) for d in b.defects
        ]
