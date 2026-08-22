#!/usr/bin/env python3
"""Generate a synthetic survey bundle so the pipeline can be exercised end to end.

Purpose: let someone run the whole chain — ingest, sampling, detection, tracking,
geolocation, clustering, storage, export — before any phone, model or footage exists.

    python3 scripts/make_demo_survey.py surveys/demo
    roadeye process surveys/demo --db demo.db
    roadeye export --db demo.db --geojson demo.geojson

**What this proves and does not prove.** It proves the plumbing works: that timestamps
line up, that positions interpolate, that repeated views of one defect collapse into one
map marker, that storage round-trips, and that exports are well-formed. It proves
nothing whatsoever about detecting road damage, because the detector is fake and there
are no pixels. Do not present its output as a road survey.

The route follows a plausible Yerevan drive so the exported GeoJSON lands on real
streets when opened in a map viewer — which makes an eyeball check of the geometry
actually meaningful. A route through (0, 0) would hide latitude/longitude swaps.
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import sys
from pathlib import Path

# Allow running from a source checkout without installing first.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from roadeye.geolocation.geodesy import LatLon, destination_point  # noqa: E402
from roadeye.geolocation.timesync import LocationSample  # noqa: E402
from roadeye.ingest.bundle import write_bundle_skeleton  # noqa: E402

#: Roughly Kentron, central Yerevan.
START = LatLon(40.18231, 44.51491)

#: A rectangular circuit: east, north, west, south. Each leg is a bearing and a length
#: in metres. Turns matter — they exercise circular heading interpolation, which a
#: straight line would not.
LEGS: list[tuple[float, float]] = [
    (90.0, 800.0),
    (0.0, 400.0),
    (270.0, 800.0),
    (180.0, 400.0),
]

#: Metres per second. ~36 km/h, a realistic urban survey speed.
CRUISE_MPS = 10.0

#: A red light partway through, to exercise the stationary guard in distance sampling.
STOP_AT_LEG = 1
STOP_DURATION_S = 25


def build_track(
    start_epoch_ms: int,
    *,
    gps_hz: float = 1.0,
    accuracy_m: float = 5.0,
    include_stop: bool = True,
) -> list[LocationSample]:
    """Simulate a drive around the circuit, one fix per GPS tick."""
    samples: list[LocationSample] = []
    position = START
    t_ms = start_epoch_ms
    step_ms = int(1000 / gps_hz)

    for leg_index, (bearing, length_m) in enumerate(LEGS):
        travelled = 0.0
        while travelled < length_m:
            hop = min(CRUISE_MPS / gps_hz, length_m - travelled)
            position = destination_point(position, bearing, hop)
            travelled += hop
            t_ms += step_ms
            samples.append(
                LocationSample(
                    t_epoch_ms=t_ms,
                    lat=position.lat,
                    lon=position.lon,
                    accuracy_m=accuracy_m,
                    speed_mps=CRUISE_MPS,
                    heading_deg=bearing,
                )
            )

        if include_stop and leg_index == STOP_AT_LEG:
            # Stationary at a light: position unchanged, speed zero. Distance-based
            # sampling must emit no frames here. Small position jitter is included
            # because a real receiver never reports a perfectly constant fix.
            for tick in range(int(STOP_DURATION_S * gps_hz)):
                t_ms += step_ms
                jitter = 0.0000015 * math.sin(tick)
                samples.append(
                    LocationSample(
                        t_epoch_ms=t_ms,
                        lat=position.lat + jitter,
                        lon=position.lon + jitter,
                        accuracy_m=accuracy_m,
                        speed_mps=0.0,
                        heading_deg=LEGS[leg_index][0],
                    )
                )

    return samples


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("output", nargs="?", default="surveys/demo", help="bundle directory")
    parser.add_argument("--survey-id", default=None)
    parser.add_argument("--gps-hz", type=float, default=1.0)
    parser.add_argument("--accuracy-m", type=float, default=5.0, help="reported fix accuracy")
    parser.add_argument("--no-stop", action="store_true", help="omit the red-light pause")
    args = parser.parse_args()

    started_at = dt.datetime(2026, 8, 18, 10, 42, 11, tzinfo=dt.UTC)
    start_epoch_ms = int(started_at.timestamp() * 1000)
    survey_id = args.survey_id or "demo_synthetic_001"

    samples = build_track(
        start_epoch_ms,
        gps_hz=args.gps_hz,
        accuracy_m=args.accuracy_m,
        include_stop=not args.no_stop,
    )
    duration_s = (samples[-1].t_epoch_ms - start_epoch_ms) / 1000.0

    path = write_bundle_skeleton(
        args.output,
        survey_id=survey_id,
        started_at=started_at,
        recording_start_epoch_ms=start_epoch_ms,
        samples=samples,
        ended_at=started_at + dt.timedelta(seconds=duration_s),
        device={"model": "synthetic", "os": "none", "orientation": "landscape"},
    )

    total_m = sum(length for _, length in LEGS)
    print(f"Wrote synthetic survey bundle: {path}")
    print(f"  survey_id     {survey_id}")
    print(f"  GPS fixes     {len(samples)} at {args.gps_hz} Hz")
    print(f"  route length  ~{total_m:.0f} m ({total_m / 1000:.2f} km)")
    print(f"  duration      ~{duration_s:.0f} s")
    print("  video         none (the pipeline uses a synthetic frame source)")
    print()
    print("This bundle contains NO imagery. Processing it exercises the pipeline;")
    print("it says nothing about detecting road damage.")
    print()
    print("Next:")
    print(f"  roadeye validate {path}")
    print(f"  roadeye process {path} --db demo.db")
    print("  roadeye export --db demo.db --geojson demo.geojson --csv demo.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
