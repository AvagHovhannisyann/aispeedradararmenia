"""Controlled vocabularies for the RoadEye domain.

These enums are part of the on-disk and on-wire contract. Values are stored in the
database and exported in reports, so **renaming a value is a breaking change**.
Add new members; do not repurpose existing ones.
"""

from __future__ import annotations

from enum import Enum


class DamageClass(str, Enum):
    """Initial road-damage ontology.

    Deliberately adopted from RDD2022 rather than invented, so that RoadEye results
    stay comparable to published benchmarks. The original RDD codes are preserved in
    :data:`RDD_CODE_TO_DAMAGE_CLASS` rather than used directly as identifiers, because
    "D20" means nothing to a municipal inspector reading a report.
    """

    LONGITUDINAL_CRACK = "longitudinal_crack"
    TRANSVERSE_CRACK = "transverse_crack"
    ALLIGATOR_CRACK = "alligator_crack"
    POTHOLE = "pothole"


#: RDD2022 damage codes -> RoadEye classes.
#:
#: Note: the upstream dataset spells D20 "Aligator Crack" (sic). We normalise the
#: spelling but keep the code so provenance back to the source annotation is exact.
RDD_CODE_TO_DAMAGE_CLASS: dict[str, DamageClass] = {
    "D00": DamageClass.LONGITUDINAL_CRACK,
    "D10": DamageClass.TRANSVERSE_CRACK,
    "D20": DamageClass.ALLIGATOR_CRACK,
    "D40": DamageClass.POTHOLE,
}


class Severity(str, Enum):
    """How bad the defect is.

    Explicitly *not* the same thing as detector confidence, and not the same thing as
    repair priority. See :class:`SeveritySource` — an unattributed severity is worse
    than no severity, because it invites a municipality to trust a guess.
    """

    UNASSESSED = "unassessed"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SeveritySource(str, Enum):
    """Who or what assigned the severity. Always recorded alongside it."""

    AI = "ai"
    HUMAN = "human"
    GEOMETRIC_ESTIMATE = "geometric_estimate"
    OTHER = "other"


class DefectStatus(str, Enum):
    """Review lifecycle of a defect.

    ``PROBABLE`` is the only state a machine may assign. Everything else requires a
    human action, because a municipality acting on unverified output is the failure
    mode that ends the pilot.
    """

    PROBABLE = "probable"
    VERIFIED = "verified"
    REJECTED = "rejected"
    REPAIRED = "repaired"
    REOPENED = "reopened"


class LocationMethod(str, Enum):
    """How a coordinate was derived.

    This exists because the phone's GPS is the *camera's* position, never the defect's.
    Conflating the two is the single most consequential geolocation error available to
    us, so the method is a required field everywhere a coordinate appears.

    Ordered roughly by increasing accuracy.
    """

    #: Raw phone fix at (or nearest to) the observation instant.
    PHONE_GPS = "phone_gps"
    #: Linear interpolation between the two bracketing phone fixes. MVP default.
    INTERPOLATED_PHONE_GPS = "interpolated_phone_gps"
    #: Interpolated fix snapped to a road-network segment.
    ROAD_SEGMENT_MATCHED = "road_segment_matched"
    #: Ray-to-ground-plane projection using camera intrinsics + pose. Future.
    GROUND_PROJECTED = "ground_projected"
    #: A human moved the marker in the dashboard. Highest trust; never overwritten.
    MANUAL_CORRECTION = "manual_correction"


class FrameQuality(str, Enum):
    """Outcome of the pre-inference image-quality gate.

    ``DEGRADED`` deliberately exists so marginal frames are neither silently trusted
    nor silently discarded: they are analysed, but their detections carry the flag so
    failures can later be explained ("most misses happened after sunset").
    """

    ACCEPTED = "accepted"
    DEGRADED = "degraded"
    REJECTED = "rejected"


class SamplingMode(str, Enum):
    """Frame-sampling strategy."""

    #: Analyse N frames per second of video.
    FIXED_FPS = "fixed_fps"
    #: Analyse one frame every N seconds.
    FIXED_INTERVAL = "fixed_interval"
    #: Analyse one frame every N metres travelled. Preferred while moving: sitting at
    #: a red light must not generate 500 near-identical images.
    DISTANCE = "distance"


class ReviewAction(str, Enum):
    """What a human reviewer did. Each becomes training signal."""

    APPROVE = "approve"
    REJECT = "reject"
    CHANGE_CLASS = "change_class"
    CHANGE_SEVERITY = "change_severity"
    ADJUST_LOCATION = "adjust_location"
    ADD_NOTE = "add_note"


class DefectTrend(str, Enum):
    """Cross-survey change state.

    ``POSSIBLY_REPAIRED`` is deliberately hedged: a single missing observation is not
    evidence of repair, it is evidence of one drive. See ``docs/DATA_MODEL.md``.
    """

    NEW = "new"
    STABLE = "stable"
    WORSENING = "worsening"
    POSSIBLY_REPAIRED = "possibly_repaired"
    UNKNOWN = "unknown"
