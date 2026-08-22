# RoadEye — Milestones

Each milestone has acceptance criteria. **Do not skip ahead without a reason** — the
ordering exists so that each stage is validated before the next depends on it.

Current: **M1**.

---

## M0 — Research and architecture ✅ COMPLETE

**Done:**
- Technology research verified against primary sources (`docs/RESEARCH.md`)
- Decision matrix with USE/EXPERIMENT/DEFER/REJECT (`docs/TECHNOLOGY_EVALUATION.md`)
- Licence audit, including two blocking findings (`docs/LICENSE_AUDIT.md`)
- 9 ADRs recording irreversible choices
- Repository structure and monorepo layout
- Risks documented

**Two findings that changed the plan:**
1. RDD2022's licence is *contradictory*, not merely unverified — quarantine required.
2. torchvision, not MMDetection, is the right M3 baseline on a CPU-only machine.

---

## M1 — Collector 🔄 IN PROGRESS

**Goal:** a real survey bundle off a real phone after a real drive.

**Done:**
- Expo collector scaffolded with foreground video + GPS recording
- Survey bundle format v1, with a cross-language contract test
- `roadeye validate` for inspecting bundles
- Collection protocol written
- Bundle contract split into a dependency-free module and **tested under `node --test`**
  with no install — 31 tests, run by `pytest` too
- Four survey-losing bugs found by review and fixed: the manifest written before the
  video landed, concurrent flushes discarding GPS fixes, a free-space floor too small
  for a 30-minute drive, and a permissions button that could not re-request location

**Remaining:**
- [ ] `npm install` and run on a physical device
- [ ] Buy and fit a rigid phone mount
- [ ] Measure and record mount calibration
- [ ] **Drive 10+ minutes in Yerevan**
- [ ] Transfer a bundle and `roadeye validate` it
- [ ] Confirm GPS fix rate, accuracy distribution and video/GPS alignment on real data

**Acceptance:** one person records a 30-minute survey without corruption, and the
resulting bundle validates cleanly.

---

## M2 — Offline ingestion ✅ COMPLETE (pending real data)

**Done:** bundle ingest, GPS cleaning, timestamp synchronisation, distance-based
sampling, quality gating, SQLite + R*Tree storage, CSV/GeoJSON export, 201 tests.

**Acceptance:** every analysed frame maps to a plausible time and position. *Verified
against synthetic data; must be re-confirmed on the first real bundle.*

---

## M3 — Baseline computer vision 🔄 IN PROGRESS

**Goal:** a reproducible detector producing real detections.

**Done:**
- [x] Dataset ingestion with provenance, checksums and **partial download** —
      `remote_zip.py` reads one country out of the 13.3 GB archive over HTTP ranges
- [x] Map D00/D10/D20/D40 → RoadEye classes, preserving original codes
- [x] Leakage-safe contiguous splits (ADR-008), asserted by test
- [x] `TorchvisionDetector` adapter behind the existing Protocol
- [x] Training pipeline with full experiment metadata and epoch checkpointing
- [x] Evaluation with per-class metrics that refuses the training split
- [x] Model registry entries inherit `distribution_allowed` from the dataset
- [x] `roadeye detect` — run a model over images and draw the boxes

**Remaining:**
- [ ] Verify RDD2022's licence with the authors (**BLOCKING-1**)
- [ ] Train properly on GPU (Kaggle) rather than a CPU proof run — `docs/TRAINING.md`
- [ ] Run on **Armenian footage**, which does not exist until M1 completes

**Acceptance:** a reproducible baseline runs on Armenian footage and produces
detections with recorded provenance. *Blocked on M1: there is no Armenian footage.*

---

## M4 — Armenian review loop ✅ COMPLETE (pending real data)

**Goal:** turn drives into labelled Armenian data.

**Done:**
- [x] Evidence images written per defect — clean frame, annotated context, close-up crop
- [x] Keyboard-driven review UI: approve / reject / change class / severity / location
- [x] Reviews are append-only, recording before and after values
- [x] `roadeye export-dataset` turns decisions into a versioned training dataset
- [x] Rejections become **hard negatives**; corrections carry the human's class
- [x] Survey-disjoint splits, with an honest warning when only one drive exists
- [x] Exported datasets are marked `distribution_allowed: true` — ours, not RDD2022's

**Remaining:**
- [ ] Retention policy enforcement + `Anonymizer` interface (moved to M5 with blurring)
- [ ] Run it on **Armenian footage**, which does not exist until M1 completes

**Acceptance:** a reviewer decides in seconds, and corrections flow into a dataset
version. *Mechanically verified on synthetic and real road images; blocked on M1 for
Armenian data.* See `docs/REVIEW_LOOP.md`.

---

## M5 — Defect-level pipeline + API 🔄 IN PROGRESS

**Done:**
- [x] Map matching against OSM geometry — `roadeye roads`, `roadeye match-roads`
- [x] `ROAD_SEGMENT_MATCHED` location method, with uncertainty that never shrinks
- [x] Attribution derived from the data, in GeoJSON and as a CSV sidecar
- [x] Match quality (`match_distance_m`, `heading_delta_deg`) persisted — storage
      schema v2, with an additive migration
- [x] FastAPI local API over domain concepts (delivered early with M4's review UI)

- [x] Redaction interface (`RegionDetector` protocol) mirroring the detector seam
- [x] People + vehicle blurring — irreversible mosaic, fail-closed, licence-audited
- [x] Retention policy enforcement with an append-only deletion log (carried from M4)

**Remaining:**
- [ ] Run map matching and redaction on a **real Yerevan survey**, which does not exist
      until M1

**Acceptance:** defects are assigned to the correct road segment; nothing leaves the
laptop unblurred. *Matching is verified on synthetic geometry only — thresholds are
defensible starting values, not tuned results (`docs/MAP_MATCHING.md`). Redaction is
verified on real road photographs and is best-effort by nature: a detector that missed
somebody has produced an image with somebody in it (`docs/PRIVACY.md`).*

---

## M6 — Dashboard ✅ COMPLETE (pending real data and a real reader)

**Done:**
- [x] MapLibre map, **Armenian by default** with an English toggle
- [x] Filters: class, severity, verification state, survey, confidence
- [x] Defect panel: evidence, confidence, uncertainty, street, model version, review controls
- [x] Summary counts keeping probable and verified separate — and filtered-vs-total apart
- [x] Uncertainty drawn as a ring in **metres**, so a pin never implies precision
- [x] Streets drawn from the local road network, so no tile server is required
- [x] Provenance banner: synthetic output declares itself
- [x] **Per-street rollup** (`roadeye streets`) with coverage measured from the camera
      track, so "driven and clean" and "never driven" can never be confused

Built without React or a build step — [ADR-010](DECISIONS/ADR-010-dashboard-without-a-build-step.md).

**Remaining:**
- [ ] Show it to an actual municipal employee and watch where they get stuck
- [ ] Run it on **Armenian data**, which does not exist until M1

**Acceptance:** a non-technical person understands it unaided and can review a defect in
seconds. *Mechanically complete; the "understands it unaided" half is unverified until a
non-technical person has sat in front of it.* See `docs/DASHBOARD.md`.

---

## M7 — Local Armenian model

- [ ] Armenian dataset ≥5,000 reviewed images
- [ ] **Route-disjoint** train/val/test split, auditable via `split_routes`
- [ ] Model trained **without RDD2022 lineage** → `distribution_allowed=True`
- [ ] Honest per-class metrics on held-out routes
- [ ] Documented failure modes

**Acceptance:** an evaluation that names its held-out routes and reports where the model
fails.

---

## M8 — Controlled pilot

- [ ] Fixed route, chosen and written down in advance
- [ ] Human ground-truth inventory built **first**
- [ ] Two RoadEye surveys of the same route
- [ ] Blind review, then comparison at a pre-agreed 20 m matching threshold
- [ ] Full system metrics, including misses and location-error distribution

**Acceptance:** an honest comparison against human inspectors — including what RoadEye
missed.

Full design in `docs/PILOT_PLAN.md`.

---

## Deliberately later

Faded lane markings · damaged signs · blocked bus stops · illegal dumping · sidewalk
defects · road roughness from accelerometer · on-device inference (Core ML / LiteRT) ·
ray-to-ground projection · trend analysis across surveys · PostgreSQL/PostGIS ·
multi-city.

**Never without a separate company-level decision and its own legal review:** automated
traffic enforcement (see `docs/PRIVACY.md`).
