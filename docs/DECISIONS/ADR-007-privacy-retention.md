# ADR-007 — Privacy-first retention and anonymisation

**Status:** Accepted (2026-08-22). Legal basis **OPEN** — see `PRIVACY.md`.

## Context

Windshield video of Yerevan captures faces, licence plates, homes and people entering
buildings. A road-condition dataset is incidentally a **movement record of identifiable
people**. Armenia's Law on Protection of Personal Data (HO-49-N, 2015) applies, and the
regulatory landscape is actively changing.

Commercial precedent exists: Vaisala anonymises every RoadAI video before it appears in
their web app, and the RDD2022 authors blurred faces and plates before public release.
Anonymisation and defect detection are compatible.

## Decision

Privacy is architectural, not a later feature:

1. Raw video is processed **locally** and never auto-uploaded.
2. Faces and plates are blurred in anything retained beyond the raw file.
3. Raw video has the **shortest retention** of any artefact.
4. The defect database contains **no personal data** — coordinates, class, confidence,
   uncertainty.
5. RoadEye performs **no** face recognition, person re-identification, ALPR, plate
   lookup or automated enforcement.

## Alternatives considered

- **Retain everything, restrict access.** Rejected: retention is the risk; access
  control is a mitigation, not a substitute.
- **Anonymise later, once we have customers.** Rejected: it would be retrofitted under
  deadline pressure onto data already collected.

## Consequences

Good:
- The high-risk surface is small and controllable — evidence images, not "all footage".
- Compatible with a municipal customer's own obligations.
- Forecloses the enforcement pivot that would change the company's legal posture.

Bad:
- Blurring is engineering work not yet done (M5).
- **Until it exists, survey video may not leave the founder's machine** — not in demos,
  not in PRs, not in a pitch deck.
- Raw video cannot be uploaded to free GPU services for training.

## Open

Lawful basis (L-4) and retention period (L-5) require Armenian counsel and/or the
Personal Data Protection Agency.
