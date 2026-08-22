# ADR-009 — Reject Ultralytics; quarantine RDD2022 lineage

**Status:** Accepted (2026-08-22)

## Context

Two licensing findings from M0 research shape what RoadEye may ship.

**Ultralytics YOLO** — the framework nearly every road-damage tutorial uses — is
AGPL-3.0 by default. The licence covers the training code *and the models it produces*,
and AGPL's network clause reaches software served over a network. A hosted municipal
dashboard is exactly that. Compliance would mean publishing the complete corresponding
source of the whole product. An Enterprise licence removes the obligation, for money we
do not have.

**RDD2022** is published under two contradictory licences by the same authors: the
Figshare DOI record says **CC BY 4.0** (attribution only), while the authors' GitHub
repository says **CC BY-SA 4.0** (share-alike). Whether trained weights are "Adapted
Material" under CC BY-SA is legally unsettled and jurisdiction-dependent. Under the
strict reading, distributing an RDD-derived model could oblige us to give it away.

## Decision

1. **Ultralytics is rejected for the shipping path.** It appears in no dependency
   manifest. Permissive alternatives exist: torchvision (BSD-3), RTMDet via MMDetection
   (Apache-2.0), RT-DETR variants (mostly Apache-2.0).
2. **Assume the stricter RDD2022 licence (CC BY-SA 4.0)** until the authors clarify in
   writing. Models with RDD2022 in their lineage are marked
   `distribution_allowed=False` and used only for internal evaluation and
   pseudo-labelling.

## Alternatives considered

- **Use Ultralytics and open-source everything.** A legitimate business model, but not
  the stated one.
- **Assume the permissive Figshare licence.** Rejected: assuming the convenient reading
  of a contradiction is how a company discovers a problem after it matters.
- **Avoid RDD2022 entirely.** Unnecessarily slow — it is fine for bootstrapping a
  labelling assistant, which is all we need it for.

## Consequences

Good:
- The commercial path stays open with no licence contamination.
- The mitigation (train the shipping model on owned Armenian data) is the business goal
  anyway, so the constraint costs nothing strategically.

Bad:
- Cannot use the most convenient tutorials.
- An extra provenance field to maintain per model.

## Enforcement

`ModelVersion.distribution_allowed` defaults to `False` and raises if set `True` without
stated `training_data_licenses`. Tested in
`tests/unit/test_domain_models.py::TestModelVersionProvenance`.

## Revisit when

The RDD2022 authors clarify the licence in writing (tracked as L-1/L-2 in
`LICENSE_AUDIT.md`).
