# RoadEye — The Review Loop (M4)

**Status:** implemented and working end to end.

This is the mechanism that makes RoadEye compound rather than plateau:

```
AI detects  →  human corrects  →  correction stored  →  dataset grows
                                                             ↓
        human work decreases  ←  AI improves  ←  model retrained
```

A detector is a commodity. **The Armenian dataset this loop produces is not**, and it is
the asset that gets harder for anyone else to replicate with every drive.

## Using it

```bash
# 1. Process a survey — evidence images are written automatically alongside the db
roadeye process surveys/my_drive --db yerevan.db --model models/road_damage/armenia_v001

# 2. Review
roadeye review --db yerevan.db
#    → open the forwarded port (Codespaces) or http://127.0.0.1:8010

# 3. Turn the decisions into training data
roadeye export-dataset --db yerevan.db --output data/datasets/armenia_v002

# 4. Retrain on it
python3 ml/training/train.py --dataset data/datasets/armenia_v002 --epochs 40
```

## The review screen

Built around one number: **seconds per decision**. A reviewer works through hundreds of
defects, so every extra click multiplies.

| Key | Action |
|---|---|
| `A` | Approve — a real defect |
| `R` | Reject — a false positive |
| `1`–`4` | Wrong class → pothole / alligator / longitudinal / transverse |
| `Q` `W` `E` | Severity low / medium / high |
| `S` | Skip for now |
| `N` | Add a note |
| `←` `→` | Move through the queue |

The whole queue loads in one request and the next image preloads while you look at the
current one — waiting on the network between decisions is what makes review feel slow.

Each defect shows the full frame with the detection outlined, a close-up crop, and its
provenance: model, processing run, source frame, position and uncertainty.

## The three signals, and why rejections matter most

| Decision | Becomes | Why it matters |
|---|---|---|
| **Approve** | A positive example | Ordinary training signal |
| **Change class** | A corrected positive | Worth more than an approval — a case the model got wrong in a specific, learnable way |
| **Reject** | A **hard negative** | The model saw a manhole, shadow, tar repair or wet patch and called it damage |

That third row is the one people skip. `docs/ML_STRATEGY.md` warns that a dataset of
only beautiful potholes produces a model that has learned *"dark irregular blob =
pothole"* — which demos brilliantly and fails on the first real drive. Rejections are
the cure, and they arrive free as a by-product of review.

Skipping a defect is not a decision and produces nothing. That is deliberate: an
uncertain reviewer should skip rather than guess, because a wrong label is worse than a
missing one.

## Rules the code enforces

**Only reviewed defects are exported.** Training on the model's own unreviewed output
teaches it to agree with itself, which makes its errors permanent.

**The human's class beats the model's.** The detection carries the box; the defect
carries the corrected class. Exporting the model's own label would discard the
correction.

**The clean frame is used, never the annotated one.** Evidence is saved three ways —
`_frame.jpg` (clean, for training), `_context.jpg` (with the box drawn, for review) and
`_crop.jpg` (close-up). Training on the context image would teach the model to find red
rectangles.

**Splits are survey-disjoint.** No drive appears in two splits. With only one survey
everything goes to train and the manifest says so — a single drive cannot produce an
honest held-out set, and pretending otherwise is worse than an empty test split.

**Reviews are append-only.** The defect shows current state; the log shows how it got
there, with before and after values. A municipal record whose history can be
overwritten is not auditable.

**The exported dataset is ours.** `distribution_allowed: true`, unlike anything
RDD2022-derived. A model trained solely on reviewed Armenian data is unencumbered by
BLOCKING-1 — which is the whole reason for collecting it.

## Two bugs this work surfaced

Recorded because both were silent, and silent bugs in a review loop destroy exactly the
data the loop exists to collect.

**A column missing from the storage upsert.** `damage_class` was absent from the
`ON CONFLICT DO UPDATE` clause, so a reviewer correcting a misclassified defect got a
success response *and* an entry in the review log — while the defect kept its original
class. The single most valuable output of review was being thrown away with no error.
Now covered by a test that walks every mutable field, so the next forgotten column
fails immediately.

**A cross-field invariant fired mid-update.** Setting `severity` and `severity_source`
one at a time briefly constructs a defect with an assessed severity and no source,
which the domain model rejects — correctly. The fix was to apply both atomically and
re-validate, not to weaken the rule.

## Not built yet

- **Face and plate blurring.** Evidence images are the only part of the defect database
  carrying personal data. Until redaction exists (M5) they must not leave the local
  machine — `docs/PRIVACY.md`.
- **Authentication.** The review server binds to localhost and has none. It must not be
  exposed to a network.
- **Multi-reviewer workflows** and inter-rater agreement measurement. `PILOT_PLAN.md`
  notes that disagreement between two humans sets a realistic ceiling on what any model
  can be expected to achieve.
