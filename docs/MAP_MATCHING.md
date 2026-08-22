# RoadEye — Map Matching (M5)

**Status:** implemented, verified against synthetic geometry. Never run on a real
Armenian survey, because there isn't one yet.

A municipality does not dispatch a crew to `40.187214, 44.515236`. It dispatches them to
Mashtots Avenue. Turning a coordinate into a street name is most of what makes a defect
list actionable — and it is an excellent way to be confidently wrong, because the
coordinate came from a consumer GPS with metres of error and urban roads are metres
apart.

## Using it

```bash
# 1. Get road geometry for the survey area, once. It is cached and reused.
roadeye roads --bbox 40.150,44.450,40.230,44.560 --output data/roads/yerevan.json.gz

#    No Overpass access? Export an .osm file from JOSM or the OSM website instead:
roadeye roads --osm-file yerevan.osm --output data/roads/yerevan.json.gz

# 2. See what would change before changing it.
roadeye match-roads --db yerevan.db --roads data/roads/yerevan.json.gz --dry-run

# 3. Do it.
roadeye match-roads --db yerevan.db --roads data/roads/yerevan.json.gz
```

The bbox is `min_lat,min_lon,max_lat,max_lon`. Overpass wants south-west-north-east and
GeoJSON wants `[lon, lat]`; the `BBox` type exists so nobody has to remember which is
which at the call site.

To try the whole chain with no network and no real data:

```bash
python3 scripts/make_demo_survey.py demo_output/survey
roadeye process demo_output/survey --db demo_output/demo.db
python3 scripts/make_demo_roads.py --output demo_output/roads.json
roadeye match-roads --db demo_output/demo.db --roads demo_output/roads.json
```

Those street names are invented. They are not the real streets of Kentron.

## The four rules

Each is pinned by a test, and each was checked by breaking the code and confirming the
test fails.

### Matching never shrinks uncertainty

Snapping a point onto a centreline *looks* like it should sharpen the estimate. It does
not. The along-road position is still only as good as the GPS fix, and we do not know
which lane, which side, or which kerb. Worse: if the snap moved the point 12 m and we
carried on claiming ±8 m, the true position could now lie outside our own stated circle.

So the reported uncertainty is `max(original, snap_distance)` — never less than it was,
and always wide enough to contain the move. This is rule 7 in `CLAUDE.md` applied to a
place where the temptation to claim an improvement is strong.

### Heading decides between streets, not just distance

At a crossroads the nearest centreline is often the one the vehicle never drove. The
camera was pointing along the road it was on, so a segment whose bearing disagrees with
the vehicle heading is rejected however close it is.

A two-way street is driven in both directions, so a 175° disagreement folds to 5°. A
one-way street does not get that courtesy: driving it backwards is not something the
survey did, so a reversed heading there is real evidence the match is wrong.

`oneway=-1` means one-way *against* the way's drawn direction. Stored unreversed, its
bearing would be 180° from the direction traffic actually travels, and the heading check
would reject every correct match. The geometry is reversed on import instead.

### Ambiguity is refused, not guessed

When the two best candidates are **different streets** at nearly the same score, no match
is recorded. A defect that keeps its interpolated coordinate is a small nuisance. A defect
labelled with the wrong street sends a crew to the wrong place, and nothing downstream can
tell that it happened.

"Different streets" is judged by name, not by segment. A way is stored as many short
pieces, so being equidistant from two consecutive segments of one avenue is the normal
case and either is correct.

### A match changes position and road reference. Nothing else

Not the class, not the status, not the confidence, not the review state. Map matching is
a statement about where a defect is, never about whether it exists. A test dumps the
whole model before and after and asserts the changed set is a subset of
`{location, road, updated_at}`.

A position a human corrected is never touched at all: `MANUAL_CORRECTION` outranks
anything a machine derives.

## What gets recorded

```
RoadSegmentRef
  source              "osm"
  segment_id          "way/24601#3"
  name                "Mashtots Avenue"      (may be null — service roads often are)
  match_distance_m    4.2
  heading_delta_deg   7.5
```

The last two are the point. *"On Mashtots Avenue"* and *"19 m from Mashtots Avenue, and
nothing else was nearby"* are very different claims, and only one of them belongs on a
work order. Storing only the name would make every match look equally good.

Both columns are new in storage schema version 2, with an additive migration: an existing
pilot database gains them on next open rather than crashing.

`match-roads` prints counts by outcome — `matched`, `ambiguous`, `heading_mismatch`,
`no_candidates`, `skipped_manual` — because a silent map-matching pass is one nobody can
audit. `heading_mismatch` is deliberately distinct from `no_candidates`: "no road near
this defect" and "the only road near it runs the wrong way" send you to different places.

## Thresholds

All in `MatchingConfig`, all recorded in `ProcessingRun.config`.

| Setting | Default | Why |
|---|---:|---|
| `max_distance_m` | 20.0 | Further than this is a car park or a courtyard, not that road |
| `uncertainty_multiplier` | 2.0 | A well-located defect should not be matched against half the neighbourhood |
| `min_search_radius_m` | 15.0 | For fixes whose stated uncertainty is optimistic |
| `max_heading_delta_deg` | 55.0 | Generous: GPS heading is noisy at low speed, and a curve's chord bearing differs from the heading at any point on it |
| `heading_penalty_m_per_deg` | 0.15 | 40° of disagreement costs 6 m — enough to lose to a slightly more distant segment pointing the right way |
| `ambiguity_margin_m` | 4.0 | Below this gap between two streets, refuse |

**None of these are tuned.** They are defensible starting values chosen from GPS error
magnitudes and urban road spacing. Tuning them requires a real survey with known ground
truth, which is M8. Until then, treat them as assumptions, not results.

## Where the geometry comes from

| Source | Status | Notes |
|---|---|---|
| **Overpass API** | Primary | Built for this query. Free, donated capacity — fetch a city once and reuse the file |
| **OSM XML** (`.osm`) | Supported | What JOSM and the OSM website export. Works with no Overpass access |
| **`.osm.pbf`** | **Not supported** | Needs a protobuf decoder. Every dependency here must be licence-audited and justified, and Overpass or XML covers a city without one |

Some networks block public Overpass instances outright — including the one this was
developed on, where the connection is reset. `fetch_overpass` says so in the error rather
than leaving you with "connection reset by peer", which sends people looking in the wrong
place.

Only drivable highway values are imported. A defect seen from a car must not be matched
onto a footpath two metres away, so `footway`, `cycleway`, `path` and `steps` are excluded.

The network is a bag of short segments with a uniform-grid index, **not** a routing graph.
Assigning a defect to a street needs proximity and direction, not connectivity, and a
graph would be a much larger thing to build, store and keep correct.

## Licensing — read this before caching anything

OpenStreetMap is **ODbL**, which carries share-alike obligations *on data*.

**No OSM data is committed to this repository.** Extracts are fetched at run time into
`data/roads/`, which is git-ignored, and the test suite uses invented geometry. A cached
extract living in a proprietary tree is exactly the ambiguity L-3 in
`docs/LICENSE_AUDIT.md` is trying not to create.

**Attribution is derived from the data, not remembered by a caller.** A defect matched
against OSM carries `road.source == "osm"`, and the exporter turns that into the notice:

* GeoJSON gets a top-level `attribution` member.
* CSV has nowhere to put one — no header, no metadata block — so a
  `<name>.csv.ATTRIBUTION.txt` sidecar is written beside it. A spreadsheet emailed to a
  municipality travels alone otherwise.

Both directions are tested. An export that never touched OSM must **not** claim to have
used it: an attribution added unconditionally is noise, and a field that is always
present is one readers learn to skip — which is exactly when it stops working as
compliance. A stale sidecar is deleted on re-export for the same reason.

`RoadSegmentRef` stays a separable reference rather than denormalised columns, preserving
the option to detach OSM-derived identifiers from a proprietary defect database if L-3 is
ever resolved the unfavourable way.

## A bug this work surfaced

`point_to_segment_distance_m` computed its along-track distance with
`acos(cos(d13) / cos(xtd))`. `acos` cannot return a negative, so a point *behind* a
segment's start was indistinguishable from one the same distance ahead.

A point 852 m behind an 852 m segment reported as **0.10 m** from it. Collinear with the
road, so the perpendicular distance is ~0; and the endpoint clamp never fired, because the
unsigned along-track was within the segment length. Further back still, it clamped to the
*far* endpoint and reported the distance to the wrong end.

Nothing shipped was using it — only tests — so no result was ever wrong. But it is the
exact primitive map matching sits on, and it would have snapped defects onto roads they
were hundreds of metres from, with a distance small enough to look like a good match.

The sign is recovered from `cos(theta13 - theta12)`. Both cases are now regression tests,
and the existing "clamps beyond the endpoint" test is why only one end was ever checked.

## Not built

- **Route-aware matching.** Matching each defect independently ignores that they came
  from one continuous drive. A hidden Markov model over the GPS trace (the Newson–Krumm
  approach) uses that and is markedly better at ambiguous junctions. It is also much more
  machinery, and worth building only once there is real data showing per-point matching
  is the limiting factor.
- **Address interpolation.** "Mashtots Avenue near number 23" needs OSM address nodes and
  a defensible nearest-address rule.
- **Segment-level aggregation.** "This 200 m of Abovyan has 14 defects" is what a road
  maintenance budget is actually built from. It needs map matching first, which is this,
  and is the natural next step.
