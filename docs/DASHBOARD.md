# RoadEye — The Municipal Dashboard (M6)

**Status:** implemented. Never shown to an actual municipal employee, and never run on
real Armenian data, because there isn't any yet.

This is the screen a city employee opens. Everything else in RoadEye exists to put
something truthful on it.

```bash
roadeye dashboard --db yerevan.db --roads data/roads/yerevan.json.gz
#  → http://127.0.0.1:8010/dashboard
```

## Armenian first

The interface is **Armenian by default**, with an English toggle. Not the other way
round: the people who will use this work for Yerevan's municipality, and a tool that
greets them in English is a tool that says it was built for somebody else.

Every user-facing string lives in one `STRINGS` table in `dashboard.js`, so a
translation error is a one-line fix rather than a hunt through markup. The choice is
remembered per browser.

The lookup falls back to English when a key is missing, which is right on screen — an
English word beats a raw `coverageOf` — and dangerous in a repository, because it is
silent. Add a key to `en`, forget `hy`, and the Armenian interface starts serving English
to the one reader it exists for, with nothing on the page to say so. So the two tables are
checked against each other by `tests/unit/test_dashboard_strings.py`: no browser, no npm,
just the two sets of keys.

This reaches into the API too. Provenance warnings are returned as **codes with
parameters**, never as prose:

```json
{"code": "synthetic_detector", "models": "fake-detector-v1"}
```

An English sentence built on the server would arrive untranslatable — and the honesty
warnings are the last thing on the page that should fall back to a language the reader
may not have.

No webfont is loaded. The font stack leads with faces that carry Armenian glyphs
(`Noto Sans Armenian`, `Segoe UI`, `Arial Armenian`, `Sylfaen`), because pulling one from
Google Fonts would put a network dependency into a product whose whole posture is
offline-first, for a script the system fonts already cover.

## The two things this screen must never do

### Merge probable with verified

They are different claims. One is a machine guess; the other is a person's judgement.
The header shows them as separate figures in different colours, labelled with what they
mean rather than with a field name, and no number anywhere contains both.

Conflating *"the AI found 147"* with *"147 defects exist"* is the fastest way to lose a
pilot. `docs/METRICS.md` makes the same point about reporting; this is the same rule
applied to pixels.

The summary counts the **whole database**, while a separate figure counts what the
active filter shows. Otherwise a filtered screen reads as the whole picture, and someone
takes "1 defect" off a map hiding four.

### Draw a defect as a bare point

A pin implies a precision the phone's GPS does not have. Every defect is drawn as a
**ring sized in metres** — its stated uncertainty — with a small dot at the centre. The
ring is recomputed on every zoom, so it stays a real distance on the ground rather than a
decoration; at street zoom an ±8 m defect is visibly a circle you could park a car in.

The centre dot is deliberately smaller than the ring at every useful zoom. An earlier
version had a 4.5 px dot and a 5 px ring, which meant the honesty feature was completely
hidden behind the thing it was supposed to qualify.

There is a 4 px floor on the ring, so a very confident defect still shows one. A missing
ring would read as "no uncertainty", which is never true.

## Streets without a tile server

`tile.openstreetmap.org` is donated capacity under a usage policy that **excludes
production use**. It is fine on a founder's laptop and not fine under a municipality, and
that is not a licence detail to be discovered later.

So the basemap is **off by default**. Street context comes instead from the road network
`roadeye roads` already downloaded for map matching — the same ODbL data, held locally,
drawn as lines. No tile server is contacted, and none needs to be.

**That fixes the tiles and not the library.** MapLibre itself is still fetched from unpkg
at view time ([ADR-010](DECISIONS/ADR-010-dashboard-without-a-build-step.md)), so with no
internet the page loads and the map does not. An earlier version of this file claimed the
dashboard "works on a laptop with no internet at all"; it does not, and the ADR said so
plainly while this page contradicted it. Vendoring MapLibre is permitted by its BSD-3
licence and remains the open item — it is ~800 KB and one commit, and it is the difference
between offline-first as a posture and as a fact.

`?tiles=1` turns the OSM raster basemap on for local use, and the attribution bar then
says plainly what it is.

One deliberate omission: **no street-name labels on the map.** A MapLibre symbol layer
needs a `glyphs` font endpoint, which is another network dependency — and avoiding those
is the entire point. The street name is in the defect's detail panel, where a reader
needs it anyway.

## The detail panel

Click a defect and the panel shows the evidence photograph, then the facts a work order
needs, then the review controls.

Two fields are emphasised over the rest: the **street** (bold when matched — a crew is
dispatched to a road, not to a decimal coordinate) and **"could be off by ±N m"**.

`Located by` is written in words — *"snapped to the road centreline"*, *"placed by a
person"* — rather than as the enum value. A `LocationMethod` means something specific and
a municipal reader has no reason to know the vocabulary.

Review controls hit the same endpoint the keyboard review screen uses, so a decision made
here is the same append-only record with the same invariants. The dashboard is a second
door onto one review loop, not a parallel one.

## Streets, not just pins

A city does not fix one pothole. It resurfaces a length of street, and it budgets by that
length — so the sidebar rolls defects up per stretch of road, densest first, and
`roadeye streets` prints the same thing.

```bash
roadeye streets --db yerevan.db --roads data/roads/yerevan.json.gz
roadeye streets --db yerevan.db --roads ... --worst 10     # the ten worst stretches
roadeye streets --db yerevan.db --roads ... --all          # including never-driven ones
```

### Coverage comes from the frames, not the defects

This is the point of the whole feature, and it is a distinction a naive rollup destroys.

*"No defects found on Teryan Street"* and *"we have never driven Teryan Street"* are
opposite claims. A rollup built only from defects cannot tell them apart — both produce
an absent row — and a reader taking one for the other has been misled by the report's
**structure**, which is worse than a wrong number because there is nothing on the page to
disagree with.

So surveyed length is measured from where the camera actually went, and every stretch
falls into one of three stated states:

| Driven | Defects | State | Means |
|---|---|---|---|
| > 0 | > 0 | `defects` | Found this many, over this distance |
| > 0 | 0 | `clean` | **Driven and clean** |
| 0 | — | `not_surveyed` | **Never driven.** Not a claim about the road at all |

The denominator is always reported, even when unsurveyed rows are hidden: *"2.39 km of
the network's 2.4 km (99.6%)"*. Without it, four busy streets read as a survey of the
city when they may be four streets out of eleven hundred.

### Coverage is not distance driven

They come apart in two ordinary ways, and both were bugs here before they were rules.

Driving one 400 m street twice is **800 m driven and 400 m of street**. Driving 3 km down
a motorway outside the extract is 3 km driven and **none** of this network. Divide total
distance by network length and either one reports a city as surveyed on the strength of a
drive that never touched most of it — a percentage that can exceed 100% and a report a
municipality is right to stop believing.

So the numerator is metres of network inspected: per street, driven length capped at that
street's own length; off-network driving contributes nothing. Both other figures are still
reported — `surveyed_m` for what was driven, `unmatched_m` for what was driven off the
network — because a rollup that hides distance is as bad as one that inflates it. The
`roadeye streets` table shows **driven** and **length** side by side for the same reason.

### Three more rules

**Density is `None`, never zero, below 50 m of coverage.** Two defects in 8 m is not 25
per 100 m; it is an unusable sample, and the report says so rather than computing
something. `worst()` ranks only stretches long enough to rate, because putting a 10 m
sample above a 2 km one puts noise at the top of a work plan.

**Rejected defects are counted and excluded from the work.** A human saying "that was a
shadow" is the only judgement in the system that is not a guess; throwing it back into
the total would waste it.

**A gap larger than 60 m between frames is a break, not driving.** A tunnel, a signal
loss or a stopped app leaves a hole, and bridging it would claim to have inspected road
nobody drove past.

Defects that match no street are counted as `unmatched_defects` rather than dropped — a
rollup silently missing a tenth of the defects is not one to budget from.

## Rejected defects stay on the map

Faded, not removed. Removing them would hide the reviewer's own work — and *"we checked
that and it was a shadow"* is exactly the thing a municipality asks about twice.

## Architecture

Static HTML, CSS and JavaScript served by the existing FastAPI app. No bundler, no
framework, no `node_modules`, no build step. The reasoning, and the specific triggers
that should overturn it, are in
[ADR-010](DECISIONS/ADR-010-dashboard-without-a-build-step.md).

| Endpoint | Serves |
|---|---|
| `GET /dashboard` | The page |
| `GET /static/{name}` | Its CSS and JS — whitelisted by name, never a directory mount |
| `GET /api/map` | Defects as GeoJSON, filtered, plus summary, surveys and provenance |
| `GET /api/roads` | The local road network as lines, or an empty collection |
| `GET /api/streets` | Defects per stretch, with driven length and network coverage |
| `GET /api/evidence/{file}` | One evidence image, path-traversal guarded |
| `POST /api/defects/{id}/review` | A human decision — shared with the review screen |

`/api/map` returns **plain GeoJSON**. The dashboard is not a privileged client with a
private format: the same bytes open in QGIS, which a municipality may well already own.

The static route is whitelisted by filename rather than mounted as a directory, because
this app also serves an evidence directory of survey imagery, and a static mount is one
misconfiguration away from serving the wrong tree.

## A bug worth remembering

The error panel that says *"the map could not load"* was styled `display: grid` while
relying on the HTML `hidden` attribute to stay out of the way. The `hidden` attribute
works through the user-agent rule `[hidden] { display: none }`, which **any** author rule
setting `display` silently outranks.

So the panel rendered permanently: a full-bleed white box over a working map, swallowing
every click. The map underneath had been fine the whole time. It took a screenshot to
notice, because the page looked plausible — it just did nothing.

Fixed globally with `[hidden] { display: none !important }`, stated once so the next
element given both `hidden` and a display rule does not reintroduce it.

## Not built

- **Authentication.** The server binds to localhost and has none. It must not be exposed
  to a network — the evidence images may contain identifiable people (`docs/SECURITY.md`,
  `docs/PRIVACY.md`). Hosting this for a municipality's own staff is a different security
  posture and a different piece of work.
- **Trend across surveys.** The data model carries `first_seen`, `last_seen` and
  `DefectTrend` already; nothing draws them yet, and nothing can until there are two
  surveys of one street.
- **Export from the screen.** `roadeye export` does it from the command line.
- **Printing a work order.** Wanted by any real customer; needs a customer first, to say
  what a work order has to contain.
