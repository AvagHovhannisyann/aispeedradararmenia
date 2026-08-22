#!/usr/bin/env python3
"""Generate a synthetic road network covering the demo survey's circuit.

Why synthetic rather than real OpenStreetMap data: OSM is ODbL, which carries
share-alike obligations on data, and this repository is proprietary. A cached extract
committed here is exactly the ambiguity ``docs/LICENSE_AUDIT.md`` (L-3) is trying not to
create. So the demo and the test suite invent their own streets, and real geometry is
fetched at run time into a git-ignored directory.

The street names are invented too. They are not the real streets of Kentron, and the
banner says so, because a demo that quietly looks like real Yerevan data is the sort of
thing that ends up in a slide deck.

    python3 scripts/make_demo_roads.py --output demo_output/roads.json
    .venv/bin/roadeye match-roads --db demo_output/demo.db --roads demo_output/roads.json
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from roadeye.geolocation.geodesy import LatLon, destination_point  # noqa: E402
from roadeye.map_matching.network import (  # noqa: E402
    NetworkProvenance,
    RoadNetwork,
    RoadSegment,
)

#: Same start point and circuit as ``scripts/make_demo_survey.py``. The two must agree
#: or the demo matches nothing.
START = LatLon(40.18231, 44.51491)

#: ``(name, bearing, length_m, oneway)`` — the four sides of the demo circuit.
STREETS: list[tuple[str, float, float, bool]] = [
    ("Demo Street South", 90.0, 800.0, False),
    ("Demo Street East", 0.0, 400.0, True),
    ("Demo Street North", 270.0, 800.0, False),
    ("Demo Street West", 180.0, 400.0, True),
]

#: Vertices per street. Real OSM ways are broken at every shape point, and matching sees
#: one segment per pair, so a single 800 m segment would not exercise the same code.
VERTICES = 9


def build_network(*, offset_m: float = 0.0) -> RoadNetwork:
    """Trace the circuit as named ways.

    ``offset_m`` shifts each centreline sideways, which is what makes the demo honest:
    the GPS track is where the *vehicle* drove, and the road centreline is a couple of
    metres away from it. A network laid exactly on the track would make matching look
    perfect for the wrong reason.
    """
    segments: list[RoadSegment] = []
    corner = START
    for way_index, (name, bearing, length, oneway) in enumerate(STREETS, start=1):
        start = destination_point(corner, (bearing + 90.0) % 360.0, offset_m)
        step = length / (VERTICES - 1)
        points = [destination_point(start, bearing, step * i) for i in range(VERTICES)]
        for i, (a, b) in enumerate(zip(points, points[1:], strict=False)):
            segments.append(
                RoadSegment(
                    segment_id=f"way/{900_000 + way_index}#{i}",
                    way_id=f"way/{900_000 + way_index}",
                    start=a,
                    end=b,
                    name=name,
                    highway="residential",
                    oneway=oneway,
                )
            )
        corner = destination_point(corner, bearing, length)

    return RoadNetwork(
        segments=segments,
        provenance=NetworkProvenance(
            source="synthetic",
            license="none — invented geometry, not OpenStreetMap",
            attribution="Synthetic demo geometry. These are NOT real streets.",
            retrieved_at=datetime.now(UTC),
            query="scripts/make_demo_roads.py",
        ),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="demo_output/roads.json")
    parser.add_argument(
        "--offset-m",
        type=float,
        default=3.0,
        help="sideways offset of the centreline from the driven track (default: %(default)s)",
    )
    args = parser.parse_args(argv)

    network = build_network(offset_m=args.offset_m)
    out = network.save(args.output)
    print(f"wrote {out} — {len(network)} segments, {len(network.named_streets())} streets")
    print("\nThese street names are invented. This is not real Yerevan road geometry.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
