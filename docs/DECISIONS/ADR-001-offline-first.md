# ADR-001 — Offline-first processing

**Status:** Accepted (2026-08-22)

## Context

The obvious "impressive" design runs road-damage detection live on the phone. It is
also the design that makes a zero-budget project hardest: the collector would have to
capture video, run a neural network, geolocate defects, render overlays and maintain a
stable recording simultaneously, on a device that is also navigating and overheating.

## Decision

**The phone collects evidence. The laptop does the thinking.**

The collector records video + GPS into a survey bundle. All analysis happens offline in
Python on a laptop.

## Alternatives considered

- **Real-time on-device inference.** Rejected for MVP: highest engineering cost, blocks
  model iteration behind app releases, and provides no capability the offline path
  lacks at this stage.
- **Cloud processing.** Rejected: costs money, requires uploading video that may
  contain personal data (`PRIVACY.md`), and needs connectivity we do not need.

## Consequences

Good:
- The collector is simple enough to build and debug quickly.
- The model can change without touching the phone.
- Training uses free laptop/Kaggle/Colab compute.
- The whole pipeline is testable with no GPU, no ffmpeg and no network.

Bad:
- No live feedback to the driver during a survey.
- Results arrive after the drive, not during it.

Neutral:
- Moving inference on-device later does not require redesigning the product — the
  detector Protocol (ADR-004) already isolates it.

## Revisit when

A customer requires live in-vehicle feedback, or surveys become frequent enough that
offline turnaround is the bottleneck.
