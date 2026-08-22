# Experiment: rdd_bootstrap_v001

**Outcome: FAILED — the model detects nothing (F1 = 0, mAP@0.5 = 0.000).**

Kept in the repository because an unrecorded failure gets repeated, and because it is
the baseline the next run has to beat.

| | |
|---|---|
| Data | RDD2022 Czech, 900 images (630 train / 135 val / 135 test) |
| Architecture | Faster R-CNN + MobileNetV3-Large FPN (torchvision, BSD-3) |
| Hyperparameters | 3 epochs, batch 4, 384 px, SGD lr 0.005, seed 1337 |
| Hardware | 4-core CPU, 35 minutes |
| Distributable | **No** — inherits RDD2022's disputed licence |

## What happened

Loss plateaued (0.0911 → 0.0910 between epochs 2 and 3) and maximum raw confidence
*fell* from 0.037 to 0.010. The model converged on predicting background everywhere.

562 of the 900 images contain no damage, so "detect nothing" scores well on 62% of the
data immediately — a strong local minimum for a 3-epoch run on a small set.

Full diagnosis and the fixes to try, in order: `docs/TRAINING.md`.

## Files

- `metadata.json` — full provenance: git commit, dataset hash, hyperparameters, licences
- `evaluation_test.json` — the per-class evaluation on the held-out split

The weights themselves are not committed (`.gitignore`): they are large, licence-
encumbered, and worthless.
