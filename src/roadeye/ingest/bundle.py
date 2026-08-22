"""Survey bundle: the contract between the collector and the processor.

A *survey bundle* is one immutable drive on disk:

    survey_<uuid>/
      manifest.json     <- schema_version, file inventory, checksums
      route.json        <- route id, timings, camera config
      locations.jsonl   <- one GPS fix per line
      motion.jsonl      <- optional accelerometer/gyro
      device.json       <- optional device/OS/app metadata
      calibration.json  <- optional mount geometry
      video.mp4         <- the raw evidence

The bundle format, not the app, is the real interface. A future native iOS or Android
collector is "compatible" precisely when it emits a bundle that validates here — which
is what lets us start on Expo without betting the product on it (ADR-002).

Two rules this module exists to enforce:

* **Every bundle declares its ``schema_version``.** The on-disk format must never
  change silently; a reader that meets an unknown version must say so rather than
  guessing and producing subtly wrong output.
* **A partly-broken bundle is still worth processing.** Phones run out of storage, get
  unplugged and get force-quit mid-drive. Losing a 30-minute survey because
  ``motion.jsonl`` is truncated would be a self-inflicted wound. Problems are collected
  and reported; only genuinely unusable bundles raise.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from roadeye.geolocation.timesync import LocationSample, LocationTrack

#: Bundle format version. Increment on any breaking layout/field change and add a
#: migration path here. Readers must refuse versions they do not understand.
BUNDLE_SCHEMA_VERSION = 1

#: Files a bundle may contain. Anything else is reported but ignored.
KNOWN_FILES = {
    "manifest.json",
    "route.json",
    "locations.jsonl",
    "motion.jsonl",
    "device.json",
    "calibration.json",
    "video.mp4",
}

_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


class BundleError(Exception):
    """The bundle cannot be used at all."""


@dataclass
class BundleIssue:
    """A recoverable problem. Surfaced in the processing run, not raised."""

    severity: str  # "warning" | "error"
    message: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"[{self.severity}] {self.message}"


@dataclass
class SurveyBundle:
    """A validated, loaded survey bundle."""

    path: Path
    schema_version: int
    survey_id: str
    started_at: datetime
    ended_at: datetime | None
    recording_start_epoch_ms: int
    route: dict[str, Any]
    device: dict[str, Any]
    calibration: dict[str, Any]
    track: LocationTrack
    video_path: Path | None
    issues: list[BundleIssue] = field(default_factory=list)

    @property
    def has_video(self) -> bool:
        return self.video_path is not None and self.video_path.exists()

    @property
    def errors(self) -> list[str]:
        return [i.message for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[str]:
        return [i.message for i in self.issues if i.severity == "warning"]

    def duration_s(self) -> float | None:
        if self.ended_at is None:
            return None
        return (self.ended_at - self.started_at).total_seconds()


def _read_json(path: Path, issues: list[BundleIssue], *, required: bool) -> dict[str, Any]:
    """Read a JSON file, degrading to ``{}`` for optional files."""
    if not path.exists():
        if required:
            raise BundleError(f"required file missing: {path.name}")
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        if required:
            raise BundleError(f"{path.name} is not valid JSON: {exc}") from exc
        issues.append(BundleIssue("warning", f"{path.name} is not valid JSON, ignored: {exc}"))
        return {}
    if not isinstance(data, dict):
        if required:
            raise BundleError(f"{path.name} must contain a JSON object")
        issues.append(BundleIssue("warning", f"{path.name} is not a JSON object, ignored"))
        return {}
    return data


def iter_jsonl(path: Path, issues: list[BundleIssue]) -> Iterator[dict[str, Any]]:
    """Yield objects from a JSON Lines file, skipping malformed lines.

    Line-oriented on purpose: a JSON *array* truncated by a crashing app is entirely
    unparseable, whereas JSONL truncated at any point loses only the final line. For a
    field instrument recording to a phone that may be force-quit, that difference is
    the difference between losing a drive and losing a second of it.
    """
    if not path.exists():
        return
    bad = 0
    try:
        with path.open("r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    bad += 1
                    # The final line of a force-quit recording is routinely a partial
                    # write. Report the count, not one warning per line.
                    continue
                if isinstance(obj, dict):
                    yield obj
                else:
                    bad += 1
    except UnicodeDecodeError as exc:
        issues.append(BundleIssue("error", f"{path.name} is not valid UTF-8: {exc}"))
        return
    if bad:
        issues.append(
            BundleIssue("warning", f"{path.name}: skipped {bad} malformed line(s)")
        )


def _parse_location(obj: dict[str, Any]) -> LocationSample | None:
    """Convert one JSONL record to a sample, or ``None`` if unusable."""
    try:
        t = obj["t"]
        lat = float(obj["lat"])
        lon = float(obj["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    if not isinstance(t, (int, float)):
        return None

    def _opt(key: str) -> float | None:
        v = obj.get(key)
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    return LocationSample(
        t_epoch_ms=int(t),
        lat=lat,
        lon=lon,
        accuracy_m=_opt("accuracy_m"),
        speed_mps=_opt("speed_mps"),
        heading_deg=_opt("heading_deg"),
        altitude_m=_opt("altitude_m"),
    )


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        # Accept the trailing "Z" that mobile runtimes emit.
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def load_bundle(
    path: str | Path,
    *,
    max_accuracy_m: float | None = None,
    strict_version: bool = True,
) -> SurveyBundle:
    """Load and validate a survey bundle from disk.

    Raises :class:`BundleError` only when the bundle is genuinely unusable — missing
    ``route.json``, an unreadable location log, or an unknown schema version. Everything
    else is recorded in :attr:`SurveyBundle.issues`.
    """
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise BundleError(f"not a directory: {root}")

    issues: list[BundleIssue] = []

    manifest = _read_json(root / "manifest.json", issues, required=False)
    route = _read_json(root / "route.json", issues, required=True)

    version = manifest.get("schema_version", route.get("schema_version", BUNDLE_SCHEMA_VERSION))
    if not isinstance(version, int):
        raise BundleError(f"schema_version must be an integer, got {version!r}")
    if version > BUNDLE_SCHEMA_VERSION:
        message = (
            f"bundle schema_version {version} is newer than this build supports "
            f"({BUNDLE_SCHEMA_VERSION}); upgrade RoadEye rather than risking a "
            "misinterpreted survey"
        )
        if strict_version:
            raise BundleError(message)
        issues.append(BundleIssue("warning", message))

    survey_id = route.get("route_id") or route.get("survey_id")
    if not survey_id or not isinstance(survey_id, str):
        raise BundleError("route.json must contain a string 'route_id'")
    if not _SAFE_NAME.match(survey_id):
        # Survey ids reach filesystem paths and export filenames; refusing separators
        # and traversal sequences here is cheaper than sanitising at every use site.
        raise BundleError(
            f"route_id {survey_id!r} contains unsafe characters; expected [A-Za-z0-9._-]+"
        )

    started_at = _parse_dt(route.get("started_at"))
    if started_at is None:
        raise BundleError("route.json must contain an ISO-8601 'started_at'")
    ended_at = _parse_dt(route.get("ended_at"))

    # The time anchor. Falling back to started_at is acceptable but degrades accuracy,
    # so it is a loud warning rather than a silent default: a wrong anchor displaces
    # every defect in the survey by however long the camera took to start.
    recording_start = route.get("recording_start_epoch_ms")
    if not isinstance(recording_start, (int, float)):
        recording_start = int(started_at.timestamp() * 1000)
        issues.append(
            BundleIssue(
                "warning",
                "route.json has no 'recording_start_epoch_ms'; falling back to "
                "'started_at'. Any delay between survey start and the first video "
                "frame will offset every position in this survey.",
            )
        )
    recording_start = int(recording_start)

    device = _read_json(root / "device.json", issues, required=False)
    calibration = _read_json(root / "calibration.json", issues, required=False)

    # ---- locations ----
    loc_path = root / "locations.jsonl"
    if not loc_path.exists():
        raise BundleError("locations.jsonl is missing; positions cannot be estimated")

    raw_samples: list[LocationSample] = []
    unparsable = 0
    for obj in iter_jsonl(loc_path, issues):
        sample = _parse_location(obj)
        if sample is None:
            unparsable += 1
        else:
            raw_samples.append(sample)
    if unparsable:
        issues.append(
            BundleIssue("warning", f"locations.jsonl: {unparsable} record(s) lacked usable lat/lon/t")
        )

    track = LocationTrack.from_samples(
        raw_samples,
        max_accuracy_m=max_accuracy_m if max_accuracy_m is not None else 25.0,
    )
    for note in track.stats.issues:
        issues.append(BundleIssue("warning", f"GPS: {note}"))
    if len(track) == 0:
        issues.append(
            BundleIssue("error", "no usable GPS fixes; defects cannot be geolocated")
        )

    # ---- video ----
    video_path: Path | None = root / "video.mp4"
    if not video_path.exists():
        issues.append(
            BundleIssue(
                "warning",
                "video.mp4 is missing; the survey can be inspected but not analysed",
            )
        )
        video_path = None

    for extra in sorted(p.name for p in root.iterdir() if p.name not in KNOWN_FILES):
        issues.append(BundleIssue("warning", f"unexpected file in bundle, ignored: {extra}"))

    return SurveyBundle(
        path=root,
        schema_version=version,
        survey_id=survey_id,
        started_at=started_at,
        ended_at=ended_at,
        recording_start_epoch_ms=recording_start,
        route=route,
        device=device,
        calibration=calibration,
        track=track,
        video_path=video_path,
        issues=issues,
    )


def write_bundle_skeleton(
    root: str | Path,
    *,
    survey_id: str,
    started_at: datetime,
    recording_start_epoch_ms: int,
    samples: list[LocationSample],
    ended_at: datetime | None = None,
    device: dict[str, Any] | None = None,
) -> Path:
    """Write a minimal valid bundle (no video). Used by tests and fixtures.

    Deliberately part of the library rather than the test suite: it is the executable
    specification of the format that a native collector must reproduce.
    """
    path = Path(root).expanduser()
    path.mkdir(parents=True, exist_ok=True)

    route = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "route_id": survey_id,
        "started_at": started_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "recording_start_epoch_ms": recording_start_epoch_ms,
        "camera_facing": "back",
        "requested_video_quality": "1080p",
    }
    if ended_at is not None:
        route["ended_at"] = ended_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    (path / "route.json").write_text(json.dumps(route, indent=2), encoding="utf-8")
    (path / "manifest.json").write_text(
        json.dumps({"schema_version": BUNDLE_SCHEMA_VERSION, "files": ["route.json", "locations.jsonl"]}, indent=2),
        encoding="utf-8",
    )
    if device:
        (path / "device.json").write_text(json.dumps(device, indent=2), encoding="utf-8")

    with (path / "locations.jsonl").open("w", encoding="utf-8") as fh:
        for s in samples:
            rec: dict[str, Any] = {"t": s.t_epoch_ms, "lat": s.lat, "lon": s.lon}
            if s.accuracy_m is not None:
                rec["accuracy_m"] = s.accuracy_m
            if s.speed_mps is not None:
                rec["speed_mps"] = s.speed_mps
            if s.heading_deg is not None:
                rec["heading_deg"] = s.heading_deg
            fh.write(json.dumps(rec) + "\n")

    return path
