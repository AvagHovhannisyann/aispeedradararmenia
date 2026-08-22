"""RoadEye command line: ``python -m roadeye``.

Deliberately small. The CLI is a thin shell over the library so that everything it can
do is also callable from tests, notebooks and the API without shelling out.

Commands
--------
``validate``  inspect a survey bundle and report problems without processing it
``process``   run the pipeline over a bundle and store the result
``detect``    run a detector over images and draw the boxes
``review``    launch the human-in-the-loop review UI
``dashboard`` launch the municipal dashboard — a map of every defect
``redact``    blur people and vehicles out of images
``retention`` delete artefacts past their retention period, logging each one
``roads``     fetch or import an OpenStreetMap road network
``match-roads``     assign stored defects to road segments
``export``    write defects to CSV / GeoJSON
``export-dataset``  turn reviewed defects into training data
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
from roadeye.map_matching.matcher import MatchingConfig
from roadeye.map_matching.osm import DEFAULT_OVERPASS_ENDPOINT
from roadeye.pipeline import PipelineConfig, process_survey
from roadeye.privacy.redaction import RedactionMethod
from roadeye.privacy.retention import RetentionPolicy
from roadeye.reporting.export import summarize, to_csv, to_geojson
from roadeye.storage.db import Database
from roadeye.vision.fake import FakeDetector


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


def _serve(args: argparse.Namespace, *, path: str, title: str, hints: list[str]) -> int:
    """Serve the local web app, whichever page the caller wants first.

    The review screen and the dashboard are two pages of one app, so this is shared
    rather than duplicated — and both are bound to localhost for the same reason.
    """
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"No database at {db_path}. Run `roadeye process` first.", file=sys.stderr)
        return 1

    try:
        import uvicorn
        from services.api.app import create_app
    except ImportError:
        # services/ is not an installed package, so fall back to a path import. This
        # keeps the web UI runnable straight from a source checkout.
        import sys as _sys

        _sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        try:
            import uvicorn
            from services.api.app import create_app
        except ImportError as exc:
            print(
                f"The web UI needs the 'api' extra: pip install -e '.[api]'  ({exc})",
                file=sys.stderr,
            )
            return 1

    app = create_app(db_path, args.evidence, getattr(args, "roads", None))
    print("=" * 62)
    print(f"  {title} on port {args.port}")
    print()
    print("  In a Codespace: click 'Open in Browser' when prompted, or use the")
    print(f"  PORTS tab and the globe icon on port {args.port}.")
    print(f"  Locally: http://127.0.0.1:{args.port}{path}")
    print()
    for hint in hints:
        print(f"  {hint}")
    print("=" * 62)
    # Bound to localhost deliberately: there is no authentication and the evidence
    # images may contain identifiable people (docs/SECURITY.md, docs/PRIVACY.md).
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def _cmd_review(args: argparse.Namespace) -> int:
    """Launch the local review UI."""
    return _serve(
        args,
        path="/",
        title="RoadEye review",
        hints=[
            "Keys:  A approve   R reject   1-4 change class   Q/W/E severity",
            "       S skip      N note     arrows navigate",
            "",
            f"Map view: http://127.0.0.1:{args.port}/dashboard",
        ],
    )


def _cmd_dashboard(args: argparse.Namespace) -> int:
    """Launch the municipal dashboard."""
    return _serve(
        args,
        path="/dashboard",
        title="RoadEye dashboard",
        hints=[
            "A map of every defect, filterable, with evidence and review controls.",
            "The background map needs the internet; the defect data does not.",
            "",
            f"Review queue: http://127.0.0.1:{args.port}/",
        ],
    )
    return 0


def _cmd_process(args: argparse.Namespace) -> int:
    config = PipelineConfig()
    if args.db and not args.no_evidence:
        from roadeye.reporting.evidence import evidence_dir_for

        config.evidence_dir = evidence_dir_for(args.db)
    if args.min_confidence is not None:
        config.min_detection_confidence = args.min_confidence

    if config.evidence_dir is not None:
        from roadeye.privacy.base import RedactionError

        try:
            config.anonymizer = _build_anonymizer(args)
        except RedactionError as exc:
            # Fail the run rather than quietly writing identifiable faces because the
            # redactor would not load. Opting out has to be explicit.
            print(f"{exc}\n\nTo proceed without redaction, pass --no-redact.", file=sys.stderr)
            return 1
        if config.anonymizer is None:
            print(
                "NOTE: --no-redact. Evidence images will contain faces and licence "
                "plates. They must not leave this machine — see docs/PRIVACY.md.",
                file=sys.stderr,
            )

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


def _cmd_export_dataset(args: argparse.Namespace) -> int:
    """Turn reviewed defects into a training dataset."""
    from roadeye.reporting.training_export import export_reviewed_dataset

    stats = export_reviewed_dataset(
        args.db,
        args.output,
        evidence_dir=args.evidence,
        name=args.name,
        include_negatives=not args.no_negatives,
    )
    print(stats.summary())
    print(f"\nwrote {args.output}/")
    if stats.images == 0:
        print(
            "\nNothing was exported. Review some defects first: roadeye review --db ...",
            file=sys.stderr,
        )
        return 1
    if len(stats.surveys) < 2:
        print(
            "\nNOTE: only one survey, so everything is in the train split. A single "
            "drive cannot produce an honest held-out set (ADR-008).",
            file=sys.stderr,
        )
    return 0


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
        to_geojson(defects, args.geojson)
        print(f"wrote {args.geojson} ({len(defects)} features)")
    if not args.csv and not args.geojson:
        print(json.dumps(summarize(defects), indent=2))
    return 0


def _build_anonymizer(args: argparse.Namespace) -> object | None:
    """Build the redactor a command was asked for, or refuse clearly.

    ``--no-redact`` is spelled out rather than being the absence of a flag, so that
    writing unredacted images is always something someone chose.
    """
    from roadeye.privacy.anonymizer import Anonymizer
    from roadeye.privacy.detectors import TorchvisionPersonVehicleDetector
    from roadeye.privacy.redaction import RedactionConfig, RedactionMethod

    if getattr(args, "no_redact", False):
        return None

    detector = TorchvisionPersonVehicleDetector(score_threshold=args.redact_confidence)
    return Anonymizer(
        detector,
        config=RedactionConfig(
            method=RedactionMethod(args.redact_method),
            blocks_across=args.redact_blocks,
        ),
    )


def _cmd_redact(args: argparse.Namespace) -> int:
    """Redact people and vehicles out of a directory of images, in place or to a copy."""
    import numpy as np
    from PIL import Image

    from roadeye.privacy.base import RedactionError
    from roadeye.vision.annotate import iter_images

    images = iter_images(args.images)
    if not images:
        print(f"no images found at {args.images}", file=sys.stderr)
        return 1

    try:
        anonymizer = _build_anonymizer(args)
    except RedactionError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if anonymizer is None:
        print("--no-redact does nothing here; this command exists to redact", file=sys.stderr)
        return 2

    out_dir = Path(args.output) if args.output else None
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)

    totals = {"images": 0, "regions": 0, "person": 0, "vehicle": 0}
    for path in images:
        pixels = np.asarray(Image.open(path).convert("RGB"), dtype="uint8")
        redacted, report = anonymizer.redact(pixels)  # type: ignore[attr-defined]
        destination = (out_dir / path.name) if out_dir is not None else path
        Image.fromarray(redacted).save(destination, quality=90)

        totals["images"] += 1
        totals["regions"] += report.region_count
        for kind, count in report.to_json()["by_kind"].items():
            totals[kind] = totals.get(kind, 0) + count
        if args.verbose:
            print(f"{path.name:<32} {report.region_count:3d} regions")

    print(f"\ndetector   {anonymizer.detector_id}")  # type: ignore[attr-defined]
    print(f"images     {totals['images']}")
    print(
        f"regions    {totals['regions']} ({totals.get('person', 0)} person, "
        f"{totals.get('vehicle', 0)} vehicle)"
    )
    if out_dir is None:
        print("\nOriginals were OVERWRITTEN. Redaction is not reversible, by design.")
    else:
        print(f"\nwrote {out_dir}/ — the originals are untouched and still identifiable")
    print(
        "\nRedaction is best-effort. A detector that missed somebody has produced an "
        "image with somebody in it; this is not a guarantee of anonymity."
    )
    return 0


def _cmd_retention(args: argparse.Namespace) -> int:
    """Delete artefacts past their retention period, and log every deletion."""
    from roadeye.privacy.retention import RetentionPolicy, apply_retention

    policy = RetentionPolicy(
        raw_video_days=args.raw_video_days,
        frames_days=args.frames_days,
    )
    sweep = apply_retention(args.root, policy, delete=args.delete)
    print(sweep.summary())
    for candidate in sweep.candidates:
        mark = "x" if candidate.deleted else "-"
        print(f"  {mark} {candidate.kind:<10} {candidate.age_days:6.1f}d  {candidate.path}")
    if sweep.dry_run and sweep.candidates:
        print("\nNothing was deleted. Re-run with --delete to actually remove these.")
    if args.json:
        Path(args.json).write_text(json.dumps(sweep.to_json(), indent=2), encoding="utf-8")
        print(f"wrote {args.json}")
    return 0


def _cmd_roads(args: argparse.Namespace) -> int:
    """Fetch or import a road network for map matching."""
    from roadeye.map_matching.osm import BBox, fetch_overpass, parse_osm_xml

    if bool(args.bbox) == bool(args.osm_file):
        print("give exactly one of --bbox or --osm-file", file=sys.stderr)
        return 2

    if args.osm_file:
        bbox = BBox.parse(args.bbox) if args.bbox else None
        network = parse_osm_xml(args.osm_file, bbox=bbox)
    else:
        bbox = BBox.parse(args.bbox)
        print(f"fetching drivable roads for {bbox.overpass()} from {args.endpoint} ...")
        try:
            network = fetch_overpass(bbox, endpoint=args.endpoint)
        except OSError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    if not len(network):
        print(
            "no drivable roads found — check the bbox order (min_lat,min_lon,max_lat,max_lon)",
            file=sys.stderr,
        )
        return 1

    out = network.save(args.output)
    streets = network.named_streets()
    print(f"wrote {out} — {len(network)} segments, {len(streets)} named streets")
    for name, count in sorted(streets.items(), key=lambda kv: -kv[1])[:10]:
        print(f"  {count:5d}  {name}")
    print(f"\n{network.attribution} — this obligation travels with any export built on it.")
    return 0


def _cmd_match_roads(args: argparse.Namespace) -> int:
    """Assign stored defects to road segments."""
    from roadeye.map_matching.matcher import MatchingConfig, match_defects
    from roadeye.map_matching.network import RoadNetwork

    network = RoadNetwork.load(args.roads)
    config = MatchingConfig(
        max_distance_m=args.max_distance_m,
        max_heading_delta_deg=args.max_heading_delta_deg,
        ambiguity_margin_m=args.ambiguity_margin_m,
        require_named=args.require_named,
    )

    with Database(args.db) as db:
        defects = db.list_defects()
        headings = db.representative_headings()
        matched, stats = match_defects(defects, network, headings=headings, config=config)
        if not args.dry_run:
            db.upsert_defects(
                [d for d, before in zip(matched, defects, strict=True) if d is not before]
            )

    total = len(defects)
    print(f"{len(network)} road segments, {total} defects, {len(headings)} with a heading")
    print(json.dumps(dict(sorted(stats.items())), indent=2))
    if total:
        print(
            f"\nmatched {stats.get('matched', 0)}/{total} ({stats.get('matched', 0) / total:.0%})"
        )
    if args.dry_run:
        print("\n--dry-run: nothing was written")
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


def _add_redaction_args(parser: argparse.ArgumentParser) -> None:
    """Redaction options, shared by every command that can write an evidence image."""
    parser.add_argument(
        "--redact-confidence",
        type=float,
        default=0.35,
        help="detector threshold. LOWER is safer here — a missed person is the costly "
        "error, an over-blurred bollard is not (default: %%(default)s)".replace("%%", "%"),
    )
    parser.add_argument(
        "--redact-method",
        choices=[m.value for m in RedactionMethod],
        default=RedactionMethod.MOSAIC.value,
        help="mosaic keeps the scene readable; solid destroys the region completely",
    )
    parser.add_argument(
        "--redact-blocks",
        type=int,
        default=4,
        help="blocks across the shorter side of a region (default: %(default)s)",
    )


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
    p.add_argument(
        "--no-evidence",
        action="store_true",
        help="skip writing per-defect evidence images (they are what makes review possible)",
    )
    p.add_argument("--min-confidence", type=float, default=None)
    p.add_argument(
        "--fake-detections",
        type=int,
        default=1,
        help="synthetic detections per frame (fake detector only)",
    )
    p.add_argument(
        "--no-redact",
        action="store_true",
        help="write evidence images WITHOUT blurring people and vehicles. Legitimate "
        "for local-only processing; the images must then never leave this machine",
    )
    _add_redaction_args(p)
    p.set_defaults(func=_cmd_process)

    p = sub.add_parser("review", help="launch the human review UI")
    p.add_argument("--db", required=True)
    p.add_argument("--evidence", help="evidence image directory (default: beside the database)")
    p.add_argument("--port", type=int, default=8010)
    p.add_argument(
        "--host",
        default="127.0.0.1",
        help="bind address; leave as localhost — there is no authentication",
    )
    p.set_defaults(func=_cmd_review)

    p = sub.add_parser(
        "dashboard",
        help="launch the municipal dashboard — a map of every defect",
    )
    p.add_argument("--db", required=True)
    p.add_argument("--evidence", help="evidence image directory (default: beside the database)")
    p.add_argument("--port", type=int, default=8010)
    p.add_argument(
        "--roads",
        help="road network from 'roadeye roads'; draws streets without needing a tile server",
    )
    p.add_argument(
        "--host",
        default="127.0.0.1",
        help="bind address; leave as localhost — there is no authentication",
    )
    p.set_defaults(func=_cmd_dashboard)

    p = sub.add_parser("export", help="export defects to CSV / GeoJSON")
    p.add_argument("--db", required=True)
    p.add_argument("--csv")
    p.add_argument("--geojson")
    p.add_argument("--damage-class", choices=[c.value for c in DamageClass])
    p.add_argument("--status", choices=[s.value for s in DefectStatus])
    p.add_argument("--min-confidence", type=float)
    p.set_defaults(func=_cmd_export)

    p = sub.add_parser(
        "export-dataset",
        help="turn reviewed defects into a training dataset (closes the review loop)",
    )
    p.add_argument("--db", required=True)
    p.add_argument("--output", required=True, help="dataset directory to write")
    p.add_argument("--evidence", help="evidence image directory (default: beside the database)")
    p.add_argument("--name", help="dataset name recorded in the manifest")
    p.add_argument(
        "--no-negatives",
        action="store_true",
        help="omit rejected defects — they are hard negatives and usually worth keeping",
    )
    p.set_defaults(func=_cmd_export_dataset)

    p = sub.add_parser(
        "redact",
        help="blur people and vehicles out of images before they leave this machine",
    )
    p.add_argument("images", help="image file or directory")
    p.add_argument(
        "--output",
        help="write redacted copies here (default: OVERWRITE the originals in place)",
    )
    _add_redaction_args(p)
    p.add_argument("--verbose", "-v", action="store_true")
    p.set_defaults(func=_cmd_redact, no_redact=False)

    p = sub.add_parser(
        "retention",
        help="delete artefacts past their retention period, logging each deletion",
    )
    p.add_argument("root", help="directory to sweep")
    p.add_argument(
        "--delete",
        action="store_true",
        help="actually delete. Without this the sweep only reports what is due",
    )
    p.add_argument("--raw-video-days", type=int, default=RetentionPolicy.raw_video_days)
    p.add_argument("--frames-days", type=int, default=RetentionPolicy.frames_days)
    p.add_argument("--json", help="write the full sweep record here")
    p.set_defaults(func=_cmd_retention)

    p = sub.add_parser(
        "roads",
        help="fetch or import an OpenStreetMap road network for map matching",
    )
    p.add_argument(
        "--bbox",
        help="min_lat,min_lon,max_lat,max_lon — fetch this area from Overpass",
    )
    p.add_argument(
        "--osm-file",
        help="import an .osm XML file instead of fetching (works with no network)",
    )
    p.add_argument("--output", required=True, help="road network file to write (.json[.gz])")
    p.add_argument(
        "--endpoint",
        default=DEFAULT_OVERPASS_ENDPOINT,
        help="Overpass instance (default: %(default)s)",
    )
    p.set_defaults(func=_cmd_roads)

    p = sub.add_parser(
        "match-roads",
        help="assign stored defects to road segments",
    )
    p.add_argument("--db", required=True)
    p.add_argument("--roads", required=True, help="road network file from 'roadeye roads'")
    p.add_argument(
        "--max-distance-m",
        type=float,
        default=MatchingConfig.max_distance_m,
        help="never match a defect further than this from a centreline (default: %(default)s)",
    )
    p.add_argument(
        "--max-heading-delta-deg",
        type=float,
        default=MatchingConfig.max_heading_delta_deg,
        help="reject segments disagreeing with the vehicle heading by more (default: %(default)s)",
    )
    p.add_argument(
        "--ambiguity-margin-m",
        type=float,
        default=MatchingConfig.ambiguity_margin_m,
        help="refuse when two different streets score within this (default: %(default)s)",
    )
    p.add_argument(
        "--require-named",
        action="store_true",
        help="match only to named streets",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would change without writing to the database",
    )
    p.set_defaults(func=_cmd_match_roads)

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
