"""Defects rolled up to the stretch of road they are on.

A city does not fix one pothole. It resurfaces a length of street, and it budgets by
that length. A list of individual defects is the raw material for that decision; this is
the decision's shape.

## The distinction this module exists to protect

**Coverage is computed from frames, not from defects.**

"No defects found on Teryan Street" and "we have never driven Teryan Street" are opposite
claims, and a rollup built only from defects cannot tell them apart — both produce an
absent row. A municipality reading the first as the second (or vice versa) is being
misled by the report's *structure*, which is worse than a wrong number, because there is
nothing on the page to disagree with.

So surveyed length comes from where the camera actually went. A street then falls into
one of three states, and the report always says which:

| Surveyed | Defects | Means |
|---|---|---|
| > 0 | > 0 | Found this many, over this distance |
| > 0 | 0 | **Driven and clean** |
| 0 | — | **Never driven.** Not a claim about the road at all |

## Two more rules carried through from the rest of the system

**Probable and verified stay separate**, exactly as they do everywhere else. Density is
computed over probable + verified and excludes rejected, because a human saying "that was
a shadow" is information and throwing it back into the total would waste it.

**Density is `None` when nothing was surveyed**, never zero and never a large number from
a tiny denominator. A street with two defects and 8 m of coverage does not have 25
defects per 100 m; it has an unusable sample, and the report says so rather than
computing something.

**Coverage counts the network inspected, not the distance driven.** They are not the same
number: driving one street four times, or 3 km down a motorway outside the extract, is
real distance that inspects no more of this network. Both are reported — as
``surveyed_m`` and ``unmatched_m`` — and neither may raise ``coverage_fraction``.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from roadeye.domain.enums import DefectStatus
from roadeye.domain.models import Defect, Frame
from roadeye.geolocation.geodesy import LatLon, haversine_m
from roadeye.map_matching.matcher import MatchingConfig, match_point
from roadeye.map_matching.network import RoadNetwork

#: Frames further apart than this are treated as separate stretches rather than a driven
#: line between them. At the default 5 m sampling a 60 m gap is a break in the survey —
#: a tunnel, a signal loss, or the driver stopping the app — and drawing coverage across
#: it would claim to have inspected road nobody looked at.
MAX_FRAME_GAP_M = 60.0

#: Below this surveyed length, density is reported as ``None``. Two defects in 8 m is not
#: 25 per 100 m; it is too small a sample to divide by.
MIN_LENGTH_FOR_DENSITY_M = 50.0


@dataclass
class StreetRollup:
    """One stretch of road: what was driven, and what was found on it."""

    way_id: str
    name: str | None = None
    highway: str | None = None

    #: Metres of this way the camera actually travelled. From frames, not defects. Two
    #: surveys of the same 400 m street give 800 m, because that is what was driven and
    #: it is the honest denominator for a defect density.
    surveyed_m: float = 0.0

    #: How long this way is in the road network. Carried so a row can say "400 m of
    #: 1.2 km", and so coverage can be computed without asking the network again.
    length_m: float = 0.0

    verified: int = 0
    probable: int = 0
    rejected: int = 0
    by_class: dict[str, int] = field(default_factory=dict)
    by_severity: dict[str, int] = field(default_factory=dict)
    survey_ids: list[str] = field(default_factory=list)

    @property
    def outstanding(self) -> int:
        """Defects a crew might be sent to: verified plus not-yet-checked.

        Excludes rejected — a human has said those are not there, and counting them
        again would discard the only judgement in the system that is not a guess.
        """
        return self.verified + self.probable

    @property
    def surveyed(self) -> bool:
        return self.surveyed_m > 0.0

    @property
    def covered_m(self) -> float:
        """Metres of *this way* inspected — driven length capped at the way's length.

        Distinct from :attr:`surveyed_m` on purpose. Driving a 400 m street twice is
        800 m of driving and 400 m of street; using the first figure as coverage would
        report 200% of a street inspected, and a report that can exceed 100% is a report
        a municipality is right to stop believing.
        """
        if self.length_m <= 0.0:
            return 0.0
        return min(self.surveyed_m, self.length_m)

    @property
    def defects_per_100m(self) -> float | None:
        """``None`` rather than a number built on too short a sample."""
        if self.surveyed_m < MIN_LENGTH_FOR_DENSITY_M:
            return None
        return round(self.outstanding / self.surveyed_m * 100.0, 2)

    @property
    def state(self) -> str:
        """``not_surveyed`` | ``clean`` | ``defects`` — the row's meaning in one word."""
        if not self.surveyed:
            return "not_surveyed"
        return "defects" if self.outstanding else "clean"

    def to_json(self) -> dict[str, Any]:
        return {
            "way_id": self.way_id,
            "name": self.name,
            "highway": self.highway,
            "state": self.state,
            "surveyed_m": round(self.surveyed_m, 1),
            "length_m": round(self.length_m, 1),
            "covered_m": round(self.covered_m, 1),
            "verified": self.verified,
            "probable": self.probable,
            "rejected": self.rejected,
            "outstanding": self.outstanding,
            "defects_per_100m": self.defects_per_100m,
            "by_class": dict(sorted(self.by_class.items())),
            "by_severity": dict(sorted(self.by_severity.items())),
            "survey_ids": self.survey_ids,
        }


@dataclass
class StreetReport:
    """Every stretch, plus what could not be attributed to one."""

    streets: list[StreetRollup] = field(default_factory=list)

    #: Defects that matched no way — off the network, or refused as ambiguous. Reported
    #: rather than dropped: a rollup silently missing a tenth of the defects is a rollup
    #: nobody should budget from.
    unmatched_defects: int = 0
    #: Metres driven that matched no way, for the same reason.
    unmatched_m: float = 0.0

    #: How many ways the road network holds, and how long they are in total. Carried
    #: even when unsurveyed ways get no row of their own, because the most useful honest
    #: sentence this report can produce is "we have driven 2.4 km of the 340 km in this
    #: extract" — and that is unsayable without the denominator.
    network_ways: int = 0
    network_length_m: float = 0.0

    @property
    def surveyed_m(self) -> float:
        """Everything the camera drove, on the network or off it."""
        return sum(s.surveyed_m for s in self.streets) + self.unmatched_m

    @property
    def surveyed_streets(self) -> list[StreetRollup]:
        return [s for s in self.streets if s.surveyed]

    @property
    def on_network_m(self) -> float:
        """Metres of the network inspected — the only honest coverage numerator.

        Neither driving off the network nor driving one street twice inspects any more
        of the network, so neither may raise this.
        """
        return sum(s.covered_m for s in self.streets)

    @property
    def coverage_fraction(self) -> float | None:
        """Network inspected over network length, or ``None`` when there is no network.

        Deliberately **not** total driven distance over network length. A drive that
        looped one street four times and spent 3 km on a motorway outside the extract
        would report near-total coverage of a city it barely touched — the same
        structural lie this module exists to prevent, wearing a percentage sign.
        """
        if self.network_length_m <= 0:
            return None
        return min(1.0, self.on_network_m / self.network_length_m)

    def worst(self, limit: int = 10) -> list[StreetRollup]:
        """Stretches with the highest defect density, densest first.

        Only stretches with enough coverage to have a density at all. Ranking a 10 m
        sample against a 2 km one would put noise at the top of a work plan.
        """
        rated = [s for s in self.streets if s.defects_per_100m is not None]
        return sorted(rated, key=lambda s: (-(s.defects_per_100m or 0), s.way_id))[:limit]

    def by_name(self) -> dict[str, StreetRollup]:
        """Ways grouped under their street name.

        Convenience for display only. Street names repeat within a city and OSM splits
        one street into many ways, so the way id remains the identifier — this is what a
        person calls the thing, not what the system keys on.
        """
        merged: dict[str, StreetRollup] = {}
        for street in self.streets:
            if not street.name:
                continue
            target = merged.get(street.name)
            if target is None:
                target = StreetRollup(way_id=f"name:{street.name}", name=street.name)
                merged[street.name] = target
            target.surveyed_m += street.surveyed_m
            target.length_m += street.length_m
            target.verified += street.verified
            target.probable += street.probable
            target.rejected += street.rejected
            for key, value in street.by_class.items():
                target.by_class[key] = target.by_class.get(key, 0) + value
            for key, value in street.by_severity.items():
                target.by_severity[key] = target.by_severity.get(key, 0) + value
            for survey_id in street.survey_ids:
                if survey_id not in target.survey_ids:
                    target.survey_ids.append(survey_id)
        return merged

    def to_json(self) -> dict[str, Any]:
        return {
            "streets": [s.to_json() for s in self.streets],
            "surveyed_m": round(self.surveyed_m, 1),
            "on_network_m": round(self.on_network_m, 1),
            "surveyed_ways": len(self.surveyed_streets),
            "network_ways": self.network_ways,
            "network_length_m": round(self.network_length_m, 1),
            "coverage_fraction": (
                None if self.coverage_fraction is None else round(self.coverage_fraction, 4)
            ),
            "unmatched_defects": self.unmatched_defects,
            "unmatched_m": round(self.unmatched_m, 1),
            "notice": (
                "Surveyed length is measured from the camera's own track, not from "
                "defects. A street with no defects and no surveyed length was never "
                "driven; it is not a statement that the road is sound."
            ),
        }

    def summary(self) -> str:
        surveyed = self.surveyed_streets
        clean = [s for s in surveyed if s.state == "clean"]
        lines = [
            f"{len(surveyed)} stretch(es) surveyed, {self.surveyed_m / 1000:.2f} km driven",
            f"{len(clean)} driven and clean, {len(surveyed) - len(clean)} with defects",
        ]
        # The denominator, always. Without it a report of four busy streets reads as a
        # survey of the city, when it may be four streets out of eleven hundred.
        if self.network_ways:
            fraction = self.coverage_fraction or 0.0
            lines.append(
                f"that covers {fraction:.1%} of the {self.network_length_m / 1000:.1f} km "
                f"({self.network_ways} stretches) in this road network — "
                f"{self.network_ways - len(surveyed)} never driven"
            )
        if self.unmatched_defects:
            lines.append(f"{self.unmatched_defects} defect(s) matched no street")
        if self.unmatched_m > 0:
            lines.append(f"{self.unmatched_m / 1000:.2f} km driven off the road network")
        return "\n".join(lines)


def build_street_report(
    defects: list[Defect],
    frames: list[Frame],
    network: RoadNetwork,
    *,
    config: MatchingConfig | None = None,
    include_unsurveyed: bool = False,
) -> StreetReport:
    """Roll defects up to the stretch of road they sit on.

    ``defects`` need not be map-matched beforehand: those that already carry a
    ``RoadSegmentRef`` are used as-is, and the rest are matched here. That way a report
    can be produced from a database that has never had ``roadeye match-roads`` run on it.

    ``include_unsurveyed`` adds a row for every way in the network, including the ones
    nobody has driven. Off by default only because a city extract holds tens of thousands
    of ways and the answer is usually "show me what we surveyed" — the *counts* are on
    the report either way, so the fraction of the network covered is never unavailable.
    """
    cfg = config or MatchingConfig()
    by_way: dict[str, StreetRollup] = {}
    way_meta = {s.way_id: (s.name, s.highway) for s in network.segments}

    way_lengths: dict[str, float] = defaultdict(float)
    for segment in network.segments:
        way_lengths[segment.way_id] += segment.length_m

    def rollup_for(way_id: str) -> StreetRollup:
        street = by_way.get(way_id)
        if street is None:
            name, highway = way_meta.get(way_id, (None, None))
            street = StreetRollup(
                way_id=way_id,
                name=name,
                highway=highway,
                length_m=way_lengths.get(way_id, 0.0),
            )
            by_way[way_id] = street
        return street

    report = StreetReport(
        network_ways=len(way_lengths),
        network_length_m=sum(way_lengths.values()),
    )

    if include_unsurveyed:
        for way_id in way_lengths:
            rollup_for(way_id)

    # Coverage first, so a street with frames and no defects still gets a row. Doing it
    # the other way round would only ever create rows for streets that had defects —
    # which is precisely the "clean vs never driven" collapse this module exists to stop.
    coverage, unmatched_m = _coverage_by_way(frames, network, cfg)
    report.unmatched_m = unmatched_m
    for way_id, metres in coverage.items():
        rollup_for(way_id).surveyed_m = metres

    for defect in defects:
        matched_way = _way_for_defect(defect, network, cfg)
        if matched_way is None:
            report.unmatched_defects += 1
            continue

        street = rollup_for(matched_way)
        if defect.status is DefectStatus.VERIFIED:
            street.verified += 1
        elif defect.status is DefectStatus.REJECTED:
            street.rejected += 1
        else:
            street.probable += 1

        if defect.status is not DefectStatus.REJECTED:
            key = defect.damage_class.value
            street.by_class[key] = street.by_class.get(key, 0) + 1
            severity = defect.severity.value
            street.by_severity[severity] = street.by_severity.get(severity, 0) + 1

        for survey_id in defect.survey_ids:
            if survey_id not in street.survey_ids:
                street.survey_ids.append(survey_id)

    report.streets = sorted(by_way.values(), key=lambda s: (s.name or "~", s.way_id))
    return report


def _way_for_defect(defect: Defect, network: RoadNetwork, config: MatchingConfig) -> str | None:
    """The way a defect belongs to, using an existing match when there is one."""
    if defect.road is not None and defect.road.segment_id:
        # Segment ids are "way/123#4"; the rollup is per way, not per vertex pair.
        return defect.road.segment_id.split("#", 1)[0]

    result = match_point(network, defect.location, config=config)
    if not result.matched or result.road is None:
        return None
    return result.road.segment_id.split("#", 1)[0]


def _coverage_by_way(
    frames: list[Frame], network: RoadNetwork, config: MatchingConfig
) -> tuple[dict[str, float], float]:
    """Metres driven per way, and metres that matched no way.

    Distance is attributed pairwise: the gap between two consecutive frames of one survey
    is credited to the way the *earlier* frame matched. Frames further apart than
    :data:`MAX_FRAME_GAP_M` are treated as a break — bridging them would claim to have
    inspected road nobody drove past.
    """
    coverage: dict[str, float] = defaultdict(float)
    unmatched = 0.0

    by_survey: dict[str, list[Frame]] = defaultdict(list)
    for frame in frames:
        if frame.observation_location is not None:
            by_survey[frame.survey_id].append(frame)

    for survey_frames in by_survey.values():
        ordered = sorted(survey_frames, key=lambda f: (f.t_epoch_ms, f.video_time_s))
        for current, following in zip(ordered, ordered[1:], strict=False):
            here = current.observation_location
            there = following.observation_location
            if here is None or there is None:
                continue

            distance = haversine_m(LatLon(here.lat, here.lon), LatLon(there.lat, there.lon))
            if distance <= 0 or distance > MAX_FRAME_GAP_M:
                continue

            result = match_point(network, here, heading_deg=current.heading_deg, config=config)
            if result.matched and result.road is not None:
                coverage[result.road.segment_id.split("#", 1)[0]] += distance
            else:
                unmatched += distance

    return dict(coverage), unmatched
