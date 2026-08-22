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


def _load_detector(
    model_dir: str | None,
    fake_detections: int,
    score_threshold: float = 0.3,
):
    """Return a detector, and say plainly which kind it is.

    Without ``--model`` this is the fake detector, whose output describes nothing about
    any real road. That warning is not boilerplate: synthetic markers on a real map of
    Yerevan look exactly like a working product.
    """
    if model_dir:
        from roadeye.vision.torchvision_detector import TorchvisionDetector

        # The threshold must reach the detector, not just filter its output:
        # torchvision drops boxes below its own internal threshold first, so a
        # low --min-confidence applied afterwards would silently do nothing.
        detector = TorchvisionDetector.from_registry(model_dir, score_threshold=score_threshold)
        metadata_path = Path(model_dir) / "metadata.json"
        if metadata_path.exists():
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if not metadata.get("distribution_allowed", False):
                print(
                    f"NOTE: model '{detector.model_id}' is marked non-distributable "
                    "(disputed training-data licence). Internal evaluation only — see "
                    "docs/LICENSE_AUDIT.md.",
                    file=sys.stderr,
                )
            if metadata.get("warning"):
                print(f"NOTE: {metadata['warning']}", file=sys.stderr)
        return detector

    print(
        "WARNING: no --model given, so the FAKE detector is being used. Its output is "
        "synthetic and describes nothing about any real road.",
        file=sys.stderr,
    )
    return FakeDetector(detections_per_frame=fake_detections)


def _cmd_detect(args: argparse.Namespace) -> int:
    """Run a detector over images and report — and optionally draw — what it finds."""
    from roadeye.vision.annotate import annotate_image, iter_images, load_image_array
    from roadeye.vision.base import FrameImage

    images = iter_images(args.images)
    if not images:
        print(f"no images found at {args.images}", file=sys.stderr)
        return 1

    detector = _load_detector(args.model, 1, score_threshold=args.min_confidence)
    if args.limit:
        images = images[: args.limit]

    out_dir = Path(args.output) if args.output else None
    totals: dict[str, int] = {}
    with_detections = 0
    records = []

    for path in images:
        pixels = load_image_array(path)
        frame = FrameImage(
            frame_id=path.stem, width=pixels.shape[1], height=pixels.shape[0], pixels=pixels
        )
        detections = [d for d in detector.predict(frame) if d.confidence >= args.min_confidence]

        if detections:
            with_detections += 1
        for det in detections:
            totals[det.damage_class.value] = totals.get(det.damage_class.value, 0) + 1

        records.append(
            {
                "image": str(path),
                "detections": [
                    {
                        "class": d.damage_class.value,
                        "confidence": round(d.confidence, 4),
                        "box": [round(d.x1, 1), round(d.y1, 1), round(d.x2, 1), round(d.y2, 1)],
                    }
                    for d in detections
                ],
            }
        )

        if out_dir is not None and (detections or not args.only_detections):
            annotate_image(path, detections, out_dir / f"{path.stem}.jpg")

        if args.verbose:
            # An undertrained model can emit dozens of boxes per image; printing them
            # all makes the output unreadable and hides the summary underneath.
            shown = sorted(detections, key=lambda d: -d.confidence)[:4]
            summary = ", ".join(f"{d.damage_class.value} {d.confidence:.2f}" for d in shown)
            if len(detections) > len(shown):
                summary += f"  (+{len(detections) - len(shown)} more)"
            print(f"{path.name:<28} {summary or '-'}")

    print(f"\nmodel            {detector.model_id}")
    print(f"images           {len(images)}")
    print(f"with detections  {with_detections} ({with_detections / len(images) * 100:.0f}%)")
    print(f"detections       {sum(totals.values())}")
    for cls, count in sorted(totals.items(), key=lambda kv: -kv[1]):
        print(f"  {cls:<22} {count}")
    if out_dir is not None:
        print(f"\nannotated images written to {out_dir}/")
    if args.json:
        Path(args.json).write_text(json.dumps(records, indent=2), encoding="utf-8")
        print(f"wrote {args.json}")
    return 0


def _cmd_process(args: argparse.Namespace) -> int:
    config = PipelineConfig()
    if args.min_confidence is not None:
        config.min_detection_confidence = args.min_confidence

    detector = _load_detector(
        args.model,
        args.fake_detections,
        score_threshold=(
            args.min_confidence
            if args.min_confidence is not None
            else config.min_detection_confidence
        ),
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

    p = sub.add_parser("detect", help="run a detector over images and draw the results")
    p.add_argument("images", help="an image file or a directory of images")
    p.add_argument("--model", help="model directory containing metadata.json and weights.pt")
    p.add_argument("--output", help="directory to write annotated copies into")
    p.add_argument("--json", help="write per-image detections to this JSON file")
    p.add_argument("--min-confidence", type=float, default=0.3)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--verbose", action="store_true", help="print every image")
    p.add_argument(
        "--only-detections",
        action="store_true",
        help="write annotated copies only for images with at least one detection",
    )
    p.set_defaults(func=_cmd_detect)

    p = sub.add_parser("process", help="run the pipeline over a survey bundle")
    p.add_argument("bundle")
    p.add_argument("--db", help="SQLite database to write results into")
    p.add_argument("--json", help="write the run summary to this JSON file")
    p.add_argument("--model", help="model directory; omit to use the FAKE detector")
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
