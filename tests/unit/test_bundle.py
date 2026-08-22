"""Survey bundle ingest tests.

The bundle format is the contract between collector and processor, so these tests
double as its executable specification. The recurring theme: phones fail mid-recording,
and losing a 30-minute drive to a truncated log would be a self-inflicted wound.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from roadeye.geolocation.timesync import LocationSample
from roadeye.ingest.bundle import (
    BUNDLE_SCHEMA_VERSION,
    BundleError,
    load_bundle,
    write_bundle_skeleton,
)

START = dt.datetime(2026, 8, 18, 10, 42, 11, tzinfo=dt.UTC)
T0 = int(START.timestamp() * 1000)
LAT, LON = 40.18231, 44.51491


def samples(n: int = 10) -> list[LocationSample]:
    return [
        LocationSample(
            t_epoch_ms=T0 + i * 1000,
            lat=LAT,
            lon=LON + i * 0.000118,
            accuracy_m=5.0,
            speed_mps=10.0,
            heading_deg=90.0,
        )
        for i in range(n)
    ]


def make_bundle(tmp_path: Path, **kw) -> Path:
    return write_bundle_skeleton(
        tmp_path / "survey",
        survey_id=kw.pop("survey_id", "s001"),
        started_at=kw.pop("started_at", START),
        recording_start_epoch_ms=kw.pop("recording_start_epoch_ms", T0),
        samples=kw.pop("samples", samples()),
        **kw,
    )


class TestRoundTrip:
    def test_written_bundle_loads(self, tmp_path: Path):
        bundle = load_bundle(make_bundle(tmp_path))
        assert bundle.survey_id == "s001"
        assert bundle.schema_version == BUNDLE_SCHEMA_VERSION
        assert len(bundle.track) == 10
        assert bundle.recording_start_epoch_ms == T0

    def test_track_distance(self, tmp_path: Path):
        bundle = load_bundle(make_bundle(tmp_path))
        assert bundle.track.total_distance_m() == pytest.approx(90.0, abs=2.0)

    def test_device_metadata_preserved(self, tmp_path: Path):
        path = make_bundle(tmp_path, device={"model": "iPhone", "os": "iOS 19"})
        assert load_bundle(path).device["model"] == "iPhone"


class TestValidation:
    def test_missing_directory(self, tmp_path: Path):
        with pytest.raises(BundleError, match="not a directory"):
            load_bundle(tmp_path / "nope")

    def test_missing_route_json(self, tmp_path: Path):
        path = make_bundle(tmp_path)
        (path / "route.json").unlink()
        with pytest.raises(BundleError, match="route.json"):
            load_bundle(path)

    def test_missing_locations(self, tmp_path: Path):
        path = make_bundle(tmp_path)
        (path / "locations.jsonl").unlink()
        with pytest.raises(BundleError, match="locations.jsonl"):
            load_bundle(path)

    def test_malformed_route_json(self, tmp_path: Path):
        path = make_bundle(tmp_path)
        (path / "route.json").write_text("{not json", encoding="utf-8")
        with pytest.raises(BundleError, match="not valid JSON"):
            load_bundle(path)

    def test_missing_route_id(self, tmp_path: Path):
        path = make_bundle(tmp_path)
        (path / "route.json").write_text(
            json.dumps({"started_at": START.isoformat()}), encoding="utf-8"
        )
        with pytest.raises(BundleError, match="route_id"):
            load_bundle(path)

    @pytest.mark.parametrize("bad_id", ["../escape", "a/b", "with space", "semi;colon"])
    def test_rejects_unsafe_survey_ids(self, tmp_path: Path, bad_id: str):
        """Survey ids reach filesystem paths and export filenames. Path traversal must
        be refused at the boundary rather than sanitised at every use site."""
        path = make_bundle(tmp_path)
        route = json.loads((path / "route.json").read_text())
        route["route_id"] = bad_id
        (path / "route.json").write_text(json.dumps(route), encoding="utf-8")
        with pytest.raises(BundleError, match="unsafe characters"):
            load_bundle(path)

    def test_rejects_newer_schema_version(self, tmp_path: Path):
        """Guessing at a format we do not understand would silently misinterpret a
        survey — worse than refusing it."""
        path = make_bundle(tmp_path)
        (path / "manifest.json").write_text(
            json.dumps({"schema_version": BUNDLE_SCHEMA_VERSION + 5}), encoding="utf-8"
        )
        with pytest.raises(BundleError, match="newer than this build"):
            load_bundle(path)

    def test_newer_version_can_be_forced(self, tmp_path: Path):
        path = make_bundle(tmp_path)
        (path / "manifest.json").write_text(
            json.dumps({"schema_version": BUNDLE_SCHEMA_VERSION + 5}), encoding="utf-8"
        )
        bundle = load_bundle(path, strict_version=False)
        assert any("newer than this build" in w for w in bundle.warnings)


class TestDegradedBundles:
    def test_truncated_locations_survive(self, tmp_path: Path):
        """A force-quit app leaves a half-written final line. JSONL means we lose one
        sample, not the drive."""
        path = make_bundle(tmp_path)
        with (path / "locations.jsonl").open("a", encoding="utf-8") as fh:
            fh.write('{"t": 123, "lat": 40.1')
        bundle = load_bundle(path)
        assert len(bundle.track) == 10
        assert any("malformed line" in w for w in bundle.warnings)

    def test_missing_video_is_a_warning_not_an_error(self, tmp_path: Path):
        bundle = load_bundle(make_bundle(tmp_path))
        assert not bundle.has_video
        assert any("video.mp4 is missing" in w for w in bundle.warnings)
        assert not bundle.errors

    def test_missing_recording_anchor_warns_loudly(self, tmp_path: Path):
        """Falling back to started_at is acceptable but shifts every position in the
        survey by the camera's startup delay, so it must never be silent."""
        path = make_bundle(tmp_path)
        route = json.loads((path / "route.json").read_text())
        del route["recording_start_epoch_ms"]
        (path / "route.json").write_text(json.dumps(route), encoding="utf-8")
        bundle = load_bundle(path)
        assert bundle.recording_start_epoch_ms == T0
        assert any("recording_start_epoch_ms" in w for w in bundle.warnings)

    def test_all_bad_gps_is_an_error_not_a_crash(self, tmp_path: Path):
        bad = [
            LocationSample(t_epoch_ms=T0 + i * 1000, lat=LAT, lon=LON, accuracy_m=500.0)
            for i in range(5)
        ]
        bundle = load_bundle(make_bundle(tmp_path, samples=bad), max_accuracy_m=25.0)
        assert len(bundle.track) == 0
        assert any("cannot be geolocated" in e for e in bundle.errors)

    def test_unexpected_files_are_reported(self, tmp_path: Path):
        path = make_bundle(tmp_path)
        (path / "notes.txt").write_text("hello", encoding="utf-8")
        bundle = load_bundle(path)
        assert any("notes.txt" in w for w in bundle.warnings)

    def test_records_lacking_coordinates_are_counted(self, tmp_path: Path):
        path = make_bundle(tmp_path)
        with (path / "locations.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": T0 + 99_000}) + "\n")
        bundle = load_bundle(path)
        assert any("usable lat/lon" in w for w in bundle.warnings)
