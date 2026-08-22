# RoadEye — Pilot Plan

**Status:** design. **No pilot has been run. No Armenian data exists.**

## Purpose

To answer one question honestly:

> **How does RoadEye compare to a human inspector walking the same road?**

Not "does the demo look impressive". A municipality will ask the comparison question
within the first meeting, and having run it ourselves — including the parts that went
badly — is the difference between a vendor and a student project.

## Design

### 1. Choose the route before seeing any results

- 3-8 km of ordinary Yerevan streets — not a curated worst-case road.
- A mix: a main corridor, a residential street, a recently resurfaced stretch.
- Fixed and written down **before** any survey, so the route cannot be adjusted to
  flatter the system.

### 2. Build the human ground-truth inventory first

**Before** running RoadEye on it. Two people walk the route and record every visible
defect: position (phone pin), class, rough size, photo.

Ground truth is built first so it cannot be contaminated by knowing what the machine
found. This ordering is the whole experiment; reversing it produces a confirmation
exercise.

Where the two humans disagree, record the disagreement rather than resolving it away —
**inter-rater disagreement is itself a result**, and it sets a realistic ceiling. If two
inspectors agree only 80% of the time on what counts as a pothole, expecting 95%
machine agreement is incoherent.

### 3. Drive the survey

Per `COLLECTION_PROTOCOL.md`. Daylight, dry, normal traffic. Record the mount
calibration.

### 4. Drive it a second time

Same day, ideally 1-2 hours later. This gives repeat-survey consistency, which is the
metric a sceptical city engineer will actually propose.

### 5. Process and review blind

Process both surveys. A reviewer approves/rejects/corrects **without** the ground-truth
list in front of them, so review is not steered toward the answer.

### 6. Compare

```
HUMAN INVENTORY          ROADEYE INVENTORY
      │                          │
      └──────── match by position + class ────────┘
                        │
      ┌─────────────────┼─────────────────┐
   MATCHED          MISSED            FALSE POSITIVE
 (both found)   (human only)         (RoadEye only)
```

Matching rule, fixed in advance: same class within **20 m** counts as a match. That
threshold reflects V1 location error (`GEOLOCATION.md`) and must be stated up front —
choosing it after seeing results is how honest comparisons become dishonest ones.

Every "false positive" is inspected individually. Some will turn out to be real defects
the humans missed — a genuine result, and one worth reporting.

## What gets measured

All system metrics from `METRICS.md`, plus:

| Question | Measure |
|---|---|
| Does it find real defects? | Matched ÷ human inventory |
| Does it waste inspector time? | False positives per km |
| Can a crew find what it reports? | Median location error |
| Is one defect one item? | Duplicate rate (both directions) |
| Is it reproducible? | Agreement between drive 1 and drive 2 |
| Does it save labour? | Median review seconds vs walking survey hours |
| Where does it fail? | Categorised failure list |

## Reporting rules

1. **Report misses as prominently as finds.** A pilot report that omits what the system
   failed to see is not a pilot report.
2. **Never merge probable and verified counts.**
3. Report location error as a **distribution** (median, 90th percentile), not a mean —
   the tail is what sends a crew to the wrong street.
4. Include the inter-rater disagreement, so machine performance is read against a real
   ceiling.
5. Publish the failure categories: shadow, manhole, wet patch, occlusion, distance.

## Pre-pilot gates

Do not run the pilot until all of these hold. Skipping any produces an
uninterpretable result.

| Gate | Question |
|---|---|
| Capture | Can one person record a 30-minute survey without corruption? |
| Sync | Does every analysed frame have a plausible time and position? |
| Vision | Are obvious potholes detected on **unseen** Yerevan streets? |
| False positive | Are manholes, shadows and patches mostly rejected? |
| Dedup | Does one physical pothole become one defect? |
| Mapping | Is the defect on the correct road segment? |
| Review | Can a human decide in a few seconds? |
| Repeatability | Does a second drive give a comparable inventory? |
| Demo | Does a non-technical person understand the dashboard unaided? |
| **Privacy** | Does face/plate blurring exist for anything shown outside the laptop? |

The privacy gate is a hard blocker for anything shown to a third party, including a
pitch deck screenshot.

## What "ready to approach a municipality" looks like

Not "I have an AI idea". Rather, opening a laptop and saying:

> "This is an ordinary smartphone. I mounted it in a normal car. Yesterday I drove
> these streets. The computer processed the drive. Here is what it found — and here is
> what it missed."

Then clicking a marker and showing: the original photograph, the AI's class and
confidence, the GPS position **with its uncertainty**, the date, the model version, and
the human verification state.

Then:

> "Give us one municipal vehicle and one district. Let us test it against your
> inspectors."

The offer to be measured against their own inspectors is the credible part. It is also
only offerable by someone who has already measured themselves.

## Risks to the pilot itself

| Risk | Mitigation |
|---|---|
| Route chosen to flatter the system | Fix the route in writing before any survey |
| Ground truth contaminated by model output | Build the human inventory first, always |
| Matching threshold tuned after the fact | Fix 20 m in advance, in this document |
| Reviewer steered by knowing the answer | Blind review |
| One good drive presented as typical | Report both drives, including the worse one |
| Weather/lighting cherry-picked | State conditions; scope claims to them |
