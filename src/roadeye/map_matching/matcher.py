"""Assign a defect to the street it is on.

A municipality does not dispatch a crew to 40.187214, 44.515236. It dispatches them to
Mashtots Avenue. Turning a coordinate into a street name is most of what makes a defect
list actionable, and it is also an excellent way to be confidently wrong: the coordinate
came from a consumer GPS with metres of error, and urban roads are metres apart.

Four rules keep that from happening, each pinned by a test.

**Matching never shrinks uncertainty.** Snapping a point onto a centreline looks like it
should improve the estimate. It does not: the along-road position is still only as good
as the GPS fix, and we do not know which lane, which side, or which kerb. Worse, if the
snap *moved* the point 12 m and we kept claiming +/-8 m, the true position could now lie
outside our own stated circle. So the reported uncertainty is
``max(original, snap_distance)`` — never less than it was, and always large enough to
cover the move.

**Heading decides between streets, not just distance.** At a crossroads the nearest
segment is often the one the vehicle never drove. The camera was pointing along the road
it was on, so a segment whose bearing disagrees with the vehicle heading is rejected
however close it is.

**Ambiguity is refused, not guessed.** When the two best candidates are different streets
at nearly the same distance, no match is recorded. A defect that keeps its interpolated
coordinate is a small nuisance; a defect labelled with the wrong street sends a crew to
the wrong place, and nothing downstream can tell that it happened.

**A match changes the position and the road reference. Nothing else.** Not the class, not
the status, not the confidence. Map matching is not evidence about whether a defect
exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from roadeye.domain.enums import LocationMethod
from roadeye.domain.models import Defect, GeoPoint, RoadSegmentRef
from roadeye.geolocation.geodesy import (
    LatLon,
    bearing_difference_deg,
    project_onto_segment,
)
from roadeye.map_matching.network import RoadNetwork, RoadSegment


@dataclass(frozen=True)
class MatchingConfig:
    """Every threshold that decides a match. Recorded in ``ProcessingRun.config``."""

    #: Hard cap on how far a defect may be from a road and still be assigned to it. A
    #: pothole 30 m from the nearest centreline is not on that road; it is in a car park,
    #: a courtyard, or the survey drifted.
    max_distance_m: float = 20.0

    #: Search radius as a multiple of the defect's own uncertainty, so a well-located
    #: defect is not matched against half the neighbourhood. Bounded by
    #: ``max_distance_m`` either way.
    uncertainty_multiplier: float = 2.0

    #: Minimum radius to search, for defects whose stated uncertainty is optimistically
    #: small.
    min_search_radius_m: float = 15.0

    #: Reject a segment whose bearing disagrees with the vehicle heading by more than
    #: this. Generous, because GPS-derived heading is noisy at low speed and a curved
    #: road's chord bearing differs from the heading at any single point.
    max_heading_delta_deg: float = 55.0

    #: Metres of penalty per degree of heading disagreement when ranking candidates.
    #: At 0.15, a 40-degree disagreement costs 6 m — enough to lose to a slightly more
    #: distant segment pointing the right way.
    heading_penalty_m_per_deg: float = 0.15

    #: If the best and second-best candidates are *different streets* and their scores
    #: are within this many metres, refuse the match.
    ambiguity_margin_m: float = 4.0

    #: Match only to segments that carry a name. Off by default: an unnamed service road
    #: is still the right answer, and ``segment_id`` remains useful without a name.
    require_named: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "max_distance_m": self.max_distance_m,
            "uncertainty_multiplier": self.uncertainty_multiplier,
            "min_search_radius_m": self.min_search_radius_m,
            "max_heading_delta_deg": self.max_heading_delta_deg,
            "heading_penalty_m_per_deg": self.heading_penalty_m_per_deg,
            "ambiguity_margin_m": self.ambiguity_margin_m,
            "require_named": self.require_named,
        }


@dataclass(frozen=True)
class Candidate:
    segment: RoadSegment
    snapped: LatLon
    distance_m: float
    heading_delta_deg: float | None
    score: float


@dataclass(frozen=True)
class MatchResult:
    """The outcome of matching one point, including why it failed."""

    location: GeoPoint
    road: RoadSegmentRef | None = None
    matched: bool = False
    #: Machine-readable reason when ``matched`` is False. One of ``no_candidates``,
    #: ``too_far``, ``heading_mismatch``, ``ambiguous``, ``no_heading_required``.
    reason: str | None = None
    #: The runners-up, for debugging a surprising result.
    considered: tuple[Candidate, ...] = field(default=())


class MatchStats(dict[str, int]):
    """Counts by outcome. A silent map-matching pass is one nobody can audit."""

    def bump(self, key: str) -> None:
        self[key] = self.get(key, 0) + 1


def match_point(
    network: RoadNetwork,
    location: GeoPoint,
    *,
    heading_deg: float | None = None,
    config: MatchingConfig | None = None,
) -> MatchResult:
    """Snap one coordinate onto the road network, or explain why it was not snapped."""
    cfg = config or MatchingConfig()
    point = LatLon(location.lat, location.lon)

    radius = min(
        cfg.max_distance_m,
        max(cfg.min_search_radius_m, location.uncertainty_m * cfg.uncertainty_multiplier),
    )

    candidates: list[Candidate] = []
    rejected_on_heading = False
    for segment in network.nearby(point, radius):
        if cfg.require_named and not segment.name:
            continue
        projection = project_onto_segment(point, segment.start, segment.end)
        if projection.distance_m > cfg.max_distance_m:
            continue

        delta = _heading_delta(segment, heading_deg)
        if delta is not None and delta > cfg.max_heading_delta_deg:
            # Remembered rather than merely skipped: "no roads near this defect" and
            # "the only road near it runs the wrong way" are different problems, and a
            # stats line that conflates them sends the reader to the wrong place.
            rejected_on_heading = True
            continue

        penalty = 0.0 if delta is None else delta * cfg.heading_penalty_m_per_deg
        candidates.append(
            Candidate(
                segment=segment,
                snapped=projection.point,
                distance_m=projection.distance_m,
                heading_delta_deg=delta,
                score=projection.distance_m + penalty,
            )
        )

    if not candidates:
        return MatchResult(
            location=location,
            reason="heading_mismatch" if rejected_on_heading else "no_candidates",
        )

    candidates.sort(key=lambda c: c.score)
    best = candidates[0]

    # Ambiguity is judged between *streets*, not segments. Consecutive segments of one
    # way are near-equal by construction and choosing either is correct; two different
    # streets at the same distance is a coin flip we refuse to make.
    rival = next(
        (c for c in candidates[1:] if _street_key(c.segment) != _street_key(best.segment)),
        None,
    )
    if rival is not None and rival.score - best.score < cfg.ambiguity_margin_m:
        return MatchResult(
            location=location,
            reason="ambiguous",
            considered=(best, rival),
        )

    return MatchResult(
        location=GeoPoint(
            lat=best.snapped.lat,
            lon=best.snapped.lon,
            method=LocationMethod.ROAD_SEGMENT_MATCHED,
            # Never below the original, and always wide enough to contain the move. A
            # snap that shrank the stated error would be claiming the road network told
            # us something about along-road position, which it did not.
            uncertainty_m=round(max(location.uncertainty_m, best.distance_m), 3),
            altitude_m=location.altitude_m,
        ),
        road=RoadSegmentRef(
            source=network.provenance.source,
            segment_id=best.segment.segment_id,
            name=best.segment.name,
            match_distance_m=round(best.distance_m, 3),
            heading_delta_deg=(
                None if best.heading_delta_deg is None else round(best.heading_delta_deg, 2)
            ),
        ),
        matched=True,
        considered=tuple(candidates[:3]),
    )


def match_defects(
    defects: list[Defect],
    network: RoadNetwork,
    *,
    headings: dict[str, float] | None = None,
    config: MatchingConfig | None = None,
) -> tuple[list[Defect], MatchStats]:
    """Match a list of defects, returning updated copies and the outcome counts.

    ``headings`` maps ``defect_id`` to the vehicle heading in degrees, normally taken
    from the defect's representative frame. Defects absent from it are matched on
    distance alone.

    A defect whose position a human has corrected is never touched: ``MANUAL_CORRECTION``
    outranks anything a machine can derive (``docs/GEOLOCATION.md``).
    """
    cfg = config or MatchingConfig()
    headings = headings or {}
    stats = MatchStats()
    out: list[Defect] = []

    for defect in defects:
        if defect.location.method is LocationMethod.MANUAL_CORRECTION:
            stats.bump("skipped_manual")
            out.append(defect)
            continue

        result = match_point(
            network,
            defect.location,
            heading_deg=headings.get(defect.defect_id),
            config=cfg,
        )
        if not result.matched:
            stats.bump(result.reason or "unmatched")
            out.append(defect)
            continue

        stats.bump("matched")
        if result.road is not None and result.road.name:
            stats.bump("matched_named")
        # model_copy keeps every other field byte-identical: matching is a statement
        # about where a defect is, never about whether it exists.
        out.append(defect.model_copy(update={"location": result.location, "road": result.road}))

    return out, stats


def _heading_delta(segment: RoadSegment, heading_deg: float | None) -> float | None:
    """Angle between the vehicle heading and the segment, in ``[0, 180]``.

    A two-way street is driven in both directions, so a 175-degree disagreement is
    perfect agreement with the reverse direction and folds to 5. A one-way street does
    not get that courtesy — driving it backwards is not a thing the survey did, so a
    reversed heading there is real evidence the match is wrong.
    """
    if heading_deg is None:
        return None
    delta = bearing_difference_deg(heading_deg, segment.bearing_deg)
    if segment.oneway:
        return delta
    return min(delta, 180.0 - delta)


def _street_key(segment: RoadSegment) -> str:
    """What counts as "the same street" for the ambiguity check.

    Named streets are compared by name, so the many segments of one avenue are one
    street. Unnamed geometry falls back to the way id: two unnamed ways really are
    different candidates, and treating them all as one street would defeat the check.
    """
    return f"name:{segment.name}" if segment.name else f"way:{segment.way_id}"
