"""Contract tests between the collector app and the Python processor.

The survey bundle format is written by TypeScript and read by Python. Nothing but
discipline keeps the two ends in step, so these tests turn that discipline into a
failing build.

They parse the TypeScript source as text rather than executing it — Node is not
guaranteed to be present, and the point is to catch the two definitions drifting
apart, which a text check does perfectly well.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from roadeye.ingest.bundle import BUNDLE_SCHEMA_VERSION, load_bundle

REPO_ROOT = Path(__file__).resolve().parents[2]
COLLECTOR = REPO_ROOT / "apps" / "collector"
SURVEY_TS = COLLECTOR / "src" / "survey.ts"
APP_TSX = COLLECTOR / "App.tsx"

pytestmark = pytest.mark.skipif(
    not SURVEY_TS.exists(), reason="collector app not present in this checkout"
)


class TestSchemaVersionAgreement:
    def test_typescript_and_python_agree(self):
        """A version mismatch means the processor silently misreads real surveys."""
        source = SURVEY_TS.read_text(encoding="utf-8")
        match = re.search(r"BUNDLE_SCHEMA_VERSION\s*=\s*(\d+)", source)
        assert match, "BUNDLE_SCHEMA_VERSION not found in apps/collector/src/survey.ts"
        assert int(match.group(1)) == BUNDLE_SCHEMA_VERSION, (
            f"collector writes schema_version {match.group(1)} but the processor "
            f"expects {BUNDLE_SCHEMA_VERSION}"
        )


class TestFieldNameAgreement:
    """The processor reads these exact keys. A rename on either side breaks ingest."""

    @pytest.mark.parametrize(
        "field",
        ["route_id", "started_at", "recording_start_epoch_ms", "schema_version"],
    )
    def test_route_fields_present(self, field: str):
        assert field in SURVEY_TS.read_text(encoding="utf-8"), (
            f"collector no longer writes route field '{field}'"
        )

    @pytest.mark.parametrize("field", ["accuracy_m", "speed_mps", "heading_deg"])
    def test_location_fields_present(self, field: str):
        assert field in SURVEY_TS.read_text(encoding="utf-8"), (
            f"collector no longer writes location field '{field}'"
        )

    def test_location_uses_short_time_key(self):
        """Locations use 't', not 'timestamp' — matches _parse_location()."""
        assert re.search(r"\bt:\s*number", SURVEY_TS.read_text(encoding="utf-8"))

    def test_filenames_match(self):
        source = SURVEY_TS.read_text(encoding="utf-8")
        for filename in ("route.json", "locations.jsonl", "manifest.json", "video.mp4"):
            assert filename in source, f"collector no longer writes {filename}"


class TestTimeAnchorHandling:
    """Regression: the recording anchor must be captured once and never recomputed.

    An earlier draft rewrote ``recording_start_epoch_ms`` from ``started_at`` when the
    survey stopped. Those are different instants — the user tapping START and the
    camera actually beginning — and conflating them offsets every position in the
    survey by the camera's startup delay. At 50 km/h that is ~14 m per second.
    """

    def test_anchor_is_stored_in_a_ref(self):
        source = APP_TSX.read_text(encoding="utf-8")
        assert "recordingStartRef" in source, (
            "the recording time anchor must be preserved across start/stop, not "
            "recomputed from started_at"
        )

    def test_anchor_not_derived_from_started_at(self):
        source = APP_TSX.read_text(encoding="utf-8")
        assert not re.search(r"recording_start_epoch_ms:\s*Math\.round\(\s*startedAtRef", source), (
            "recording_start_epoch_ms must not be recomputed from startedAt"
        )


class TestCollectorOutputIsIngestible:
    """A bundle shaped exactly as the TypeScript writes it must load cleanly.

    Written by hand rather than generated, so it fails if the *Python* reader drifts
    away from what the collector actually emits.
    """

    def test_handwritten_collector_bundle_loads(self, tmp_path: Path):
        root = tmp_path / "survey_2026-08-18T10-42-11-123Z_a1b2c3"
        root.mkdir()

        (root / "route.json").write_text(
            json.dumps(
                {
                    "schema_version": BUNDLE_SCHEMA_VERSION,
                    "route_id": "survey_2026-08-18T10-42-11-123Z_a1b2c3",
                    "started_at": "2026-08-18T10:42:11.123Z",
                    "ended_at": "2026-08-18T11:04:51.441Z",
                    "recording_start_epoch_ms": 1787049731123,
                    "camera_facing": "back",
                    "requested_video_quality": "1080p",
                    "app_version": "0.1.0",
                }
            ),
            encoding="utf-8",
        )
        (root / "manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": BUNDLE_SCHEMA_VERSION,
                    "files": ["route.json", "locations.jsonl", "device.json", "video.mp4"],
                }
            ),
            encoding="utf-8",
        )
        (root / "device.json").write_text(
            json.dumps(
                {
                    "os": "ios",
                    "os_version": "19.0",
                    "orientation": "landscape",
                    "location_accuracy_authorization": "full",
                }
            ),
            encoding="utf-8",
        )
        (root / "locations.jsonl").write_text(
            "\n".join(
                json.dumps(
                    {
                        "t": 1787049731123 + i * 1000,
                        "lat": 40.18231,
                        "lon": 44.51491 + i * 0.000118,
                        "accuracy_m": 5.8,
                        "speed_mps": 8.3,
                        "heading_deg": 92.1,
                    }
                )
                for i in range(10)
            )
            + "\n",
            encoding="utf-8",
        )

        bundle = load_bundle(root)

        assert bundle.survey_id == "survey_2026-08-18T10-42-11-123Z_a1b2c3"
        assert bundle.recording_start_epoch_ms == 1787049731123
        assert len(bundle.track) == 10
        assert bundle.device["location_accuracy_authorization"] == "full"
        assert not bundle.errors

        # The "Z" suffix that JavaScript's toISOString() emits must parse.
        assert bundle.started_at.year == 2026
        assert bundle.ended_at is not None

        located = bundle.track.locate(1787049731123 + 4500)
        assert located is not None
        assert located.is_trustworthy

    def test_generated_survey_id_shape_is_accepted(self, tmp_path: Path):
        """The collector's timestamped id contains dots and dashes; the processor's
        safe-name rule must permit exactly that shape."""
        import datetime as dt

        from roadeye.geolocation.timesync import LocationSample
        from roadeye.ingest.bundle import write_bundle_skeleton

        survey_id = "survey_2026-08-18T10-42-11-123Z_a1b2c3"
        start = dt.datetime(2026, 8, 18, 10, 42, 11, tzinfo=dt.UTC)
        path = write_bundle_skeleton(
            tmp_path / survey_id,
            survey_id=survey_id,
            started_at=start,
            recording_start_epoch_ms=int(start.timestamp() * 1000),
            samples=[
                LocationSample(
                    t_epoch_ms=int(start.timestamp() * 1000) + i * 1000,
                    lat=40.18231,
                    lon=44.51491 + i * 0.000118,
                    accuracy_m=5.0,
                )
                for i in range(5)
            ],
        )
        assert load_bundle(path).survey_id == survey_id
