# RoadEye — ML Strategy

**Status:** M0 plan. No model has been trained. No Armenian data exists yet.

## The strategic claim

> The moat is **not** the model. It is the **Armenian dataset**, the geolocation and
> deduplication system around it, and the workflow that turns raw observations into
> municipal decisions.

Anyone can download a detector. Nobody else has 50,000 reviewed observations of
Armenian roads across seasons, districts and pavement types. That asset compounds; a
model checkpoint does not.

## Domain shift is measured, not hypothetical

The RDD research line exists *because* single-country models generalise badly: models
trained predominantly on Japanese roads degraded substantially on Indian and Czech
roads, which is why the dataset became multi-national.

For calibration: the best multi-country ensemble at CRDDC'2022 reached roughly
**F1 ≈ 0.76** on the combined six-country test set. Road distress detection across
varying countries, cameras and conditions is a **hard, unsolved problem**.

Two consequences:

1. **A foreign pretrained model is a starting point, not the product.** Any claim that
   RDD2022 weights will "just work" in Yerevan is unsupported by the literature.
2. **Our honest metrics should be read against ~0.76, not against an imagined 0.99.**
   Managing that expectation — for ourselves and for a municipality — is part of the
   engineering job.

Yerevan brings its own asphalt textures, patching styles, freeze-thaw damage, dust,
shadow structure, markings, vehicle mix and repair conventions.

## Bootstrap, then sever

```
RDD2022 (quarantined)  ->  bootstrap model  ->  pseudo-labels on Armenian footage
                                                        |
                                          human review + correction
                                                        |
                                             Armenian dataset v1..vN
                                                        |
                                     model trained WITHOUT RDD2022 lineage
                                                        |
                                              distributable product
```

The final step is not merely a licensing manoeuvre — it is the business goal anyway.
But it does resolve **BLOCKING-1**: RDD2022 is published under two contradictory
licences (Figshare says CC BY 4.0; the authors' repository says CC BY-SA 4.0), and
under the share-alike reading, distributing weights derived from it could oblige us to
give the model away.

So the repository enforces a **provenance quarantine**:

- `ModelVersion.training_data_licenses` is mandatory.
- `ModelVersion.distribution_allowed` **defaults to `False`** — fail closed.
- Setting it `True` without stating licences raises a validation error (tested).
- Any model with RDD2022 in its lineage stays `False` until BLOCKING-1 is resolved.

RDD-derived models are for **internal evaluation and pseudo-labelling only.**

## Building the Armenian dataset for $0

### Do not label every frame

Adjacent video frames are near-duplicates. Labelling 30 fps footage wastes effort and
inflates apparent dataset size without adding information. The pipeline's
distance-based sampling already spaces frames ~2.5 m apart; label those.

### The active-learning loop

```
drive -> distance-sample frames -> quality filter -> model predicts
      -> human reviews (approve / reject / correct)
      -> corrections become labels
      -> dataset version N+1 -> retrain -> repeat
```

Human review time is the scarce resource, so spend it where it buys most:

- detections near the confidence threshold (most informative)
- confident detections that were wrong (hard negatives)
- frames where the model found nothing but a defect exists (misses — the expensive kind)

### Label hard negatives deliberately

A dataset of only beautiful potholes produces a model that has learned *"dark irregular
blob = pothole"*. It will demo brilliantly on curated screenshots and fail on the first
real drive.

Required negative classes in the Armenian set:

```
manhole covers        asphalt patches      tar repair lines
utility cuts          shadows (tree, building, vehicle)
wet asphalt           oil stains           drains
repaired potholes     speed bumps          construction plates
crosswalk paint       road joints          debris and leaves
```

Shadows and manholes are the two that will hurt most in Yerevan. Budget for them.

## Data splitting: the mistake that fakes success

**Never randomly split adjacent frames across train/val/test.**

```
frame 100  pothole A  -> train
frame 101  pothole A  -> train
frame 102  pothole A  -> TEST     <-- almost the same photograph
frame 103  pothole A  -> train
```

That yields excellent validation numbers and a model that has memorised, not learned.
It is the single easiest way to spend months believing a broken system works.

**RoadEye splits by route, survey and date** — never by frame:

```
TRAIN       routes driven on days A, B, C
VALIDATION  different streets, different day
TEST        completely held-out routes, different date, ideally different conditions
```

`DatasetVersion.split_routes` stores route ids per split, making the split auditable
rather than a claim in a README. Where possible, the test set should also differ in
**device and mount**, since those are real distribution shifts we will meet in the field.

## Model selection

See `TECHNOLOGY_EVALUATION.md` for the full matrix. Summary:

| Stage | Choice | Why |
|---|---|---|
| M3 baseline | **torchvision** (BSD-3) | Installs cleanly on a CPU-only machine; zero build risk |
| M7 candidate | **RTMDet / MMDetection** (Apache-2.0) | Strong accuracy-per-parameter; the likely shipping model |
| M7 alternative | RT-DETR family (mostly Apache-2.0) | Current real-time frontier; audit each checkpoint |
| Never | Ultralytics YOLO | AGPL-3.0 covers training code *and produced models* |

The domain layer depends on a `RoadDamageDetector` Protocol, so being wrong about any
of these costs one adapter, not a rewrite.

### Boxes now, masks later

Potholes are well served by boxes. Thin cracks are not — a diagonal crack's bounding
box is mostly not-crack, and area/length/density estimates need masks. `Detection.mask`
exists from day one and stays `None` in MVP, so adding segmentation is not a migration.

## Severity: what we will not fake

**Bounding-box area is not physical size.** A box filling 20% of the frame is either a
small pothole close by or a large one far away; perspective destroys the relationship.

**Depth is not recoverable** from one monocular RGB frame. We will not tell a
municipality a hole is 7.3 cm deep.

So MVP severity is `UNASSESSED / LOW / MEDIUM / HIGH`, **human-assigned**, and every
value carries a `severity_source`. The domain model *refuses to construct* a defect with
an assessed severity and no source — an unattributed severity is exactly the false
authority that loses a government's trust.

Later, calibrated ground-plane geometry plus segmentation can support
`GEOMETRIC_ESTIMATE`, still labelled as an estimate.

## Experiment discipline

Every training run records: experiment id, git commit, architecture, framework and
version, checkpoint origin **and its licence**, dataset version, class list,
hyperparameters, resolution, augmentation, seed, hardware, metrics, artefacts.

```
ml/experiments/exp_001/
  config.yaml  metrics.json  confusion_matrix.png  predictions/  model_metadata.json
```

**There must never be an unexplained `best.pt`.** A government-facing system that cannot
say where a model came from cannot defend its outputs.

## Free compute, used correctly

Kaggle (~30 GPU-h/week, commonly P100) is the primary trainer; Colab is backup, and its
free tier explicitly guarantees no GPU, with unpublished limits that "vary over time".

Rules:
- Checkpoint frequently; eviction is normal, not exceptional.
- Never make production depend on either.
- **Never upload raw Armenian survey video** (see `PRIVACY.md`).

## What success looks like at M7

Not "mAP went up". Specifically:

1. An Armenian dataset ≥5,000 reviewed images with documented hard negatives.
2. A route-disjoint train/val/test split with **zero** frame leakage, auditable from
   `DatasetVersion.split_routes`.
3. A model trained without RDD2022 lineage, marked `distribution_allowed=True` with
   licences stated.
4. Honest per-class metrics on a held-out **route**, reported next to system-level
   metrics from `METRICS.md`.
5. A measured statement of where it fails — night, rain, shadow, wet asphalt — rather
   than a single headline number.
