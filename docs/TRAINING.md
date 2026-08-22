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
import sys; sys.path.insert(0, "ml/datasets")
from rdd2022 import fetch_country

fetch_country("Czech", "data/datasets/rdd2022_czech", limit=900)
```

`limit` caps the image count, which is how you try the whole chain in under a minute.

## Where to actually train

**Not on a laptop CPU.** For calibration, measured on this project's 4-core CPU
container: Faster R-CNN + MobileNetV3, 630 images at 384 px, batch 4 — roughly
**4 seconds per step, ~10 minutes per epoch**. Ten epochs is an afternoon, and the
result is still undertrained.

Use free GPU:

| Service | Quota | Use |
|---|---|---|
| **Kaggle** | ~30 GPU-h/week, commonly P100 16 GB | Primary |
| **Colab** | No guaranteed GPU; unpublished, varying limits; ~12 h session cap | Backup |

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
