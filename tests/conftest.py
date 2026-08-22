"""Shared fixtures.

Everything here is synthetic and deterministic. No network, no GPU, no ffmpeg, no
model weights — the whole suite must run on a bare laptop in seconds, because a test
suite that needs a GPU is a test suite that stops being run.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from roadeye.domain.enums import DamageClass
from roadeye.geolocation.timesync import LocationSample
from roadeye.ingest.bundle import load_bundle, write_bundle_skeleton
from roadeye.vision.base import RawDetection

#: A real point in Yerevan (roughly Kentron), so fixtures look like the target city
#: rather than the Gulf of Guinea at (0, 0) — which would hide sign and swap bugs.
YEREVAN_LAT = 40.18231
YEREVAN_LON = 44.51491

#: ~0.000118 degrees of longitude at this latitude is very close to 10 m.
LON_PER_10M = 0.000118


@pytest.fixture
def survey_start() -> dt.datetime:
    return dt.datetime(2026, 8, 18, 10, 42, 11, tzinfo=dt.timezone.utc)


@pytest.fixture
def straight_drive_samples(survey_start: dt.datetime) -> list[LocationSample]:
    """60 seconds heading due east at a steady 10 m/s, 1 Hz, good accuracy."""
    t0 = int(survey_start.timestamp() * 1000)
    return [
        LocationSample(
            t_epoch_ms=t0 + i * 1000,
            lat=YEREVAN_LAT,
            lon=YEREVAN_LON + i * LON_PER_10M,
            accuracy_m=5.0,
            speed_mps=10.0,
            heading_deg=90.0,
        )
        for i in range(61)
    ]


@pytest.fixture
def bundle_path(
    tmp_path: Path, survey_start: dt.datetime, straight_drive_samples: list[LocationSample]
) -> Path:
    """A minimal valid survey bundle on disk (no video)."""
    return write_bundle_skeleton(
        tmp_path / "survey_fixture",
        survey_id="fixture001",
        started_at=survey_start,
        recording_start_epoch_ms=int(survey_start.timestamp() * 1000),
        samples=straight_drive_samples,
        ended_at=survey_start + dt.timedelta(seconds=60),
        device={"model": "synthetic", "os": "test"},
    )


@pytest.fixture
def loaded_bundle(bundle_path: Path):
    return load_bundle(bundle_path)


def pothole(x1: float, y1: float, size: float = 100.0, conf: float = 0.9) -> RawDetection:
    """A square pothole detection, for building scripted detector fixtures."""
    return RawDetection(
        damage_class=DamageClass.POTHOLE,
        confidence=conf,
        x1=x1,
        y1=y1,
        x2=x1 + size,
        y2=y1 + size,
    )
