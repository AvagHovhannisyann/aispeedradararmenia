"""End-to-end processing: survey bundle -> defects.

This is the orchestration layer. It owns *sequence*, not algorithms — every stage lives
in its own module so it can be tested, replaced and reasoned about alone.

    bundle -> sampling plan -> frames -> quality gate -> detector
           -> tracking -> observations -> clustering -> defects -> storage

Every run produces a :class:`~roadeye.domain.models.ProcessingRun` recording the
complete effective configuration, the model used, the code commit and what happened.
Without that, a defect on a municipal map cannot answer "why do you believe this
exists?", which is the whole auditability claim.
"""

from __future__ import annotations

import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from roadeye.clustering.geo import ClusterCandidate, ClusteringConfig, build_defects
from roadeye.domain.enums import FrameQuality
from roadeye.domain.models import (
    BoundingBox,
    Defect,
    DefectObservation,
    Detection,
    Frame,
    GeoPoint,
    ProcessingRun,
    Survey,
)
from roadeye.geolocation.timesync import video_time_to_epoch_ms
from roadeye.ingest.bundle import SurveyBundle, load_bundle
from roadeye.quality.metrics import QualityConfig, assess
from roadeye.reporting.evidence import save_defect_evidence
from roadeye.storage.db import Database
from roadeye.tracking.tracker import GreedyIouTracker, TrackingConfig
from roadeye.video.decoder import (
    FrameSource,
    ImageSequenceFrameSource,
    SyntheticFrameSource,
)
from roadeye.video.sampling import SamplingConfig, build_sampling_plan
from roadeye.vision.base import RoadDamageDetector


@dataclass
class PipelineConfig:
    """Everything tunable about a run.

    Serialised verbatim into the processing run. Any threshold that affects output
    lives here rather than being hard-coded at a call site — otherwise a run cannot be
    reproduced from its own record.
    """

    sampling: SamplingConfig = field(default_factory=SamplingConfig)
    tracking: TrackingConfig = field(default_factory=TrackingConfig)
    clustering: ClusteringConfig = field(default_factory=ClusteringConfig)
    quality: QualityConfig = field(default_factory=QualityConfig)
    #: Detections below this are discarded before tracking.
    min_detection_confidence: float = 0.25
    #: Drop GPS fixes worse than this during ingest.
    max_gps_accuracy_m: float = 25.0
    #: Analyse frames the quality gate marked DEGRADED (flagged, not silently trusted).
    analyse_degraded_frames: bool = True
    #: Where to write per-defect evidence images. ``None`` skips extraction entirely.
    #: Without these a reviewer sees a frame id and nothing else, which makes review —
    #: the product's bottleneck — impossible.
    evidence_dir: Path | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "sampling": self.sampling.as_dict(),
            "tracking": self.tracking.as_dict(),
            "clustering": self.clustering.as_dict(),
            "quality": self.quality.as_dict(),
            "min_detection_confidence": self.min_detection_confidence,
            "max_gps_accuracy_m": self.max_gps_accuracy_m,
            "analyse_degraded_frames": self.analyse_degraded_frames,
            "evidence_dir": str(self.evidence_dir) if self.evidence_dir else None,
        }


@dataclass
class PipelineResult:
    """What a run produced, in memory. Persisted separately by :func:`process_survey`."""

    run: ProcessingRun
    survey: Survey
    frames: list[Frame] = field(default_factory=list)
    detections: list[Detection] = field(default_factory=list)
    defects: list[Defect] = field(default_factory=list)
    observations: list[DefectObservation] = field(default_factory=list)

    @property
    def defects_per_km(self) -> float | None:
        km = (self.survey.ingest_stats or {}).get("track_distance_m")
        if not km or km <= 0:
            return None
        return len(self.defects) / (km / 1000.0)


def _git_commit() -> str | None:
    """Current commit, for provenance. Returns ``None`` outside a git checkout."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def process_bundle(
    bundle: SurveyBundle,
    detector: RoadDamageDetector,
    *,
    frame_source: FrameSource | None = None,
    config: PipelineConfig | None = None,
    run_id: str | None = None,
) -> PipelineResult:
    """Run the pipeline over an already-loaded bundle.

    ``frame_source`` defaults to a :class:`SyntheticFrameSource` sized from the
    bundle's GPS timespan, which lets the whole pipeline run without ffmpeg. Pass a
    real source (``roadeye.video.decoder.open_video``) to analyse actual footage.
    """
    cfg = config or PipelineConfig()
    started = time.monotonic()
    run = ProcessingRun(
        run_id=run_id or f"run_{uuid.uuid4().hex[:12]}",
        survey_id=bundle.survey_id,
        model_id=detector.model_id,
        git_commit=_git_commit(),
        config=cfg.as_dict(),
    )
    run.warnings.extend(bundle.warnings)
    run.errors.extend(bundle.errors)

    survey = Survey(
        survey_id=bundle.survey_id,
        started_at=bundle.started_at,
        ended_at=bundle.ended_at,
        recording_start_epoch_ms=bundle.recording_start_epoch_ms,
        video_path=str(bundle.video_path) if bundle.video_path else None,
        device=bundle.device,
        app_version=bundle.route.get("app_version"),
        ingest_stats={
            **bundle.track.stats.as_dict(),
            "track_distance_m": round(bundle.track.total_distance_m(), 2),
        },
    )

    # A survey with no usable GPS can still be inspected by a human, but nothing it
    # produces can be placed on a map. Stop rather than emit unplaceable defects.
    if len(bundle.track) == 0:
        run.errors.append("no usable GPS fixes; refusing to emit unplaceable defects")
        run.finished_at = datetime.now(UTC)
        run.duration_s = round(time.monotonic() - started, 4)
        return PipelineResult(run=run, survey=survey)

    if frame_source is None:
        span_ms = (bundle.track.end_epoch_ms or 0) - bundle.recording_start_epoch_ms
        frame_source = SyntheticFrameSource(
            duration_s=max(0.0, span_ms / 1000.0), survey_id=bundle.survey_id
        )

    info = frame_source.info()
    plan = build_sampling_plan(
        duration_s=info.duration_s,
        recording_start_epoch_ms=bundle.recording_start_epoch_ms,
        config=cfg.sampling,
        track=bundle.track,
    )
    run.warnings.extend(plan.notes)
    run.frames_sampled = len(plan)

    frames: list[Frame] = []
    detections: list[Detection] = []
    tracker = GreedyIouTracker(cfg.tracking)
    det_counter = 0

    for video_time_s, image in frame_source.frames_at(plan.video_times):
        run.frames_decoded += 1
        t_epoch_ms = video_time_to_epoch_ms(bundle.recording_start_epoch_ms, video_time_s)

        quality = assess(image.pixels, cfg.quality)
        loc = bundle.track.locate(t_epoch_ms)

        frame = Frame(
            frame_id=image.frame_id,
            survey_id=bundle.survey_id,
            video_time_s=video_time_s,
            t_epoch_ms=t_epoch_ms,
            width=image.width,
            height=image.height,
            observation_location=(
                GeoPoint(
                    lat=loc.position.lat,
                    lon=loc.position.lon,
                    method=loc.method,
                    uncertainty_m=loc.uncertainty_m,
                )
                if loc
                else None
            ),
            speed_mps=loc.speed_mps if loc else None,
            heading_deg=loc.heading_deg if loc else None,
            quality=quality.verdict,
            quality_scores=quality.scores,
        )
        frames.append(frame)

        if quality.verdict is FrameQuality.REJECTED:
            run.frames_rejected_quality += 1
            continue
        if quality.verdict is FrameQuality.DEGRADED and not cfg.analyse_degraded_frames:
            run.frames_rejected_quality += 1
            continue

        frame_detections: list[Detection] = []
        for raw in detector.predict(image):
            if raw.confidence < cfg.min_detection_confidence:
                continue
            det_counter += 1
            frame_detections.append(
                Detection(
                    detection_id=f"{bundle.survey_id}:det{det_counter:06d}",
                    frame_id=frame.frame_id,
                    survey_id=bundle.survey_id,
                    damage_class=raw.damage_class,
                    confidence=raw.confidence,
                    bbox=BoundingBox(x1=raw.x1, y1=raw.y1, x2=raw.x2, y2=raw.y2),
                    mask=raw.mask,
                    model_id=detector.model_id,
                )
            )

        tracker.update(bundle.survey_id, frame.frame_id, t_epoch_ms, frame_detections)
        detections.extend(frame_detections)

    tracks = tracker.finish(bundle.survey_id)
    run.detections = len(detections)
    run.tracks = len(tracks)

    # One track becomes one observation: the pass in which a defect was seen. Its
    # position is taken from the frame where the detector was most confident, which is
    # typically the closest clear view.
    by_id = {d.detection_id: d for d in detections}
    frame_by_id = {f.frame_id: f for f in frames}
    candidates: list[ClusterCandidate] = []

    for track in tracks:
        members = [by_id[i] for i in track.detection_ids if i in by_id]
        if not members:
            continue
        best = max(members, key=lambda d: d.confidence)
        # Deliberately not named `frame`: that name belongs to the sampling loop above,
        # and reusing it here would shadow a `Frame` with a `Frame | None`.
        best_frame = frame_by_id.get(best.frame_id)
        if best_frame is None or best_frame.observation_location is None:
            run.warnings.append(f"track {track.track_id} has no usable position and was dropped")
            continue

        observation = DefectObservation(
            observation_id=f"{track.track_id}:obs",
            # Filled in by clustering; a placeholder here keeps the model non-optional.
            defect_id="",
            survey_id=bundle.survey_id,
            track_id=track.track_id,
            detection_ids=list(track.detection_ids),
            observed_at=datetime.fromtimestamp(track.first_t_epoch_ms / 1000.0, tz=UTC),
            confidence=track.max_confidence,
            location=best_frame.observation_location,
            representative_frame_id=best.frame_id,
        )
        candidates.append(
            ClusterCandidate(observation=observation, damage_class=track.damage_class)
        )

    defects, observations = build_defects(
        candidates,
        config=cfg.clustering,
        model_id=detector.model_id,
        processing_run_id=run.run_id,
        defect_id_prefix=f"{bundle.survey_id}_def",
    )
    run.defects = len(defects)

    if cfg.evidence_dir is not None and defects:
        saved = _extract_evidence(defects, frames, detections, frame_source, cfg.evidence_dir)
        if saved < len(defects):
            run.warnings.append(
                f"evidence images written for {saved} of {len(defects)} defects; "
                "the rest cannot be reviewed visually"
            )

    run.finished_at = datetime.now(UTC)
    run.duration_s = round(time.monotonic() - started, 4)

    return PipelineResult(
        run=run,
        survey=survey,
        frames=frames,
        detections=detections,
        defects=defects,
        observations=observations,
    )


def _extract_evidence(
    defects: list[Defect],
    frames: list[Frame],
    detections: list[Detection],
    frame_source: FrameSource,
    evidence_dir: Path,
) -> int:
    """Write one context + crop image per defect. Returns how many succeeded.

    Deliberately a **second pass** rather than caching pixels during the main loop.
    Which frame is "representative" is only known after clustering, so caching would
    mean holding every analysed frame's pixels in memory — hundreds of megabytes for a
    30-minute survey — to use a handful. Re-requesting the few we need costs one extra
    read each and bounded memory.
    """
    frame_by_id = {f.frame_id: f for f in frames}
    detection_by_frame: dict[str, Detection] = {}
    for det in detections:
        best = detection_by_frame.get(det.frame_id)
        if best is None or det.confidence > best.confidence:
            detection_by_frame[det.frame_id] = det

    wanted: dict[float, list[Defect]] = {}
    for defect in defects:
        frame = frame_by_id.get(defect.representative_frame_id or "")
        if frame is not None:
            wanted.setdefault(frame.video_time_s, []).append(defect)
    if not wanted:
        return 0

    saved = 0
    for video_time_s, image in frame_source.frames_at(sorted(wanted)):
        for defect in wanted.get(video_time_s, []):
            detection = detection_by_frame.get(defect.representative_frame_id or "")
            paths = save_defect_evidence(
                defect.defect_id,
                image.pixels,
                detection.bbox if detection else None,
                evidence_dir,
            )
            if paths is not None:
                defect.representative_image_path = paths.context
                saved += 1
    return saved


def open_frame_source(bundle: SurveyBundle) -> FrameSource | None:
    """Pick the best available frame source for a bundle.

    Preference order: a real video if one is present and decodable, then a ``frames/``
    directory of images, then nothing (the caller falls back to synthetic frames).

    The middle case matters more than it looks: without ffmpeg there is otherwise no
    way to run a real detector over real pixels, so a bundle carrying extracted frames
    keeps the whole chain testable and usable on a machine with no video stack.
    """
    if bundle.video_path is not None and bundle.video_path.exists():
        try:
            from roadeye.video.decoder import open_video

            return open_video(bundle.video_path)
        except (RuntimeError, FileNotFoundError):
            # ffmpeg/PyAV missing. Fall through — a frames directory may still work.
            pass

    frames_dir = bundle.path / "frames"
    if frames_dir.is_dir():
        try:
            return ImageSequenceFrameSource(
                frames_dir,
                duration_s=bundle.duration_s(),
                survey_id=bundle.survey_id,
            )
        except (FileNotFoundError, NotADirectoryError, RuntimeError):
            return None
    return None


def process_survey(
    bundle_path: str | Path,
    detector: RoadDamageDetector,
    *,
    db: Database | None = None,
    frame_source: FrameSource | None = None,
    config: PipelineConfig | None = None,
) -> PipelineResult:
    """Load a bundle, process it, and persist the result if a database is given."""
    cfg = config or PipelineConfig()
    bundle = load_bundle(bundle_path, max_accuracy_m=cfg.max_gps_accuracy_m)
    if frame_source is None:
        frame_source = open_frame_source(bundle)
    result = process_bundle(bundle, detector, frame_source=frame_source, config=cfg)

    if db is not None:
        db.upsert_survey(result.survey)
        db.insert_frames(result.frames)
        db.insert_detections(result.detections)
        db.upsert_defects(result.defects)
        db.insert_observations(result.observations)
        db.upsert_processing_run(result.run)

    return result
