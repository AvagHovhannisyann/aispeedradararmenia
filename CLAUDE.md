# CLAUDE.md — working agreement for RoadEye

Read this before changing anything. It is short on purpose.

## What RoadEye is

A smartphone-based road inspection system. An ordinary phone is rigidly mounted in a
vehicle and records forward-facing video plus synchronised GPS. An **offline** pipeline
turns that drive into a map of probable road defects, each traceable back to the frame,
the model and the configuration that produced it.

Intended customer: municipalities and road agencies, starting in Yerevan.

## Where the project actually is

| Component | State |
|---|---|
| Processing core (`src/roadeye/`) | **Implemented, 429 tests passing** |
| Collector (`apps/collector/`) | Scaffolded, **never run on a device** |
| Detector | Real adapter + training pipeline built (M3). Bootstrap model trained on **Czech** RDD2022 data only |
| Armenian data | **None** |
| Review loop (`roadeye review`) | **Implemented** (M4) — evidence, corrections, dataset export |
| Map matching (`roadeye match-roads`) | **Implemented** (M5) — OSM geometry, synthetic verification only |
| Redaction + retention (`roadeye redact` / `retention`) | **Implemented** (M5) — people and vehicles, fail-closed |
| Dashboard | **Not built** |

**No number this repository can currently produce says anything about a real road.**
The CLI prints a warning to that effect when the fake detector runs. Do not remove it,
and do not present synthetic output as a result.

## Build and test

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest          # must pass with no GPU, no ffmpeg, no network
.venv/bin/roadeye env               # report the host environment
.venv/bin/roadeye validate <bundle> # inspect a survey without processing it
```

314 of the 429 tests require **no optional dependency** and run in ~1.8 s; the rest are
`importorskip`-guarded and skip on a bare install. Keep it that way: a suite that needs a
GPU is a suite that stops being run.

`ruff check`, `ruff format --check` and `mypy` must all be clean across `.` — the mypy
scope covers `src/`, `ml/`, `services/` and `scripts/`, not just the domain layer.

## The constraint that shapes everything

**Zero budget.** No paid cloud, no paid AI APIs, no paid mapping, no unnecessary SaaS.
Every proposal must answer: *what does this cost the founder today?* If non-zero, find
the free alternative or defer it. Record it in `docs/COST_LEDGER.md`.

## Rules that are not up for casual revision

Each has an ADR behind it in `docs/DECISIONS/`.

1. **No ML framework in the domain layer.** Detectors sit behind
   `RoadDamageDetector` (ADR-004). A `torch.Tensor` in a domain model is a bug.
2. **No runtime AI API.** No Anthropic/OpenAI SDK in any manifest. Claude Code builds
   RoadEye; RoadEye runs offline (ADR-005). A Claude Max subscription does **not**
   include API credits.
3. **Never add Ultralytics.** AGPL-3.0 covers the training code *and produced models*,
   and the network clause reaches a hosted dashboard (ADR-009). Use torchvision
   (BSD-3) or RTMDet/MMDetection (Apache-2.0).
4. **Never mark an RDD2022-derived model distributable.** Its licence is contradictory
   — Figshare says CC BY 4.0, the authors' repo says CC BY-SA 4.0 — and is unresolved
   (`docs/LICENSE_AUDIT.md`, BLOCKING-1).
5. **Only humans may verify a defect.** No automated path writes anything beyond
   `PROBABLE` (ADR-006).
6. **The phone's GPS is the camera's position, never the defect's.** Every coordinate
   carries a `LocationMethod` and an `uncertainty_m`; both are required fields.
7. **Never claim precision we do not have** — no pothole depth from a monocular frame,
   no severity without a `severity_source`, no coordinate without uncertainty.
8. **Never add face or plate recognition.** Detection-for-blurring only
   (`docs/PRIVACY.md`, ADR-007).
9. **Never commit survey video, frames, GPS logs, databases or model weights.**
   `.gitignore` is a safety net, not permission.
10. **Never split ML data by frame.** Route-disjoint splits only (ADR-008).
11. **Record every new dependency's licence** in `THIRD_PARTY_LICENSES.md`, in the same
    commit. An unverified licence counts as rejected.

## Conventions

- Python ≥3.11, typed, `ruff` + `mypy` clean. Small modules; no 4,000-line files.
- Pydantic models use `extra="forbid"` — unexpected input must fail loudly.
- Every tunable threshold lives in a config object and is recorded in
  `ProcessingRun.config`. A run must be reproducible from its own record.
- Structured errors over silent `except: pass`. A malformed GPS sample must never kill
  a survey; it is dropped, counted and reported.
- Changing an on-disk format means bumping its `schema_version` **and** writing the
  migration. The bundle format has a cross-language contract test — if you change
  `apps/collector/src/survey.ts`, the Python side must move with it.

## Where to look

| Question | File |
|---|---|
| Why is it built this way? | `docs/ARCHITECTURE.md`, `docs/DECISIONS/` |
| Can we legally ship this? | `docs/LICENSE_AUDIT.md` |
| What did the research find? | `docs/RESEARCH.md`, `docs/TECHNOLOGY_EVALUATION.md` |
| How do coordinates work? | `docs/GEOLOCATION.md` |
| What are the entities? | `docs/DATA_MODEL.md` |
| How do we train? | `docs/ML_STRATEGY.md`, `docs/TRAINING.md` |
| How do humans correct it? | `docs/REVIEW_LOOP.md` |
| Which street is this defect on? | `docs/MAP_MATCHING.md` |
| What counts as success? | `docs/METRICS.md`, `docs/PILOT_PLAN.md` |
| What about people in the video? | `docs/PRIVACY.md` |
| How do we drive a survey? | `docs/COLLECTION_PROTOCOL.md` |
| What's next? | `docs/MILESTONES.md` |

## Working style

Prefer boring, understandable engineering. Get RoadEye onto an actual road rather than
perfecting theory.

Do not: silently make irreversible architecture decisions, guess at licences, claim
performance without measurement, or generate large amounts of code before the
architecture is coherent.

Do: investigate, document, implement, run the tests, read the failures, iterate. When
uncertain — research it, write down what you found, and choose the simplest reversible
option.

**Current milestone: M1** — get a real survey bundle off a real phone after a real
drive. Everything else is secondary to that.
