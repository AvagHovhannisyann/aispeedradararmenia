# ADR-006 — Human verification before municipal action

**Status:** Accepted (2026-08-22)

## Context

Road distress detection is not solved: the best multi-country ensemble at CRDDC'2022
reached roughly F1 ≈ 0.76. Shadows, manholes, wet asphalt and tar repairs all resemble
potholes. A municipality acting directly on unverified model output would eventually
dispatch a crew to a shadow — and stop trusting the system permanently.

## Decision

**Only a human may move a defect beyond `PROBABLE`.** No automated path writes
`VERIFIED`. Severity is human-assigned in MVP, and every severity records its source.

## Alternatives considered

- **Auto-verify above a confidence threshold.** Rejected: confidence measures the
  model's certainty, not its correctness, and confident errors are the normal failure
  mode of a shadow-vs-pothole classifier.
- **No review step.** Rejected: makes the output unusable for real work.

## Consequences

Good:
- Municipal trust is protected — the expensive, slow-to-rebuild asset.
- Every correction becomes training signal (`ML_STRATEGY.md`).
- Reports can honestly separate "AI probable" from "human verified".

Bad:
- Human review time is required per defect.
- Throughput is bounded by reviewer capacity.

Mitigation:
- Review UX optimised for seconds-per-decision; the highest-confidence view of each
  defect is surfaced first.

## Enforcement

`Defect.status` defaults to `PROBABLE`; the pipeline never writes another value.
Enforced by `tests/e2e/test_pipeline.py::TestHonestOutputs`.
