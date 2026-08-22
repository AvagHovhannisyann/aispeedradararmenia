"""Temporal association: linking detections of the same object across frames.

The first of RoadEye's two deduplication layers. As a car approaches a pothole it
appears in roughly 5-20 sampled frames, growing and drifting downward in the image. A
pipeline that reports each frame detection as a municipal defect is unusable — the
dashboard fills with hundreds of phantom potholes and the municipality stops trusting it
within minutes.

Deliberately simple: greedy IoU-based association with a class constraint and a
time-gap limit. No Kalman filter, no re-identification network, no deep-SORT
dependency. That is a real decision, not laziness:

* Our frames are sampled ~2.5 m apart, so consecutive views of one defect overlap
  heavily and IoU is a strong signal.
* A learned tracker would add a dependency, a model file and a failure mode for a
  problem we have not yet proven is hard.
* Every association is inspectable and explainable to a reviewer, which matters for an
  auditable government product.

If measurement later shows this is the limiting factor, it is one module to replace.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from roadeye.domain.enums import DamageClass
from roadeye.domain.models import BoundingBox, Detection, Track


@dataclass(frozen=True, slots=True)
class TrackingConfig:
    """Association thresholds. Recorded in the processing run."""

    #: Minimum IoU between a candidate detection and a track's last box.
    min_iou: float = 0.15
    #: Maximum time gap before a track is considered finished, in seconds. At 2.5 m
    #: spacing and urban speeds, consecutive frames are well under 1 s apart.
    max_gap_s: float = 2.0
    #: How many consecutive frames a track may go unmatched before being closed.
    max_missed_frames: int = 2
    #: Tracks with fewer detections than this are dropped as probable noise. Set to 1
    #: to keep everything — appropriate when recall matters more than precision.
    min_track_length: int = 1
    #: Require the same damage class to associate. Switching this off would let a
    #: pothole absorb a crack detection.
    require_same_class: bool = True

    def as_dict(self) -> dict[str, object]:
        return {
            "min_iou": self.min_iou,
            "max_gap_s": self.max_gap_s,
            "max_missed_frames": self.max_missed_frames,
            "min_track_length": self.min_track_length,
            "require_same_class": self.require_same_class,
        }


@dataclass
class _OpenTrack:
    """Mutable working state for a track still accepting detections."""

    track_id: str
    damage_class: DamageClass
    detection_ids: list[str] = field(default_factory=list)
    confidences: list[float] = field(default_factory=list)
    last_bbox: BoundingBox | None = None
    first_frame_id: str = ""
    last_frame_id: str = ""
    first_t_epoch_ms: int = 0
    last_t_epoch_ms: int = 0
    missed: int = 0

    def to_track(self, survey_id: str) -> Track:
        return Track(
            track_id=self.track_id,
            survey_id=survey_id,
            damage_class=self.damage_class,
            detection_ids=list(self.detection_ids),
            first_frame_id=self.first_frame_id,
            last_frame_id=self.last_frame_id,
            first_t_epoch_ms=self.first_t_epoch_ms,
            last_t_epoch_ms=self.last_t_epoch_ms,
            max_confidence=max(self.confidences),
            mean_confidence=sum(self.confidences) / len(self.confidences),
        )


class GreedyIouTracker:
    """Associates detections into tracks, one survey at a time.

    Usage: feed frames in ascending time order via :meth:`update`, then call
    :meth:`finish`. Detections are mutated in place to carry their ``track_id``, so the
    link from a defect back to individual frame evidence survives into storage.
    """

    def __init__(self, config: TrackingConfig | None = None) -> None:
        self.config = config or TrackingConfig()
        self._open: list[_OpenTrack] = []
        self._closed: list[_OpenTrack] = []
        self._counter = 0

    def _new_track_id(self, survey_id: str) -> str:
        self._counter += 1
        return f"{survey_id}:trk{self._counter:05d}"

    def update(
        self, survey_id: str, frame_id: str, t_epoch_ms: int, detections: list[Detection]
    ) -> None:
        """Associate one frame's detections against the currently open tracks."""
        # Retire tracks that have gone quiet. Done before matching so a stale track
        # cannot claim a detection belonging to a genuinely new defect.
        still_open: list[_OpenTrack] = []
        for tr in self._open:
            if (t_epoch_ms - tr.last_t_epoch_ms) / 1000.0 > self.config.max_gap_s:
                self._closed.append(tr)
            else:
                still_open.append(tr)
        self._open = still_open

        # Greedy matching: consider every (track, detection) pair, take the best IoU
        # first, and never reuse either side. O(n*m) per frame, which at realistic
        # counts (single-digit detections, single-digit open tracks) is nothing.
        candidates: list[tuple[float, int, int]] = []
        for ti, tr in enumerate(self._open):
            if tr.last_bbox is None:
                continue
            for di, det in enumerate(detections):
                if self.config.require_same_class and det.damage_class is not tr.damage_class:
                    continue
                iou = tr.last_bbox.iou(det.bbox)
                if iou >= self.config.min_iou:
                    candidates.append((iou, ti, di))
        candidates.sort(key=lambda c: c[0], reverse=True)

        used_tracks: set[int] = set()
        used_dets: set[int] = set()
        for iou, ti, di in candidates:
            if ti in used_tracks or di in used_dets:
                continue
            used_tracks.add(ti)
            used_dets.add(di)
            tr, det = self._open[ti], detections[di]
            det.track_id = tr.track_id
            tr.detection_ids.append(det.detection_id)
            tr.confidences.append(det.confidence)
            tr.last_bbox = det.bbox
            tr.last_frame_id = frame_id
            tr.last_t_epoch_ms = t_epoch_ms
            tr.missed = 0

        # Unmatched open tracks age; those that age out are closed.
        survivors: list[_OpenTrack] = []
        for ti, tr in enumerate(self._open):
            if ti in used_tracks:
                survivors.append(tr)
                continue
            tr.missed += 1
            if tr.missed > self.config.max_missed_frames:
                self._closed.append(tr)
            else:
                survivors.append(tr)
        self._open = survivors

        # Unmatched detections start new tracks.
        for di, det in enumerate(detections):
            if di in used_dets:
                continue
            tr = _OpenTrack(
                track_id=self._new_track_id(survey_id),
                damage_class=det.damage_class,
                last_bbox=det.bbox,
                first_frame_id=frame_id,
                last_frame_id=frame_id,
                first_t_epoch_ms=t_epoch_ms,
                last_t_epoch_ms=t_epoch_ms,
            )
            det.track_id = tr.track_id
            tr.detection_ids.append(det.detection_id)
            tr.confidences.append(det.confidence)
            self._open.append(tr)

    def finish(self, survey_id: str) -> list[Track]:
        """Close all tracks and return those meeting the minimum length."""
        self._closed.extend(self._open)
        self._open = []
        tracks = [
            tr.to_track(survey_id)
            for tr in self._closed
            if len(tr.detection_ids) >= self.config.min_track_length
        ]
        tracks.sort(key=lambda t: (t.first_t_epoch_ms, t.track_id))
        return tracks
