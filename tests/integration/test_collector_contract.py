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
#: The bundle contract lives in bundle.ts, which is deliberately free of expo imports
#: so it can be tested by `node --test` with no install. survey.ts is the filesystem
#: half and holds no field definitions.
BUNDLE_TS = COLLECTOR / "src" / "bundle.ts"
SURVEY_TS = COLLECTOR / "src" / "survey.ts"
APP_TSX = COLLECTOR / "App.tsx"

pytestmark = pytest.mark.skipif(
    not BUNDLE_TS.exists(), reason="collector app not present in this checkout"
)


class TestSchemaVersionAgreement:
    def test_typescript_and_python_agree(self):
        """A version mismatch means the processor silently misreads real surveys."""
        source = BUNDLE_TS.read_text(encoding="utf-8")
        match = re.search(r"BUNDLE_SCHEMA_VERSION\s*=\s*(\d+)", source)
        assert match, "BUNDLE_SCHEMA_VERSION not found in apps/collector/src/bundle.ts"
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
        assert field in BUNDLE_TS.read_text(encoding="utf-8"), (
            f"collector no longer writes route field '{field}'"
        )

    @pytest.mark.parametrize("field", ["accuracy_m", "speed_mps", "heading_deg"])
    def test_location_fields_present(self, field: str):
        assert field in BUNDLE_TS.read_text(encoding="utf-8"), (
            f"collector no longer writes location field '{field}'"
        )

    def test_location_uses_short_time_key(self):
        """Locations use 't', not 'timestamp' — matches _parse_location()."""
        assert re.search(r"\bt:\s*number", BUNDLE_TS.read_text(encoding="utf-8"))

    def test_filenames_match(self):
        """Checked across both modules: bundle.ts names the files a manifest lists,
        survey.ts names the paths written. Splitting the pure half out moved some of
        these; the contract is their union."""
        source = BUNDLE_TS.read_text(encoding="utf-8") + SURVEY_TS.read_text(encoding="utf-8")
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


class TestTheDriveCannotBeSilentlyLost:
    """Regressions for three bugs that would each have cost a whole survey.

    The collector has never run on a device, so these are checked by reading the source
    rather than by exercising it. That is weaker than a real test and much better than
    nothing: each one pins a specific mistake that was actually present.
    """

    def test_stop_waits_for_the_video_before_finishing(self):
        """``recordAsync()`` resolves only once the file is written. The first version
        called ``stopRecording()`` and then immediately wrote the manifest and showed
        "done" — so a user who closed the app there lost the entire drive, with a bundle
        on disk claiming a video that was still in flight."""
        source = APP_TSX.read_text(encoding="utf-8")
        assert "recordingRef" in source, "the recording promise must be kept"
        assert re.search(r"await\s+recordingRef\.current", source), (
            "stop() must await the recording promise before writing the manifest"
        )

    def test_the_manifest_reports_whether_a_video_exists(self):
        """Listing video.mp4 unconditionally makes the bundle lie about itself and sends
        the processor looking for a file a failed recording never wrote."""
        source = APP_TSX.read_text(encoding="utf-8")
        assert "hasVideo" in source
        assert not re.search(r"writeManifest\([^)]*'video\.mp4'", source, re.S), (
            "the manifest must not hard-code video.mp4"
        )

    def test_appends_are_serialised(self):
        """``appendLocations`` is read-modify-write, because expo-file-system has no
        append. Two overlapping calls both read the same contents and the second write
        discards the first's records — silently. Fixes flush on a timer and stop()
        flushes again immediately, so overlap is easy to hit."""
        source = SURVEY_TS.read_text(encoding="utf-8")
        assert "appendQueues" in source, (
            "appends must be chained per path so two flushes cannot interleave"
        )


class TestStorageIsCheckedInMinutesNotBytes:
    """A fixed 2 GB floor bought roughly 17 minutes of 1080p video, while M1's own
    acceptance criterion is a 30-minute survey. The phone would have filled partway
    through and truncated the recording — discovered only after the drive."""

    def test_the_floor_is_expressed_as_recordable_minutes(self):
        source = BUNDLE_TS.read_text(encoding="utf-8")
        assert "VIDEO_BYTES_PER_MINUTE" in source
        assert "MIN_SURVEY_MINUTES" in source

    def test_the_old_fixed_byte_floor_is_gone(self):
        source = APP_TSX.read_text(encoding="utf-8")
        assert "MIN_FREE_BYTES" not in source, (
            "free space must be judged in recordable minutes, not a fixed byte count"
        )


class TestPermissionsCanBeRetried:
    def test_the_grant_button_requests_location_too(self):
        """The first version's only button re-requested the camera, so anyone who
        declined location once was stuck on a screen whose button could not fix the
        thing it was complaining about."""
        source = APP_TSX.read_text(encoding="utf-8")
        assert "requestLocation" in source
        assert re.search(r"onPress=\{async \(\) => \{[^}]*requestLocation", source, re.S), (
            "the permissions button must be able to re-request location"
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
