"""RoadEye domain models.

These are the *application's* vocabulary. Nothing here may import torch, torchvision,
mmdet, cv2 or any other ML/vision framework: detector adapters convert framework output
into these types at the boundary. That rule is what makes the detector replaceable
(ADR-004) and what keeps the test suite runnable on a CPU-only machine with no ffmpeg.

Provenance is not decoration. A municipal defect must be able to answer "why do you
believe this exists?", which means every object carries enough identity to walk back:

    Defect -> DefectObservation -> Detection -> Frame -> Survey -> raw video + GPS
                                        |
                                        +-> ModelVersion -> DatasetVersion
                                        +-> ProcessingRun -> config + code commit
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from roadeye.domain.enums import (
    DamageClass,
    DefectStatus,
    DefectTrend,
    FrameQuality,
    LocationMethod,
    ReviewAction,
    Severity,
    SeveritySource,
)

#: Version of the domain schema itself. Bump on any breaking change and write a
#: migration. Never change a field's meaning while leaving this untouched.
DOMAIN_SCHEMA_VERSION = 1


def _utcnow() -> datetime:
    return datetime.now(UTC)


class _Base(BaseModel):
    """Strict base: unknown fields are an error, not silently dropped.

    Silently ignoring an unexpected key is how a collector-side rename becomes a
    week-long debugging session. We would rather fail loudly at the boundary.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


# --------------------------------------------------------------------- geo & refs


class GeoPoint(_Base):
    """A coordinate that knows how it was derived and how wrong it might be.

    ``method`` and ``uncertainty_m`` are **required**. A bare lat/lon in this system is
    a bug: it invites a municipality to read six decimal places as centimetre accuracy
    when the underlying fix was a 12 m consumer GPS estimate.
    """

    lat: float = Field(ge=-90.0, le=90.0)
    lon: float = Field(ge=-180.0, le=180.0)
    method: LocationMethod
    uncertainty_m: float = Field(ge=0.0)
    altitude_m: float | None = None


class RoadSegmentRef(_Base):
    """A reference to a road segment in an *external* network.

    Kept as a separable reference rather than denormalised columns, deliberately. Road
    geometry comes from OpenStreetMap, whose ODbL carries share-alike obligations on
    data; our defect coordinates are our own. Keeping the OSM-derived identifier at
    arm's length preserves the option to detach it. See ``docs/LICENSE_AUDIT.md``.
    """

    source: str = Field(description="e.g. 'osm'. Names the data source and its licence.")
    segment_id: str
    name: str | None = None
    #: Distance from the observation to the matched segment; a weak match is a warning.
    match_distance_m: float | None = Field(default=None, ge=0.0)
    #: |vehicle heading - segment bearing|, degrees. Large values mean a suspect match.
    heading_delta_deg: float | None = Field(default=None, ge=0.0, le=180.0)


class BoundingBox(_Base):
    """Axis-aligned box in **pixel** coordinates of the source frame.

    Stored in absolute pixels rather than normalised, together with the frame size, so
    a box can always be redrawn on the original evidence image without guessing what it
    was normalised against.
    """

    x1: float = Field(ge=0.0)
    y1: float = Field(ge=0.0)
    x2: float = Field(ge=0.0)
    y2: float = Field(ge=0.0)

    @model_validator(mode="after")
    def _ordered(self) -> BoundingBox:
        if self.x2 <= self.x1 or self.y2 <= self.y1:
            raise ValueError(
                f"degenerate bounding box: ({self.x1},{self.y1})-({self.x2},{self.y2})"
            )
        return self

    @property
    def width(self) -> float:
        return self.x2 - self.x1

    @property
    def height(self) -> float:
        return self.y2 - self.y1

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x1 + self.x2) / 2.0, (self.y1 + self.y2) / 2.0)

    @property
    def ground_contact(self) -> tuple[float, float]:
        """Bottom-centre pixel — where the defect meets the road plane.

        This is the point fed to ray-to-ground projection (``docs/GEOLOCATION.md``).
        For a pothole the bottom edge of the box is approximately its near rim, which
        is a far better ground proxy than the box centre.
        """
        return ((self.x1 + self.x2) / 2.0, self.y2)

    def iou(self, other: BoundingBox) -> float:
        """Intersection over union. Used for temporal association."""
        ix1, iy1 = max(self.x1, other.x1), max(self.y1, other.y1)
        ix2, iy2 = min(self.x2, other.x2), min(self.y2, other.y2)
        if ix2 <= ix1 or iy2 <= iy1:
            return 0.0
        inter = (ix2 - ix1) * (iy2 - iy1)
        union = self.area + other.area - inter
        return inter / union if union > 0 else 0.0


# ------------------------------------------------------------------------ survey


class Survey(_Base):
    """One drive. Immutable evidence: never mutated after ingest."""

    schema_version: int = DOMAIN_SCHEMA_VERSION
    survey_id: str
    started_at: datetime
    ended_at: datetime | None = None
    #: Device clock at the first video frame. The anchor of all time arithmetic.
    recording_start_epoch_ms: int
    video_path: str | None = None
    device: dict[str, Any] = Field(default_factory=dict)
    app_version: str | None = None
    notes: str | None = None
    #: Anything the loader had to discard, so the run can report it honestly.
    ingest_stats: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _chronological(self) -> Survey:
        if self.ended_at is not None and self.ended_at < self.started_at:
            raise ValueError("survey ended_at precedes started_at")
        return self


class Frame(_Base):
    """A single sampled image extracted from the survey video."""

    frame_id: str
    survey_id: str
    #: Presentation timestamp within the video.
    video_time_s: float = Field(ge=0.0)
    #: Absolute device time = recording_start_epoch_ms + video_time_s * 1000.
    t_epoch_ms: int
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    image_path: str | None = None
    #: Where the *camera* was. Never assumed to be where a defect is.
    observation_location: GeoPoint | None = None
    speed_mps: float | None = None
    heading_deg: float | None = None
    quality: FrameQuality = FrameQuality.ACCEPTED
    quality_scores: dict[str, float] = Field(default_factory=dict)


class Detection(_Base):
    """One model output in one frame. Not a defect — see :class:`Defect`."""

    detection_id: str
    frame_id: str
    survey_id: str
    damage_class: DamageClass
    confidence: float = Field(ge=0.0, le=1.0)
    bbox: BoundingBox
    #: Segmentation mask, RLE or polygon. Null in MVP; the field exists from day one so
    #: adding segmentation later is not a schema migration.
    mask: dict[str, Any] | None = None
    model_id: str
    #: Populated once the detection is associated across frames.
    track_id: str | None = None


class Track(_Base):
    """A sequence of detections believed to show the same object across frames.

    The first deduplication layer: it collapses the ~6-20 frames in which a single
    pothole is visible as the car approaches it.
    """

    track_id: str
    survey_id: str
    damage_class: DamageClass
    detection_ids: list[str]
    first_frame_id: str
    last_frame_id: str
    first_t_epoch_ms: int
    last_t_epoch_ms: int
    max_confidence: float = Field(ge=0.0, le=1.0)
    mean_confidence: float = Field(ge=0.0, le=1.0)

    @property
    def observation_count(self) -> int:
        return len(self.detection_ids)


class DefectObservation(_Base):
    """A defect seen during one survey. Links a defect to its evidence."""

    observation_id: str
    defect_id: str
    survey_id: str
    track_id: str | None = None
    detection_ids: list[str] = Field(default_factory=list)
    observed_at: datetime
    confidence: float = Field(ge=0.0, le=1.0)
    location: GeoPoint
    representative_frame_id: str | None = None


class Defect(_Base):
    """RoadEye's belief that one physical road problem exists at one place.

    This is the municipal unit of work. One pothole seen in twenty frames across three
    surveys is **one** defect with twenty-plus observations, never twenty defects.
    """

    schema_version: int = DOMAIN_SCHEMA_VERSION
    defect_id: str
    damage_class: DamageClass

    #: Best current estimate of where the *defect* is. Not the camera position.
    location: GeoPoint
    road: RoadSegmentRef | None = None

    #: Aggregate detector confidence. Explicitly NOT severity and NOT repair priority.
    confidence: float = Field(ge=0.0, le=1.0)
    severity: Severity = Severity.UNASSESSED
    severity_source: SeveritySource = SeveritySource.OTHER

    status: DefectStatus = DefectStatus.PROBABLE
    trend: DefectTrend = DefectTrend.UNKNOWN

    observation_count: int = Field(default=0, ge=0)
    survey_ids: list[str] = Field(default_factory=list)
    first_seen: datetime
    last_seen: datetime
    #: The frame a reviewer should be shown first — highest-confidence view of this
    #: defect. Kept distinct from the image *path*: the frame id always exists, whereas
    #: the extracted JPEG may not have been written yet (or may have been purged under
    #: the retention policy in docs/PRIVACY.md).
    representative_frame_id: str | None = None
    representative_image_path: str | None = None

    #: Provenance. Required for auditability, not optional metadata.
    model_id: str | None = None
    processing_run_id: str | None = None

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    @model_validator(mode="after")
    def _severity_is_attributed(self) -> Defect:
        # An assessed severity with no stated source is exactly the false authority we
        # are trying to avoid shipping to a government.
        if (
            self.severity is not Severity.UNASSESSED
            and self.severity_source is SeveritySource.OTHER
        ):
            raise ValueError(
                "a severity other than UNASSESSED must declare a severity_source "
                "(ai / human / geometric_estimate)"
            )
        if self.last_seen < self.first_seen:
            raise ValueError("defect last_seen precedes first_seen")
        return self


class Review(_Base):
    """A human decision about a defect. Append-only: corrections are never overwritten.

    Every review is also training signal (``docs/ML_STRATEGY.md``), which is why the
    previous and new values are both retained.
    """

    review_id: str
    defect_id: str
    action: ReviewAction
    reviewer: str
    reviewed_at: datetime = Field(default_factory=_utcnow)
    previous_value: dict[str, Any] | None = None
    new_value: dict[str, Any] | None = None
    note: str | None = None


# ------------------------------------------------------------------- provenance


class DatasetVersion(_Base):
    """An immutable, identified snapshot of training data."""

    dataset_id: str
    name: str
    created_at: datetime = Field(default_factory=_utcnow)
    source: str
    license: str
    #: Free-text note on redistribution limits, e.g. the RDD2022 CC BY / CC BY-SA
    #: conflict. Read by the model registry when deciding distribution_allowed.
    license_notes: str | None = None
    image_count: int = Field(default=0, ge=0)
    annotation_count: int = Field(default=0, ge=0)
    class_map: dict[str, str] = Field(default_factory=dict)
    #: Route/survey IDs per split. Splitting by route, not by frame, is what prevents
    #: adjacent-frame leakage inflating our metrics (``docs/ML_STRATEGY.md``).
    split_routes: dict[str, list[str]] = Field(default_factory=dict)
    checksum: str | None = None


class ModelVersion(_Base):
    """A trained model and everything needed to trust or reproduce it.

    ``training_data_licenses`` and ``distribution_allowed`` are mandatory because of
    BLOCKING-1 in ``docs/LICENSE_AUDIT.md``: a model whose lineage includes RDD2022 may
    not be distributable, and that fact must travel with the weights rather than living
    in someone's memory.
    """

    model_id: str
    name: str
    architecture: str
    framework: str
    framework_version: str | None = None
    weights_path: str | None = None
    weights_origin: str | None = None
    weights_license: str | None = None
    dataset_id: str | None = None
    training_data_licenses: list[str] = Field(default_factory=list)
    distribution_allowed: bool = False
    classes: list[DamageClass] = Field(default_factory=list)
    input_resolution: tuple[int, int] | None = None
    git_commit: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    metrics: dict[str, float] = Field(default_factory=dict)
    notes: str | None = None

    @field_validator("distribution_allowed")
    @classmethod
    def _warn_on_unlicensed_distribution(cls, v: bool, info: Any) -> bool:
        if v and not info.data.get("training_data_licenses"):
            raise ValueError(
                "distribution_allowed=True requires training_data_licenses to be stated"
            )
        return v


class ProcessingRun(_Base):
    """One execution of the pipeline over one survey. The audit anchor."""

    run_id: str
    survey_id: str
    started_at: datetime = Field(default_factory=_utcnow)
    finished_at: datetime | None = None
    model_id: str | None = None
    git_commit: str | None = None
    #: The complete effective configuration, so a run can be reproduced exactly.
    config: dict[str, Any] = Field(default_factory=dict)
    frames_decoded: int = 0
    frames_sampled: int = 0
    frames_rejected_quality: int = 0
    detections: int = 0
    tracks: int = 0
    defects: int = 0
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    duration_s: float | None = None

    def summary(self) -> str:
        return (
            f"run={self.run_id} survey={self.survey_id} "
            f"decoded={self.frames_decoded} sampled={self.frames_sampled} "
            f"rejected={self.frames_rejected_quality} detections={self.detections} "
            f"tracks={self.tracks} defects={self.defects} "
            f"errors={len(self.errors)} warnings={len(self.warnings)}"
        )
