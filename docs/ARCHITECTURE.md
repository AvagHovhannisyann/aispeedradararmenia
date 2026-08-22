# RoadEye — Architecture

**Status:** M0-M5. The Python core is implemented and tested, including the review API;
the collector is scaffolded but unrun on a device; the dashboard is not built.

## The central bet

> **The phone collects evidence. The laptop does the thinking.**

Real-time on-device inference sounds more advanced and would make the zero-budget
project *harder*. Offline-first buys four things that matter more than sounding
impressive:

1. The collector only has to record reliably — not simultaneously capture, run a neural
   network, geolocate, render overlays and keep a stable video file.
2. The model can be replaced tomorrow without reinstalling anything on a phone.
3. Training and experimentation happen on free laptop/Kaggle/Colab compute.
4. Moving inference on-device later does not require redesigning the product.

Recorded as `ADR-001`.

## System shape

```
┌──────────────────────── PHONE (Expo, foreground) ────────────────────────┐
│  rear camera ──► video.mp4        GPS ──► locations.jsonl                │
│                       └──────────┬──────────┘                            │
│                            survey bundle                                 │
└───────────────────────────────────┬──────────────────────────────────────┘
                                    │  manual transfer (never auto-upload)
                                    ▼
┌──────────────────────── LAPTOP (Python, offline) ────────────────────────┐
│  ingest/bundle    validate, clean GPS, report what was dropped           │
│  video/sampling   distance-based frame plan (skips red lights)           │
│  video/decoder    FrameSource protocol (synthetic ⟷ PyAV)                │
│  quality/metrics  accept / degrade / reject                              │
│  vision/base      RoadDamageDetector protocol  ◄── swappable             │
│  tracking         detections ──► tracks   (dedup layer 1)                │
│  geolocation      video time ──► absolute time ──► interpolated position │
│  clustering       tracks ──► defects      (dedup layer 2)                │
│  map_matching     defect ──► road segment (OSM, ODbL)                    │
│  privacy          people + vehicles ──► destroyed  ◄── swappable         │
│  storage/db       SQLite + R*Tree                                        │
│  reporting        CSV / GeoJSON with provenance                          │
└───────────────────────────────────┬──────────────────────────────────────┘
                                    ▼
              dashboard: map · evidence · approve/reject/correct   [M6]
```

## Layering rule

```
domain/     no ML framework, no I/O, no network  ← the invariants live here
   ▲
   │  adapters convert at the boundary
   │
vision/ video/ storage/ reporting/   ← frameworks and I/O live here
   ▲
   │
pipeline.py · cli.py · services/api  ← orchestration
```

**The domain layer imports no ML framework.** A `torch.Tensor` in a domain model is a
bug. This is what makes the whole pipeline testable on a CPU-only machine with no
weights, no ffmpeg and no network — 314 of the 429 tests need no optional dependency at
all and run in ~1.8 s.

## The five seams

Each isolates something we expect to be wrong about.

| Seam | Protocol | Isolates |
|---|---|---|
| Detector | `RoadDamageDetector` | Which model/framework wins (ADR-004) |
| Redactor | `RegionDetector` | Which model finds people/vehicles, and its checkpoint licence (L-6) |
| Frame source | `FrameSource` | ffmpeg availability; synthetic vs real video |
| Storage | `Database` | SQLite → PostGIS migration (ADR-003) |
| Bundle format | `schema_version` | Expo collector → native collector (ADR-002) |

The bundle format is the important one: **the format, not the app, is the contract.** A
future native collector is compatible when `roadeye validate` accepts its output, which
`tests/integration/test_collector_contract.py` enforces against the TypeScript source.

## Two-stage deduplication

The product claim is that one physical pothole becomes **one** municipal item. Two
independent mechanisms are needed, because each catches what the other cannot:

| Stage | Mechanism | Catches |
|---|---|---|
| **Tracking** | Greedy IoU association across adjacent frames | The 5-20 frames in which one pothole is visible as the car approaches |
| **Clustering** | Single-link geospatial agglomeration with an extent cap | Broken tracks, the return leg of a drive, and **repeat surveys weeks later** |

The second stage's cross-survey behaviour is what makes `first_seen` / `last_seen` /
trend analysis possible at all — it is the difference between a defect detector and an
asset-management system.

**Both stages preserve their sources.** A defect always carries the observation and
detection ids it was built from, because a work order must be able to show the original
photograph.

### A bug worth remembering

The first end-to-end run collapsed 234 observations along a 600 m drive into **one**
defect. Single-link clustering chains: each observation was within the merge radius of
its neighbour, so one cluster grew down the entire street. A real row of potholes would
have been reported as a single item.

Fixed with `max_cluster_extent_m`, and covered by
`tests/unit/test_clustering.py::TestChainingRegression`. The lesson generalises: the
deduplication logic has two failure directions, and tests must pin **both** — too many
defects, and too few.

## Invariants enforced by types

Not conventions — the constructors refuse:

1. **`GeoPoint` requires `method` and `uncertainty_m`.** A bare lat/lon cannot exist,
   because six decimal places look like centimetres and the fix was 12 m.
2. **A `Defect` with an assessed severity must declare `severity_source`.** An
   unattributed severity is false authority.
3. **`ModelVersion.distribution_allowed` defaults to `False`** and cannot be set `True`
   without stating training-data licences (BLOCKING-1).
4. **Unknown fields are rejected** (`extra="forbid"`), so a collector-side rename fails
   loudly instead of silently dropping data.
5. **Only humans move a defect beyond `PROBABLE`.** No automated path writes `VERIFIED`.

## Provenance chain

Every defect must answer *"why do you believe this exists?"*:

```
Defect → DefectObservation → Detection → Frame → Survey → video + GPS
              │                  │          │
              │                  │          └→ quality scores, position, uncertainty
              │                  └→ ModelVersion → DatasetVersion → licences
              └→ ProcessingRun → full config + git commit
```

`ProcessingRun.config` stores the *complete effective configuration*, so a run is
reproducible from its own record rather than from someone's memory of the flags.

Reviews are **append-only**. A human correction is evidence; a system selling
auditability to a government cannot quietly overwrite it.

## What is deliberately absent

| Not built | Why |
|---|---|
| Kubernetes, microservices, queues | One laptop. Nothing here needs them. |
| Docker to run the MVP | Optional for CVAT only; never required |
| A generic DB abstraction layer | Speculative abstraction costs more than the eventual port |
| Real-time on-device inference | ADR-001 |
| Any runtime LLM/AI API call | ADR-005 — RoadEye must run offline with no per-call cost |
| Face/plate *recognition* | Prohibited by `PRIVACY.md`. Detection-for-blurring exists; nothing extracts, encodes or compares an identity |

## Repository layout

```
apps/collector/      Expo survey collector (TypeScript)
apps/dashboard/      React + MapLibre municipal UI            [M6]
services/api/        FastAPI local API + review UI
src/roadeye/         the processing core (implemented)
ml/                  datasets, training, evaluation, experiments
tests/               unit · integration · e2e (429 passing)
docs/                research, decisions, protocol, privacy, metrics
```
