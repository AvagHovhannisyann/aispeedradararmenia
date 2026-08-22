# RoadEye — Data Model

**Status:** implemented in `src/roadeye/domain/models.py`, persisted by
`src/roadeye/storage/db.py`. Domain schema version **1**.

## The distinction everything else depends on

```
DETECTION   "the model saw something in ONE frame"
                        │
                     TRACK          (dedup 1: adjacent frames)
                        │
                  OBSERVATION       "this defect was seen during THIS survey"
                        │
                     DEFECT         (dedup 2: geospatial, across surveys)
                        │
              "RoadEye believes one real road problem exists here"
```

A pothole seen in 20 frames across 3 surveys is **one defect** with 20+ detections and
3 observations. Reporting 20 defects would make the dashboard unusable within minutes
and the product untrustworthy within one meeting.

## Entities

| Entity | Meaning | Mutability |
|---|---|---|
| `Survey` | One drive | Immutable after ingest |
| `LocationSample` | One GPS fix | Immutable |
| `Frame` | One sampled image | Immutable |
| `Detection` | One model output in one frame | Immutable except `track_id` |
| `Track` | Detections of one object across adjacent frames | Derived |
| `DefectObservation` | A defect seen during one survey | Derived |
| `Defect` | A believed real-world road problem | Mutable (status, severity, location) |
| `Review` | A human decision | **Append-only** |
| `ModelVersion` | A trained model + provenance | Immutable |
| `DatasetVersion` | A training-data snapshot | Immutable |
| `ProcessingRun` | One pipeline execution | Immutable after completion |

## Fields that carry real weight

### `GeoPoint` — no naked coordinates

```python
GeoPoint(lat=..., lon=..., method=LocationMethod, uncertainty_m=float)
```

`method` and `uncertainty_m` are **required**; constructing a bare lat/lon raises. The
phone's GPS is the camera's position, never the defect's, and the two must never be
confused (`GEOLOCATION.md`).

### `Defect` — four independent concepts kept apart

These are routinely conflated, and conflating them is how a municipality is misled:

| Field | Answers |
|---|---|
| `confidence` | "How sure is the model this is a pothole?" |
| `severity` | "How bad is it?" |
| `status` | "Has a human checked?" |
| *priority* | "How urgently should the city repair it?" — **not stored yet** |

Priority is deliberately absent. It is not a model output: it depends on road
importance, traffic exposure, persistence and municipal policy. Inventing it now would
be inventing authority.

`severity_source` is mandatory whenever severity is assessed — enforced by a validator,
covered by tests.

### `RoadSegmentRef` — kept separable on purpose

Road geometry comes from OpenStreetMap under **ODbL**, which carries share-alike
obligations *on data*. Our defect coordinates are our own. Keeping the OSM-derived
identifier as a referenced object rather than denormalised columns preserves the option
to detach it from a proprietary defect database (`LICENSE_AUDIT.md`, L-3). It costs
nothing now.

### `ModelVersion` — provenance that travels with the weights

```python
training_data_licenses: list[str]     # required to distribute
distribution_allowed: bool = False    # fail closed
```

Because of BLOCKING-1 (RDD2022's contradictory licences), a model's distributability
must live *with the model record*, not in someone's memory.

### `Detection.mask` — a nullable field that prevents a migration

`None` in MVP. Boxes suit potholes; thin cracks need masks for area/length/density. One
nullable field now beats a schema migration later.

## Storage

SQLite with an R*Tree spatial index (`ADR-003`). Notable choices:

- **Foreign keys are enabled explicitly.** SQLite defaults them *off*; without the
  pragma, cascading deletes silently do nothing and orphaned evidence accumulates.
- **R*Tree entries are degenerate point rectangles**; radius queries widen the *query*
  box, then filter by true great-circle distance. Index-then-refine — the box alone
  would return points at 70 m for a "within 50 m" query (tested).
- **`reviews` has no UPDATE or DELETE path.** Not by convention: there is no method.

### Migration to PostgreSQL/PostGIS

Deferred, not designed around. When it happens:

| SQLite (now) | PostGIS (later) |
|---|---|
| `lat REAL, lon REAL` | `geography(Point, 4326)` |
| `defects_rtree` virtual table | GiST index |
| `*_json TEXT` columns | `jsonb` |
| Local files | Object storage |

The port is a day of work on a schema this size. Building a generic ORM abstraction now
to prepare for it would cost more than the port and obscure the code meanwhile.

## Cross-survey history (schema ready, logic at M7)

Re-surveying the same street is what turns RoadEye from a detector into an asset
manager:

```
18 Aug   ● pothole A      ● crack B
08 Sep   ● pothole A (larger)   ✓ crack B absent   ● pothole C (new)
```

`DefectTrend` supports `NEW / STABLE / WORSENING / POSSIBLY_REPAIRED / UNKNOWN`.

**`POSSIBLY_REPAIRED` is deliberately hedged.** One missing observation is evidence of
one drive, not of a repair — the defect may have been in shadow, occluded by a bus, or
outside the sampled frames. Declaring "repaired" on that basis would put the
municipality's own records in error. Requiring multiple clean passes is the cautious
default.

## Changing this model

1. Bump `DOMAIN_SCHEMA_VERSION` and `STORAGE_SCHEMA_VERSION` together.
2. Write the migration in `Database._migrate`.
3. Update `apps/collector/src/survey.ts` if the *bundle* format changed, and
   `BUNDLE_SCHEMA_VERSION` with it — the contract test will fail otherwise.
4. Never repurpose an existing enum value; enum values are persisted and exported.
5. Never widen retention without updating `PRIVACY.md` in the same commit.
