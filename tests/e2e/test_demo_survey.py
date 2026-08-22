"""Tests for the demo-survey generator.

The demo script is the first thing a new person runs, so it breaking is worse than an
obscure module breaking: it is the difference between "this project works" and "this
project does not start". These tests keep it honest.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from roadeye.geolocation.geodesy import LatLon, haversine_m
from roadeye.ingest.bundle import load_bundle
from roadeye.pipeline import process_survey
from roadeye.storage.db import Database
from roadeye.vision.fake import FakeDetector

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "make_demo_survey.py"

pytestmark = pytest.mark.skipif(not SCRIPT.exists(), reason="demo script not present")


def _load_module():
    """Import the script by path — it lives in scripts/, not the installed package."""
    spec = importlib.util.spec_from_file_location("make_demo_survey", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["make_demo_survey"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def demo():
    return _load_module()


class TestTrackGeneration:
    def test_produces_a_closed_circuit(self, demo):
        """The route returns to its start, which is what makes the exported geometry
        eyeball-checkable on a map."""
        samples = demo.build_track(1_787_049_731_000, include_stop=False)
        assert len(samples) > 100
        start = LatLon(samples[0].lat, samples[0].lon)
        end = LatLon(samples[-1].lat, samples[-1].lon)
        assert haversine_m(start, end) < 15.0

    def test_route_lands_in_yerevan(self, demo):
        """A route through (0, 0) would hide latitude/longitude swaps."""
        for s in demo.build_track(1_787_049_731_000):
            assert 40.0 < s.lat < 41.0
            assert 44.0 < s.lon < 45.0

    def test_includes_a_stationary_period(self, demo):
        """The red-light pause is the point of the fixture — it exercises the
        stationary guard that stops a stop from generating hundreds of duplicates."""
        samples = demo.build_track(1_787_049_731_000, include_stop=True)
        assert any(s.speed_mps == 0.0 for s in samples)

    def test_stop_can_be_omitted(self, demo):
        samples = demo.build_track(1_787_049_731_000, include_stop=False)
        assert all(s.speed_mps != 0.0 for s in samples)

    def test_timestamps_are_monotonic(self, demo):
        samples = demo.build_track(1_787_049_731_000)
        times = [s.t_epoch_ms for s in samples]
        assert times == sorted(times)


class TestGeneratedBundle:
    def test_bundle_validates_and_processes(self, demo, tmp_path: Path):
        """The whole point: a newcomer can run the full chain before owning a phone."""
        demo.main.__globals__["sys"].argv = ["demo", str(tmp_path / "survey")]
        rc = demo.main()
        assert rc == 0

        bundle = load_bundle(tmp_path / "survey")
        assert not bundle.errors
        assert len(bundle.track) > 100
        # ~2.4 km circuit.
        assert 2000 < bundle.track.total_distance_m() < 2800

    def test_full_pipeline_produces_deduplicated_defects(self, demo, tmp_path: Path):
        demo.main.__globals__["sys"].argv = ["demo", str(tmp_path / "survey")]
        demo.main()

        db = Database(tmp_path / "demo.db")
        try:
            result = process_survey(tmp_path / "survey", FakeDetector(), db=db)

            assert not result.run.errors
            assert result.run.detections > 0
            # Deduplication must actually reduce the count, or the product claim that
            # one defect equals one repairable thing is unsupported.
            assert len(result.defects) < result.run.detections

            # The stationary period must be skipped rather than generating duplicates.
            assert any("stationary" in w for w in result.run.warnings)

            for defect in result.defects:
                assert 40.0 < defect.location.lat < 41.0
                assert 44.0 < defect.location.lon < 45.0
                assert defect.location.uncertainty_m >= 5.0
                assert defect.model_id == "fake-detector-v1"
                assert defect.processing_run_id == result.run.run_id
        finally:
            db.close()
