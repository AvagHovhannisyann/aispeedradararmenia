# ADR-008 — Route-disjoint ML evaluation splits

**Status:** Accepted (2026-08-22)

## Context

Video frames sampled from one drive are near-duplicates. A random train/test split puts
frame 102 in test while frames 100, 101 and 103 — almost the same photograph — are in
training. The resulting metrics look excellent and mean nothing.

This is the single easiest way to spend months believing a broken system works.

## Decision

**Split by route, survey and date. Never by frame.** The test set must consist of
completely held-out routes, ideally from a different date and, where possible, a
different device and mount.

`DatasetVersion.split_routes` stores route ids per split, so the split is auditable
rather than a claim in a README.

## Alternatives considered

- **Random frame split.** Rejected: leakage.
- **Random split with a temporal buffer.** Better, but still leaks across a route driven
  twice, and is harder to reason about than a clean route split.

## Consequences

Good:
- Metrics measure generalisation to **new roads**, which is the actual product question.
- Directly addresses the domain-shift problem the RDD literature documents.

Bad:
- Requires more distinct routes before evaluation is meaningful.
- Reported numbers will be **lower** than a leaky split would show. This is the point.

## Enforcement

Dataset construction must record route ids per split. Any evaluation that cannot name
its held-out routes is not a valid evaluation.
