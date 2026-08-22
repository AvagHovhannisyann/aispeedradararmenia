# RoadEye

[![CI](https://github.com/AvagHovhannisyann/aispeedradararmenia/actions/workflows/ci.yml/badge.svg)](https://github.com/AvagHovhannisyann/aispeedradararmenia/actions/workflows/ci.yml)

Smartphone-based road inspection. Mount an ordinary phone in a car, drive, and get a map
of probable road defects — each one traceable back to the frame, the model and the
configuration that produced it.

Built for Armenian municipalities, on a zero software budget.

---

## Status: M1 — pre-first-drive

**Nothing in this repository has been validated against a real road.**

| Component | State |
|---|---|
| Processing core | Implemented — 509 tests, no GPU/ffmpeg/network needed |
| Survey collector (Expo) | Scaffolded, **never run on a device** — but its bundle logic is tested |
| Road-damage detector | Adapter + training pipeline built. Bootstrap model trained on **Czech** data only — not an Armenian detector |
| Armenian dataset | **None** |
| Human review loop | Implemented — review UI, corrections, training-data export |
| Map matching | Implemented — OSM geometry, street names, ODbL attribution. Verified on synthetic geometry only |
| Redaction / retention | Implemented — people and vehicles blurred irreversibly, deletions logged |
| Municipal dashboard | Implemented — **Armenian UI**, map with uncertainty rings, per-street rollup, review controls |

The CLI prints a warning whenever the fake detector runs. Any output it produces
describes nothing about any road.

## How it works

```
PHONE                          LAPTOP                        OUTPUT
rear camera ──► video.mp4      ingest + validate             map of defects
GPS ────────► locations.jsonl  sample frames by distance     evidence images
                  │            detect road damage            CSV / GeoJSON
             survey bundle ──► track across frames           human review
                               interpolate positions
                               cluster into defects
                               store + export
```

The phone collects evidence; the laptop does the thinking. That is deliberate — see
[ADR-001](docs/DECISIONS/ADR-001-offline-first.md).

## Try it in two minutes

No phone, model, footage or GPU needed.

### Easiest: GitHub Codespaces (runs in your browser)

On the repo page: **Code ▸ Codespaces ▸ Create codespace on main**. Wait for it to
finish setting up (~2 minutes — it installs everything for you), then in its terminal:

```bash
./scripts/demo.sh
```

That runs the whole chain: tests, generate a synthetic drive, validate, process,
export.

### Or locally

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
./scripts/demo.sh
```

### Then see it on a map

```bash
python3 scripts/view_map.py
```

Click **Open in Browser** when the popup appears (or open the **PORTS** tab and click
the globe on port 8000). The markers trace an 800 m × 400 m rectangle in central
Yerevan; click one for its full record — confidence, uncertainty, model version,
processing run.

Nothing is uploaded anywhere: the page is generated locally and served from your own
machine. That matters less for synthetic data than it will for real Armenian surveys,
which must never leave local storage (`docs/PRIVACY.md`).

> `demo_output/` is git-ignored, so VS Code greys it out or hides it in the file
> explorer. The files are there.

**What this proves:** timestamps align, positions interpolate, a stop at a red light
produces no duplicate frames, repeated views collapse into single defects, storage
round-trips, and exports are well-formed and carry their uncertainty.

**What it does not prove:** anything at all about detecting road damage. The detector
is fake and there are no pixels. `roadeye process` prints a warning saying so.

Requires Python ≥3.11 and one dependency (`pydantic`). Video decoding and real
detectors are optional extras.

## What makes this different from a pothole demo

**One pothole is one defect.** A defect visible in 20 frames across 3 surveys is one
map marker with 20 pieces of evidence, not 20 markers. Two independent deduplication
stages — temporal tracking and geospatial clustering — enforce that, and both failure
directions are pinned by tests.

**Nothing claims precision it doesn't have.** Every coordinate carries how it was
derived and how wrong it might be. A `GeoPoint` cannot be constructed without them. No
pothole depth is inferred from a single monocular frame; no severity exists without a
recorded source.

**Only humans verify.** A machine may write `PROBABLE` and nothing stronger. Every
human correction is retained as training signal, and the review log is append-only.

**Every defect can answer "why do you believe this exists?"**

```
Defect → Observation → Detection → Frame → Survey → video + GPS
              └→ ModelVersion → DatasetVersion → licences
              └→ ProcessingRun → full config + git commit
```

## Two findings from the research phase worth knowing

**RDD2022 — the obvious bootstrap dataset — has two contradictory licences.** The
Figshare DOI record says CC BY 4.0; the authors' own repository says CC BY-SA 4.0. Under
the share-alike reading, distributing a model trained on it could oblige us to give the
model away. We assume the stricter reading and quarantine RDD-derived weights as
non-distributable until the authors clarify.
→ [`docs/LICENSE_AUDIT.md`](docs/LICENSE_AUDIT.md)

**Ultralytics YOLO — used by nearly every road-damage tutorial — is AGPL-3.0**, covering
the training code *and the models it produces*, with a network clause that reaches a
hosted municipal dashboard. It is rejected for the shipping path, not merely noted.
→ [ADR-009](docs/DECISIONS/ADR-009-reject-ultralytics.md)

## Documentation

| | |
|---|---|
| [ARCHITECTURE](docs/ARCHITECTURE.md) | How the system fits together |
| [DECISIONS](docs/DECISIONS/) | Why, one ADR at a time |
| [RESEARCH](docs/RESEARCH.md) | Verified findings with sources |
| [TECHNOLOGY_EVALUATION](docs/TECHNOLOGY_EVALUATION.md) | USE / EXPERIMENT / DEFER / REJECT |
| [LICENSE_AUDIT](docs/LICENSE_AUDIT.md) | What we can legally ship |
| [DATA_MODEL](docs/DATA_MODEL.md) | Entities and invariants |
| [GEOLOCATION](docs/GEOLOCATION.md) | Positioning, with the projection maths |
| [ML_STRATEGY](docs/ML_STRATEGY.md) | Dataset, leakage, provenance |
| [METRICS](docs/METRICS.md) | Model metrics vs the ones that matter |
| [REVIEW_LOOP](docs/REVIEW_LOOP.md) | How human corrections become training data |
| [MAP_MATCHING](docs/MAP_MATCHING.md) | Turning a coordinate into a street name |
| [DASHBOARD](docs/DASHBOARD.md) | What a municipality actually sees |
| [TRAINING](docs/TRAINING.md) | Getting the data, and where to actually train |
| [PRIVACY](docs/PRIVACY.md) | People in the footage |
| [COLLECTION_PROTOCOL](docs/COLLECTION_PROTOCOL.md) | How to drive a survey |
| [PILOT_PLAN](docs/PILOT_PLAN.md) | The honest comparison against inspectors |
| [COST_LEDGER](docs/COST_LEDGER.md) | What everything costs |
| [MILESTONES](docs/MILESTONES.md) | M0 → M8 |

## Repository layout

```
src/roadeye/       processing core (implemented)
apps/collector/    Expo survey collector
services/api/      FastAPI + review UI + municipal dashboard
ml/                datasets, training, experiments
tests/             unit · integration · e2e
docs/              research, decisions, protocol
```

## Next step

Get a real survey bundle off a real phone after a real drive through Yerevan. Everything
else is secondary.

## Licence

Proprietary. Third-party components are inventoried in
[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).
