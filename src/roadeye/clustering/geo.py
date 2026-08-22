"""Geospatial clustering: tracks -> defects.

The second deduplication layer. Tracking collapses one defect's appearances *within a
continuous view*; clustering collapses what tracking cannot:

* a track that breaks and restarts (occlusion by a car, a bump, a missed frame),
* the same pothole seen again on the **return leg** of a drive,
* the same pothole seen again on a **later survey**, weeks apart.

That third case is the one that turns RoadEye from a defect detector into an asset
management system: it is what makes "first_seen / last_seen / worsening / possibly
repaired" possible at all.

Algorithm: single-link agglomeration with a distance threshold, constrained by damage
class. Chosen over k-means (needs k, which we do not know) and over DBSCAN (needs a
density parameter that is meaningless when a road may have exactly one pothole).

**Every merge preserves its sources.** A defect always carries the observation and
detection ids it was built from, because a municipal work order must be able to show
the original photograph.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from roadeye.domain.enums import DamageClass, DefectStatus, LocationMethod, Severity, SeveritySource
from roadeye.domain.models import Defect, DefectObservation, GeoPoint
from roadeye.geolocation.geodesy import LatLon, haversine_m


@dataclass(frozen=True, slots=True)
class ClusteringConfig:
    """Merge rules. Recorded in the processing run for reproducibility."""

    #: Two observations of the same class within this distance are the same defect.
    #: Chosen to be comparable to consumer GPS error (~10 m) rather than to the
    #: physical size of a pothole: our position uncertainty, not the defect's extent,
    #: is what limits us at MVP.
    merge_radius_m: float = 12.0
    #: Only merge observations of the same damage class.
    require_same_class: bool = True
    #: When True, the merge radius grows with the observations' own uncertainty, so
    #: two vague fixes merge more readily than two confident ones.
    scale_radius_by_uncertainty: bool = True
    #: Upper bound on the uncertainty-scaled radius, so one terrible fix cannot swallow
    #: an entire street.
    max_merge_radius_m: float = 30.0
    #: Hard cap on how far a cluster may extend from its own centroid.
    #:
    #: This exists to defeat **single-link chaining**, and it is not a theoretical
    #: concern: without it, observations spaced 2.5 m apart along a street are each
    #: within the merge radius of their neighbour, so one cluster grows continuously
    #: down the entire road and a 600 m drive yields exactly one "defect". A real row
    #: of potholes would be reported as a single item — silently destroying the
    #: product's core claim that one defect means one repairable thing.
    #:
    #: 15 m is chosen to comfortably exceed consumer-GPS error (~5-15 m) while staying
    #: well under the spacing at which a municipality would consider two potholes to be
    #: separate work items. Must be re-tuned against real Yerevan data at M8.
    max_cluster_extent_m: float = 15.0

    def as_dict(self) -> dict[str, object]:
        return {
            "merge_radius_m": self.merge_radius_m,
            "require_same_class": self.require_same_class,
            "scale_radius_by_uncertainty": self.scale_radius_by_uncertainty,
            "max_merge_radius_m": self.max_merge_radius_m,
            "max_cluster_extent_m": self.max_cluster_extent_m,
        }


@dataclass(frozen=True, slots=True)
class ClusterCandidate:
    """An observation paired with its damage class, ready for clustering.

    The class travels alongside the observation rather than inside it because
    :class:`~roadeye.domain.models.DefectObservation` describes *evidence*, while the
    damage class is a property of the *defect* the evidence supports. Bundling them
    here keeps clustering a pure function instead of reaching for shared state.
    """

    observation: DefectObservation
    damage_class: DamageClass

    @property
    def position(self) -> LatLon:
        return LatLon(self.observation.location.lat, self.observation.location.lon)

    @property
    def uncertainty_m(self) -> float:
        return self.observation.location.uncertainty_m


def _effective_radius(a: ClusterCandidate, b: ClusterCandidate, config: ClusteringConfig) -> float:
    if not config.scale_radius_by_uncertainty:
        return config.merge_radius_m
    # Two fixes each uncertain by u could genuinely be the same point if they are
    # within roughly the sum of their uncertainties.
    scaled = config.merge_radius_m + a.uncertainty_m + b.uncertainty_m
    return min(scaled, config.max_merge_radius_m)


def _weighted_centroid(cluster: list[ClusterCandidate]) -> tuple[float, float, float]:
    """Uncertainty-weighted mean position, and the resulting uncertainty.

    Weighting by 1/uncertainty^2 is the standard inverse-variance combination: a 3 m fix
    should dominate a 25 m one rather than being averaged with it as an equal.

    The combined uncertainty is **floored at the best single observation**. Formally,
    inverse-variance combination of N independent fixes shrinks error by sqrt(N) — but
    our fixes are emphatically *not* independent: consecutive observations of one
    pothole come from the same receiver, in the same multipath environment, using the
    same satellite geometry, seconds apart. Their errors are strongly correlated, so
    averaging them removes far less error than the formula promises.

    Letting the formula run unchecked would manufacture sub-metre confidence out of a
    consumer GPS — 234 observations of a 5 m fix would report 0.33 m. That is precisely
    the false precision this system exists to refuse. The floor means combining
    observations can never claim to beat the single best fix, which is conservative and
    defensible; refining it requires measuring real correlated GPS error at M8, not
    picking a nicer constant.
    """
    weights = [1.0 / (max(c.uncertainty_m, 0.5) ** 2) for c in cluster]
    total = sum(weights)
    lat = sum(c.observation.location.lat * w for c, w in zip(cluster, weights, strict=True)) / total
    lon = sum(c.observation.location.lon * w for c, w in zip(cluster, weights, strict=True)) / total

    combined = math.sqrt(1.0 / total)
    best = min(c.uncertainty_m for c in cluster)
    return lat, lon, max(combined, best)


def cluster_candidates(
    candidates: list[ClusterCandidate],
    config: ClusteringConfig | None = None,
) -> list[list[ClusterCandidate]]:
    """Group candidates into clusters, one per believed physical defect.

    Single-link agglomerative *with an extent cap*: a candidate joins a cluster when it
    is close enough to any member **and** the cluster would not thereby grow beyond
    ``max_cluster_extent_m`` from its own centroid. The second condition is what stops
    a chain of nearby observations from merging an entire street into one defect.

    When several clusters qualify, the nearest wins — so a candidate between two
    genuine defects attaches to the closer one rather than to whichever happened to be
    created first.

    Straightforward O(n^2); for MVP survey sizes (hundreds to low thousands of
    observations) that is microseconds, and the R*Tree index in
    :mod:`roadeye.storage` is the escape hatch if it ever stops being.

    Deterministic by construction — candidates are processed in a stable sorted order,
    so the same input always yields the same clustering. Without that, provenance would
    be unreproducible.
    """
    cfg = config or ClusteringConfig()
    clusters: list[list[ClusterCandidate]] = []

    ordered = sorted(
        candidates,
        key=lambda c: (c.observation.observed_at, c.observation.observation_id),
    )

    for cand in ordered:
        best_cluster: list[ClusterCandidate] | None = None
        best_distance = float("inf")

        for cluster in clusters:
            if cfg.require_same_class and cluster[0].damage_class is not cand.damage_class:
                continue

            nearest = min(haversine_m(m.position, cand.position) for m in cluster)
            if nearest > max(_effective_radius(m, cand, cfg) for m in cluster):
                continue

            # Extent guard: would absorbing this candidate stretch the cluster past its
            # permitted diameter? Measured from the centroid the cluster *would* have.
            lat, lon, _ = _weighted_centroid([*cluster, cand])
            hypothetical = LatLon(lat, lon)
            extent = max(haversine_m(hypothetical, m.position) for m in (*cluster, cand))
            if extent > cfg.max_cluster_extent_m:
                continue

            if nearest < best_distance:
                best_distance = nearest
                best_cluster = cluster

        if best_cluster is None:
            clusters.append([cand])
        else:
            best_cluster.append(cand)

    return clusters


def build_defects(
    candidates: list[ClusterCandidate],
    *,
    config: ClusteringConfig | None = None,
    model_id: str | None = None,
    processing_run_id: str | None = None,
    defect_id_prefix: str = "def",
) -> tuple[list[Defect], list[DefectObservation]]:
    """Cluster candidates and materialise :class:`Defect` records.

    Returns the defects and the observations with their ``defect_id`` back-filled, so
    the caller can persist both halves of the link.

    Confidence aggregation uses the **maximum**, not the mean. Rationale: seeing a
    pothole clearly once and ambiguously five times is evidence *for* the pothole. A
    mean would punish exactly the approach-and-pass geometry every survey produces, in
    which early distant frames are legitimately low-confidence.
    """
    cfg = config or ClusteringConfig()
    clusters = cluster_candidates(candidates, cfg)

    defects: list[Defect] = []
    linked: list[DefectObservation] = []

    ordered_clusters = sorted(clusters, key=lambda c: min(x.observation.observed_at for x in c))

    for i, cluster in enumerate(ordered_clusters, 1):
        defect_id = f"{defect_id_prefix}_{i:05d}"
        lat, lon, uncertainty = _weighted_centroid(cluster)

        # Representative evidence: the highest-confidence observation. That is the frame
        # a human reviewer should be shown first, because it is the one most likely to
        # let them decide in under a second.
        best = max(cluster, key=lambda c: c.observation.confidence)

        survey_ids: list[str] = []
        for c in cluster:
            if c.observation.survey_id not in survey_ids:
                survey_ids.append(c.observation.survey_id)

        defects.append(
            Defect(
                defect_id=defect_id,
                damage_class=cluster[0].damage_class,
                location=GeoPoint(
                    lat=lat,
                    lon=lon,
                    method=LocationMethod.INTERPOLATED_PHONE_GPS,
                    uncertainty_m=round(uncertainty, 3),
                ),
                confidence=max(c.observation.confidence for c in cluster),
                severity=Severity.UNASSESSED,
                severity_source=SeveritySource.OTHER,
                status=DefectStatus.PROBABLE,
                observation_count=len(cluster),
                survey_ids=survey_ids,
                first_seen=min(c.observation.observed_at for c in cluster),
                last_seen=max(c.observation.observed_at for c in cluster),
                representative_frame_id=best.observation.representative_frame_id,
                representative_image_path=None,
                model_id=model_id,
                processing_run_id=processing_run_id,
            )
        )

        for c in cluster:
            linked.append(c.observation.model_copy(update={"defect_id": defect_id}))

    return defects, linked
