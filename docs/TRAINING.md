# RoadEye — Training a Detector

**Status:** pipeline implemented and exercised. A bootstrap model has been trained on
Czech RDD2022 data as a proof of the chain — **not** a usable Armenian detector.

## The short version

```bash
# 1. Fetch data (only the country you want — see below)
python3 ml/datasets/rdd2022.py            # or call fetch_country() directly

# 2. Train
python3 ml/training/train.py --dataset data/datasets/rdd2022_czech --epochs 10

# 3. Evaluate on a held-out split
python3 ml/evaluation/evaluate.py \
    --model models/road_damage/rdd_bootstrap_v001 \
    --dataset data/datasets/rdd2022_czech --split test

# 4. Look at what it found
roadeye detect path/to/images --model models/road_damage/rdd_bootstrap_v001 \
    --output annotated/
```

## Getting RDD2022 — read this before downloading 13 GB

Three things discovered by trying, all of which cost time if you find them yourself:

**The per-country links in the authors' README are dead.** The S3 bucket
(`bigdatacup.s3.ap-northeast-1.amazonaws.com`) returns `AccessDenied`. Only the
Figshare DOI record still serves the data.

**Figshare serves one 13.3 GB archive** — and it is a ZIP *of ZIPs*, one per country,
stored uncompressed. `ml/datasets/remote_zip.py` exploits that: Figshare supports HTTP
range requests, so a single country can be read out of the archive without downloading
it. Fetching 900 Czech images costs ~65 MB and about 12 seconds.

**Norway is 10.6 GB of the 13.3 GB.** The other six countries together are ~2.6 GB.

| Country | Size | Notes |
|---|---:|---|
| China_Drone | 0.16 GB | Aerial viewpoint — a *different* problem from windscreen video |
| China_MotorBike | 0.19 GB | |
| **Czech** | **0.26 GB** | Smallest windscreen set — good for a first run |
| United_States | 0.44 GB | |
| India | 0.53 GB | |
| **Japan** | **1.07 GB** | Largest well-annotated windscreen set |
| Norway | 10.61 GB | 80% of the archive. Skip unless you need it |

```python
import sys

sys.path.insert(0, "ml/datasets")
from rdd2022 import fetch_country

fetch_country("Czech", "data/datasets/rdd2022_czech", limit=900)
```

`limit` caps the image count, which is how you try the whole chain in under a minute.

## Measured result: the first bootstrap run failed

Recorded here because an unrecorded failure gets repeated, and because nobody should
pick up this repository assuming the bundled model works. **It does not.**

**Run:** `rdd_bootstrap_v001` — 630 Czech training images, 3 epochs, batch 4, 384 px,
SGD lr 0.005, CPU, 35 minutes.

| Epoch | Mean loss |
|---:|---:|
| 1 | 0.1767 |
| 2 | 0.0911 |
| 3 | 0.0910 |

**Evaluation on the held-out test split** (135 images, 92 ground-truth defects,
IoU 0.5, score threshold 0.05):

```
class                  GT  pred   TP   FP   FN   prec    rec     F1     AP
alligator_crack         8     0    0    0    8      -  0.000      -  0.000
longitudinal_crack     46     0    0    0   46      -  0.000      -  0.000
pothole                16     0    0    0   16      -  0.000      -  0.000
transverse_crack       22     0    0    0   22      -  0.000      -  0.000
                                                    mAP@0.5  0.000
```

**Zero detections. F1 = 0. The detector detects nothing.**

### Diagnosis

The model collapsed to the trivial solution: predict background everywhere.

Two measurements support that rather than a plumbing fault. The loss **plateaued**
between epochs 2 and 3 (0.0911 → 0.0910) — it had settled into a minimum. And the
maximum raw confidence *fell* as training progressed:

| After | Max raw score on held-out images |
|---|---:|
| Epoch 1 | 0.037 |
| Epoch 3 | 0.010 |

It became more confident that nothing is there. That is what "predict background" looks
like from the outside.

Why it happened: 543 boxes across 900 images, of which **562 images contain no damage
at all**. With positives that sparse and only 3 epochs, "detect nothing" is a strong
local minimum — it scores well on 62% of the data immediately.

### What should fix it, in order

1. **Train far longer.** Three epochs is nothing for detection. 30-50 on a GPU.
2. **More data.** Use Japan (1.07 GB, the largest well-annotated windscreen set) rather
   than Czech, and do not cap `limit`.
3. **Rebalance early training.** `RoadDamageDataset(..., drop_negatives=True)` for the
   first epochs gets the model past the trivial minimum, then reintroduce negatives —
   they are essential for precision (`docs/ML_STRATEGY.md`), just harmful as 62% of a
   tiny dataset at the very start.
4. **Lower the learning rate** to 0.001-0.002 if collapse recurs.

None of these need new code — they are flags on the existing script.

### Why this is recorded rather than quietly retried

`CLAUDE.md` forbids claiming performance without measurement. The measurement says
zero. Publishing the zero is the same discipline as publishing a good number would be,
and it establishes the baseline that the next run has to beat.

## Where to actually train

**Not on a laptop CPU.** For calibration, measured on this project's 4-core CPU
container: Faster R-CNN + MobileNetV3, 630 images at 384 px, batch 4 — roughly
**4 seconds per step, ~10 minutes per epoch**. Ten epochs is an afternoon, and the
result is still undertrained.

### The founder's machine has a GPU (confirmed 2026-08-23)

```
NVIDIA GeForce GTX 1660 Ti — 6144 MiB, driver 531.79, CUDA 12.1, Windows/WDDM
```

This was not true when the rest of this document was written, and it changes the plan in
one place that matters more than speed. **6 GB is the binding constraint**, not the clock:
Turing without tensor cores, and WDDM reserves some VRAM for the desktop, so budget on
roughly 5 GB usable.

| Service | Quota | Use |
|---|---|---|
| **Local GTX 1660 Ti** | Unlimited, no eviction, no queue | **Anything involving our own data** |
| **Kaggle** | ~30 GPU-h/week, commonly P100 16 GB | Public datasets only, when 16 GB helps |
| **Colab** | No guaranteed GPU; unpublished, varying limits; ~12 h session cap | Backup |

**The decision rule is data provenance, not speed.** RDD2022 is already public, so train
it wherever is convenient — Kaggle's P100 has more headroom and is genuinely faster.
Armenian survey footage may never be uploaded to either service (`docs/PRIVACY.md`,
`docs/COST_LEDGER.md`), which until now left M7 — the model that actually ships — with no
compute story better than a laptop CPU.

**The 1660 Ti is therefore the enabling hardware for M7, not a convenience.** It is the
only compute available to this project that is both adequate and privacy-compliant for
training on Armenian data.

### Setting it up on Windows

```powershell
.venv\Scripts\pip install -e '.[vision]'
.venv\Scripts\python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

**On Windows this prints `False` the first time, every time.** PyPI's default `torch`
wheel for Windows is CPU-only — unlike Linux, where it bundles CUDA. Nothing is broken;
the GPU build has to be asked for by name:

```powershell
.venv\Scripts\pip install --force-reinstall torch torchvision --index-url https://download.pytorch.org/whl/cu126
```

Two constraints decide which index, and getting either wrong wastes a 2 GB download:

**Your Python version must have wheels there.** Measured 2026-08-23, Windows:

| Index | CUDA | Python versions with wheels |
|---|---|---|
| `cu121` | 12.1 | 3.8 – 3.12 |
| `cu124` | 12.4 | 3.8 – 3.13 |
| `cu126` | 12.6 | 3.9 – **3.14** |
| `cu128` | 12.8 | 3.9 – **3.14** |
| `cu130` | 13.0 | 3.10 – 3.14 |

The founder's machine runs **Python 3.14**, so `cu121` and `cu124` have nothing to
install and pip fails with "no matching distribution" — which reads like a broken command
rather than a version mismatch, so it is written down here.

**Your driver must support that CUDA major version.** Driver 531.79 is a CUDA 12.1
driver. CUDA *minor* version compatibility means it runs any 12.x build, so `cu126` and
`cu128` are fine; **`cu130` is CUDA 13 and will not work** without a much newer driver.

That leaves `cu126` as the conservative pick: newest CUDA 12.x that is well clear of the
driver's floor. If it misbehaves, **update the NVIDIA driver** rather than hunting wheels
— Turing is still supported, it is free, and a current driver removes this whole table as
a consideration.

Verify after installing. `torch.cuda.is_available()` must print `True` before any
training run, because torch will otherwise fall back to CPU silently and the only symptom
is an epoch that takes an hour.

### Batch size on 6 GB

The command further down this page (`--batch-size 8 --max-size 640`) was written for a
P100 with 16 GB and **will likely run out of memory on 6 GB.** Start here instead:

```powershell
.venv\Scripts\python ml\training\train.py --dataset data\datasets\rdd2022_japan `
    --epochs 30 --batch-size 4 --max-size 640 --device cuda
```

If that OOMs, drop to `--max-size 512` before dropping the batch size: resolution costs
memory quadratically and small road damage is exactly what disappears first when you
shrink the image. If it fits comfortably, `--batch-size 6` is worth trying.

**These are starting numbers, not measurements** — the same status as the map-matching
thresholds. Replace them with what actually fits, and record it here.

The training script checkpoints **every epoch** and resumes automatically, because
free sessions are evicted without warning. That is not defensive coding for its own
sake — an un-resumable run loses a night's training the first time Colab reclaims the
VM.

```bash
python3 ml/training/train.py --dataset data/datasets/rdd2022_japan \
    --epochs 30 --batch-size 8 --max-size 640 --device cuda
```

Resuming is the default; pass `--no-resume` to start clean.

**Never upload Armenian survey video to these services** (`docs/PRIVACY.md`). RDD2022
is already public; your own footage is not.

## What the numbers mean

For calibration: the best multi-country ensemble at CRDDC'2022 reached roughly
**F1 0.76**. Road damage detection across varying countries, cameras and conditions is
genuinely hard. A first bootstrap scoring far below that is a normal starting point.

`evaluate.py` reports precision, recall, F1 and average precision per class, **with the
counts behind them**. Watch the counts: a class with 6 ground-truth instances can show
a dramatic percentage that means nothing.

Two things it refuses to do:

- **Evaluate on the training split.** That measures memorisation. It errors rather than
  warning, because a warning does not survive the trip into a slide deck.
- **Hide the denominator.** Every rate is printed next to its numerator.

## Leakage: the mistake that fakes success

RDD2022 images are numbered in capture order along a drive, so `Czech_000101` and
`Czech_000102` are metres apart and may show the same crack. A random train/test split
puts near-duplicates on both sides and produces excellent, meaningless metrics.

`contiguous_splits()` therefore splits into **contiguous index blocks per country**, and
a test asserts the train and test index ranges do not overlap. For Armenian data we
will do better — genuine route-disjoint splits, since we will know the routes (ADR-008).

## Licence quarantine

Anything trained on RDD2022 is marked `distribution_allowed: false` **automatically**,
inherited from the dataset manifest rather than set by hand. RDD2022's licence is
published two contradictory ways (Figshare: CC BY 4.0; the authors' repository:
CC BY-SA 4.0), and under the share-alike reading, distributing a derived model could
oblige us to give the model away.

So an RDD-derived model is for **internal evaluation and pseudo-labelling only**. The
shipping model must be trained on Armenian data we own. See `docs/LICENSE_AUDIT.md`
BLOCKING-1 and `docs/ML_STRATEGY.md`.

`roadeye process --model ...` prints a reminder whenever it loads a quarantined model.

## The honest limitation

A model trained on Czech or Japanese roads will underperform on Yerevan. This is not a
guess — it is the finding that caused RDD to become multi-national in the first place:
models trained predominantly on Japanese roads degraded substantially on Indian and
Czech roads.

The bootstrap model's real job is to make **labelling Armenian data faster** by
proposing boxes a human corrects (M4). The corrections become the Armenian dataset, and
that dataset — not this model — is the product.
