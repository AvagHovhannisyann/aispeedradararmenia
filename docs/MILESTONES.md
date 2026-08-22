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

## M4 — Armenian review loop

**Goal:** turn drives into labelled Armenian data.

- [ ] Local review UI: image, class, confidence → approve / reject / correct
- [ ] Corrections export as a versioned dataset
- [ ] Hard negatives deliberately collected: manholes, shadows, patches, wet asphalt
- [ ] Retention policy enforcement + `Anonymizer` interface

**Acceptance:** a reviewer decides in seconds, and corrections flow into a dataset
version.

---

## M5 — Defect-level pipeline + API

- [ ] Map matching against OSM geometry, with attribution compliance
- [ ] `ROAD_SEGMENT_MATCHED` location method
- [ ] FastAPI local API over domain concepts
- [ ] Face/plate blurring (licence-audited detector)

**Acceptance:** defects are assigned to the correct road segment; nothing leaves the
laptop unblurred.

---

## M6 — Dashboard

- [ ] React + TypeScript + MapLibre map
- [ ] Filters: class, severity, verification state, survey, confidence
- [ ] Defect panel: evidence, confidence, uncertainty, model version, review controls
- [ ] Summary counts keeping probable and verified separate

**Acceptance:** a non-technical person understands it unaided and can review a defect in
seconds.

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
