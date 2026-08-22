# RoadEye — Development Guide

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest
```

Python ≥3.11. The core has exactly one runtime dependency (`pydantic`); everything else
is an optional extra.

```bash
.venv/bin/pip install -e '.[video]'    # real video decoding (needs ffmpeg)
.venv/bin/pip install -e '.[vision]'   # torch + torchvision (M3)
.venv/bin/pip install -e '.[api]'      # FastAPI (M5)
```

Check what the host actually has:

```bash
.venv/bin/roadeye env
```

## Testing

```bash
.venv/bin/python -m pytest                    # everything (~1.5 s)
.venv/bin/python -m pytest tests/unit         # fast, pure logic
.venv/bin/python -m pytest tests/integration  # storage, cross-language contract
.venv/bin/python -m pytest tests/e2e          # full pipeline
.venv/bin/python -m pytest -k clustering -v
```

**The default suite must never require a GPU, ffmpeg, model weights or the network.**
That is not a nicety: a suite with heavy prerequisites stops being run, and this project
depends on tests to catch the failures that are otherwise invisible (a defect in the
wrong place looks exactly like a defect in the right place).

Anything heavier is marked and excluded by default:

```python
@pytest.mark.slow            # real models, large datasets
@pytest.mark.requires_video  # ffmpeg / PyAV and a real file
@pytest.mark.requires_torch  # torch / torchvision
```

### Test layers

| Layer | Covers |
|---|---|
| `tests/unit/` | Geodesy, interpolation, sampling, tracking, clustering, schemas |
| `tests/integration/` | SQLite + R*Tree, provenance round-trips, **collector contract** |
| `tests/e2e/` | Bundle → defects → storage → export |

The collector contract test parses `apps/collector/src/survey.ts` as text and asserts
its `BUNDLE_SCHEMA_VERSION` and field names match the Python reader. Change one side
without the other and the build fails — which is the point.

## Exercising the pipeline without a phone

```bash
python3 scripts/make_demo_survey.py surveys/demo
roadeye validate surveys/demo
roadeye process  surveys/demo --db demo.db
roadeye export   --db demo.db --geojson demo.geojson
```

The generator simulates a ~2.4 km rectangular circuit through Kentron at ~36 km/h, with
a 25-second red-light stop partway round. Both details are deliberate: the **turns**
exercise circular heading interpolation (a straight line would not), and the **stop**
exercises the stationary guard that stops idling from producing hundreds of duplicate
frames. A route through (0, 0) would hide latitude/longitude swaps, so it uses real
Yerevan coordinates and the exported GeoJSON can be eyeballed on a map.

The bundle contains **no imagery**. It exercises the plumbing and says nothing about
detection quality.

## Working with synthetic data

The pipeline runs end to end with no real footage:

```python
from roadeye.vision.fake import FakeDetector, ScriptedDetector, NullDetector
```

| Detector | Use |
|---|---|
| `FakeDetector` | Deterministic pseudo-random detections — exercises plumbing at volume |
| `ScriptedDetector` | Exactly the detections you specify per frame — **use this to test tracking and clustering behaviour** |
| `NullDetector` | Finds nothing — proves a clean road produces a valid empty result |

`FakeDetector` uses a seeded blake2b hash rather than `random`, so results are identical
across processes and machines. Python's `hash()` is salted per process and would make
"deterministic" tests flaky.

## Adding a real detector

Implement the Protocol; touch nothing else:

```python
class MyDetector:
    @property
    def model_id(self) -> str: ...
    @property
    def classes(self) -> Sequence[DamageClass]: ...
    def predict(self, frame: FrameImage) -> list[RawDetection]: ...
```

Then record a `ModelVersion` with `training_data_licenses` and `distribution_allowed`.
**Framework types must not leak into domain models.**

## Changing the survey bundle format

1. Bump `BUNDLE_SCHEMA_VERSION` in `src/roadeye/ingest/bundle.py`
2. Bump it in `apps/collector/src/survey.ts`
3. Handle the old version in `load_bundle`
4. Run the contract test

Old bundles must keep loading. Surveys are evidence; a format change that orphans them
destroys data that cost a drive to collect.

## Style

- Type annotations on public functions; `ruff` and `mypy` clean.
- Small modules with a single responsibility.
- No magic numbers — thresholds live in config objects and are recorded in
  `ProcessingRun.config`.
- No silent `except: pass`. Degrade, count, and report.
- Comments explain **why**, not what. Prefer a comment that captures a non-obvious
  constraint over one that restates the code.

## Common tasks

```bash
.venv/bin/roadeye validate surveys/survey_2026-08-18...
.venv/bin/roadeye process  surveys/survey_... --db roadeye.db --json run.json
.venv/bin/roadeye export   --db roadeye.db --csv out.csv --geojson out.geojson
.venv/bin/roadeye stats    --db roadeye.db
```

Inspect the database directly:

```bash
sqlite3 roadeye.db "SELECT defect_id, damage_class, confidence, uncertainty_m, status FROM defects LIMIT 10;"
```

## Before committing

1. `pytest` passes
2. New dependency? → `THIRD_PARTY_LICENSES.md`, same commit
3. Format change? → schema version bumped on both sides
4. Behaviour change? → test that pins it
5. Architecture decision? → an ADR
6. No survey video, frames, GPS logs, databases or weights staged

## Gotchas

- **SQLite foreign keys default to OFF.** The pragma is set in `Database.__init__`.
- **GeoJSON is `[lon, lat]`.** Reversed, Yerevan renders in the Gulf of Guinea. Pinned
  by a test.
- **Bearings are circular.** Interpolating 350° → 10° linearly passes through 180°.
- **R*Tree returns a bounding box**, not a circle. Always refine by true distance.
- **Do not label every video frame.** Adjacent frames are near-duplicates; they inflate
  a dataset without adding information, and leak across splits.
