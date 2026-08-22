"""RoadEye command line: ``python -m roadeye``.

Deliberately small. The CLI is a thin shell over the library so that everything it can
do is also callable from tests, notebooks and the API without shelling out.

Commands
--------
``validate``  inspect a survey bundle and report problems without processing it
``process``   run the pipeline over a bundle and store the result
``export``    write defects to CSV / GeoJSON
``stats``     summarise what is in a database
``env``       report the host environment (useful in a pilot record)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from roadeye import __version__
from roadeye.domain.enums import DamageClass, DefectStatus
from roadeye.ingest.bundle import BundleError, load_bundle
from roadeye.pipeline import PipelineConfig, process_survey
from roadeye.reporting.export import summarize, to_csv, to_geojson
from roadeye.storage.db import Database
from roadeye.vision.fake import FakeDetector

#: Attribution required whenever an export leans on OpenStreetMap-derived geometry.
#: See docs/LICENSE_AUDIT.md.
OSM_ATTRIBUTION = "Road network data © OpenStreetMap contributors, ODbL."


def _cmd_validate(args: argparse.Namespace) -> int:
    try:
        bundle = load_bundle(args.bundle)
    except BundleError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 2

    print(f"survey_id            {bundle.survey_id}")
    print(f"schema_version       {bundle.schema_version}")
    print(f"started_at           {bundle.started_at.isoformat()}")
    print(f"recording_start_ms   {bundle.recording_start_epoch_ms}")
    print(f"gps_fixes_kept       {len(bundle.track)} of {bundle.track.stats.total_input}")
    print(f"track_distance_m     {bundle.track.total_distance_m():.1f}")
    print(f"video                {'present' if bundle.has_video else 'MISSING'}")

    for issue in bundle.issues:
        print(f"  {issue}")

    if bundle.errors:
        print(f"\n{len(bundle.errors)} error(s) — this survey cannot be fully processed.")
        return 1
    print("\nBundle is usable.")
    return 0


def _cmd_process(args: argparse.Namespace) -> int:
    config = PipelineConfig()
    if args.min_confidence is not None:
        config.min_detection_confidence = args.min_confidence

    # The default detector is FAKE. Real detectors arrive at M3; until then this
    # command exercises the pipeline, and saying so loudly prevents anyone mistaking
    # its output for a road survey.
    detector = FakeDetector(detections_per_frame=args.fake_detections)
    print(
        "WARNING: using the fake detector. Output is synthetic and describes nothing "
        "about any real road.",
        file=sys.stderr,
    )

    db = Database(args.db) if args.db else None
    try:
        result = process_survey(args.bundle, detector, db=db, config=config)
    except BundleError as exc:
        print(f"INVALID BUNDLE: {exc}", file=sys.stderr)
        return 2

    print(result.run.summary())
    for warning in result.run.warnings:
        print(f"  warning: {warning}")
    for error in result.run.errors:
        print(f"  error:   {error}")

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {
                    "run": result.run.model_dump(mode="json"),
                    "summary": summarize(result.defects),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"wrote {args.json}")

    if db is not None:
        db.close()
    return 1 if result.run.errors else 0


def _cmd_export(args: argparse.Namespace) -> int:
    with Database(args.db) as db:
        defects = db.list_defects(
            damage_class=DamageClass(args.damage_class) if args.damage_class else None,
            status=DefectStatus(args.status) if args.status else None,
            min_confidence=args.min_confidence,
        )
    if not defects:
        print("no defects matched the filters", file=sys.stderr)

    if args.csv:
        to_csv(defects, args.csv)
        print(f"wrote {args.csv} ({len(defects)} rows)")
    if args.geojson:
        to_geojson(defects, args.geojson, attribution=OSM_ATTRIBUTION)
        print(f"wrote {args.geojson} ({len(defects)} features)")
    if not args.csv and not args.geojson:
        print(json.dumps(summarize(defects), indent=2))
    return 0


def _cmd_stats(args: argparse.Namespace) -> int:
    with Database(args.db) as db:
        defects = db.list_defects()
        counts = {
            table: db.count(table)
            for table in ("surveys", "frames", "detections", "defects", "reviews")
        }
    print(json.dumps({"tables": counts, "defects": summarize(defects)}, indent=2))
    return 0


def _cmd_env(args: argparse.Namespace) -> int:
    import platform
    import shutil

    info = {
        "roadeye_version": __version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "ffmpeg": shutil.which("ffmpeg") or "NOT FOUND (video decoding unavailable)",
    }
    for module in ("numpy", "torch", "torchvision", "av", "pydantic"):
        try:
            mod = __import__(module)
            info[module] = getattr(mod, "__version__", "unknown")
        except ImportError:
            info[module] = "not installed"
    print(json.dumps(info, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="roadeye", description="RoadEye — smartphone road inspection pipeline."
    )
    parser.add_argument("--version", action="version", version=f"roadeye {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("validate", help="check a survey bundle without processing it")
    p.add_argument("bundle", help="path to a survey bundle directory")
    p.set_defaults(func=_cmd_validate)

    p = sub.add_parser("process", help="run the pipeline over a survey bundle")
    p.add_argument("bundle")
    p.add_argument("--db", help="SQLite database to write results into")
    p.add_argument("--json", help="write the run summary to this JSON file")
    p.add_argument("--min-confidence", type=float, default=None)
    p.add_argument(
        "--fake-detections",
        type=int,
        default=1,
        help="synthetic detections per frame (fake detector only)",
    )
    p.set_defaults(func=_cmd_process)

    p = sub.add_parser("export", help="export defects to CSV / GeoJSON")
    p.add_argument("--db", required=True)
    p.add_argument("--csv")
    p.add_argument("--geojson")
    p.add_argument("--damage-class", choices=[c.value for c in DamageClass])
    p.add_argument("--status", choices=[s.value for s in DefectStatus])
    p.add_argument("--min-confidence", type=float)
    p.set_defaults(func=_cmd_export)

    p = sub.add_parser("stats", help="summarise a database")
    p.add_argument("--db", required=True)
    p.set_defaults(func=_cmd_stats)

    p = sub.add_parser("env", help="report the host environment")
    p.set_defaults(func=_cmd_env)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
