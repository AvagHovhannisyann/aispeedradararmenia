# ADR-004 — Detector behind a Protocol

**Status:** Accepted (2026-08-22)

## Context

We do not yet know which detector will win. torchvision is the safe baseline; RTMDet is
the likely shipping model; RT-DETR variants are the current frontier; on-device
inference may later mean Core ML or LiteRT. Meanwhile RDD2022's licence is contested
(`LICENSE_AUDIT.md`), so even the *training data* may have to change.

## Decision

The domain layer depends on a **`RoadDamageDetector` Protocol** and nothing else.
Concrete adapters implement it. **Framework types never cross the boundary** — a
`torch.Tensor` in a domain model is a bug.

## Alternatives considered

- **Depend on one framework directly.** Simpler today; makes every listed uncertainty
  expensive to resolve later.
- **A heavyweight plugin system.** Over-engineered for four implementations.

## Consequences

Good:
- Being wrong about the detector costs **one adapter**, not a rewrite of tracking,
  clustering, geolocation, storage and review.
- `FakeDetector`, `ScriptedDetector` and `NullDetector` make the entire pipeline
  testable with no GPU, no weights and no network.
- On-device inference later reuses the same domain logic.

Bad:
- A thin conversion layer per adapter.
- Framework-specific optimisations need explicit accommodation.

## Revisit when

A detector's capabilities cannot be expressed as `predict(frame) -> list[RawDetection]`
— for example if temporal or multi-frame models become central.
