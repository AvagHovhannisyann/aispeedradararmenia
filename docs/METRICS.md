# RoadEye — Metrics

**Status:** definitions agreed; **no measurements exist.** Every number produced before
M8 is synthetic.

## The distinction that matters

> **Model metrics measure the neural network. System metrics measure the product.**
> A municipality cares only about the second.

Saying *"our mAP@0.5 is 0.73"* to a city engineer communicates nothing. Saying
*"we drove 12 km, found 47 real defects, missed 6, and reported 3 things that weren't
there"* is a business conversation.

Both are tracked. Only one goes in a pitch.

## Model metrics (frame level)

Standard, computed on a **route-disjoint** held-out test set (`ML_STRATEGY.md`):

- precision, recall, F1 — per class and overall
- mAP@0.5, mAP@0.5:0.95
- confusion analysis: which classes are mistaken for which
- error analysis by condition: lighting, weather, speed, image quality

**Reference point:** the best multi-country ensemble at CRDDC'2022 reached roughly
**F1 ≈ 0.76**. That is the bar for a hard problem, not 0.99. A first Armenian model
scoring 0.5 is a normal starting point, not a failure.

## System metrics (defect level) — the ones that decide the pilot

These are computed against a **human-verified ground-truth inventory** of a known route.

| Metric | Definition | Why it matters |
|---|---|---|
| **True defects per km** | Verified defects found ÷ km surveyed | The value delivered |
| **False positives per km** | Rejected defects ÷ km | The cost imposed on inspectors |
| **Missed defects** | Ground-truth defects with no RoadEye defect | The credibility risk |
| **Duplicate rate** | Defects mapping to the same physical defect | Dashboard usability |
| **Location error** | Distance from reported to true position | Can a crew find it? |
| **Human approval rate** | approved ÷ reviewed | Is output trustworthy? |
| **Review time per defect** | Median seconds to decide | Does this save labour? |
| **Processing time per km** | Wall-clock ÷ km | Is it operationally viable? |
| **Repeat-survey consistency** | Agreement between two drives of one route | Is it reliable? |

### The two that are easiest to fool yourself about

**Duplicate rate** has two failure directions, and only measuring one is how a system
looks good and is useless:
- *Under-merging*: one pothole reported 20 times → dashboard unusable.
- *Over-merging*: 20 potholes reported as one → **the street looks fine**. Worse,
  because it is invisible.

Both are pinned in `tests/unit/test_clustering.py`, after over-merging was found in the
first real run.

**Repeat-survey consistency** is the metric a sceptical city engineer will actually
propose. If two drives an hour apart disagree substantially, nothing else matters. It
should be measured before anyone claims the system works.

## Honest reporting rules

1. **Never merge "probable" and "verified" into one number.** Report both.
   `summarize()` keeps them separate by construction.
2. **Always report location uncertainty** alongside coordinates. Exports include it in
   the same row.
3. **Report the denominator.** "47 defects" is meaningless without km surveyed.
4. **Report what failed**, not only what worked — night, rain, shadow, wet asphalt.
5. **Never report a metric from synthetic data as a result.** The fake detector's output
   describes nothing about any road; the CLI prints a warning saying so.

## The demo table (post-M8)

What a municipal meeting should actually see:

```
Survey distance            12.4 km
Processing time            18 min
Probable defects           147
Human-verified defects     121
  potholes                  84
  alligator cracking        23
  longitudinal cracks       14
Rejected as false            26
Missed (vs manual survey)     6
Median location error      11 m
Median review time          4 s
```

Every one of those cells is currently empty. Filling them honestly is what M8 is for.

## Anti-metrics

Things that will be tempting and must not be used as evidence:

- **Screenshot quality.** A curated grid of clean detections proves nothing.
- **Total detections.** Rewards the duplicates we work to eliminate.
- **Confidence scores as accuracy.** A model can be confidently wrong; that is the
  normal failure mode of a shadow classifier.
- **Training-set performance.** Only route-disjoint held-out results count.
