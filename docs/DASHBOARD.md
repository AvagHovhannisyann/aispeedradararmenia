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
drawn as lines. The dashboard therefore works on a laptop with no internet at all, which
is what offline-first is supposed to mean.

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
- **Segment-level aggregation.** *"This 200 m of Abovyan has 14 defects"* is what a road
  maintenance budget is actually built from. Map matching (M5) is the prerequisite and is
  done; this is the natural next step.
- **Trend across surveys.** The data model carries `first_seen`, `last_seen` and
  `DefectTrend` already; nothing draws them yet, and nothing can until there are two
  surveys of one street.
- **Export from the screen.** `roadeye export` does it from the command line.
- **Printing a work order.** Wanted by any real customer; needs a customer first, to say
  what a work order has to contain.
