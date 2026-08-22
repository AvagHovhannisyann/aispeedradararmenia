# ADR-002 — Expo/React Native collector for MVP

**Status:** Accepted (2026-08-22)

## Context

The collector must reach a real Yerevan road quickly. Native iOS (AVFoundation) and
native Android (CameraX) both offer better capture control — notably iOS camera
intrinsics, which unlock accurate defect geolocation (`GEOLOCATION.md`). Neither is
needed to prove the concept.

## Decision

Build the MVP collector with **Expo + React Native + TypeScript**, foreground-only.

Critically: **the survey bundle format, not the app, is the contract.** A future native
collector is compatible when `roadeye validate` accepts its output.

## Alternatives considered

- **Native iOS first.** Better capture metadata, but doubles time-to-first-drive and
  adds Apple's 7-day free-provisioning reinstall cycle to every iteration.
- **Native Android first.** CameraX is genuinely good and Android sideloading is
  frictionless — a reasonable choice, deferred rather than rejected.
- **Background recording.** Rejected for MVP: iOS needs a development build, Always
  authorization and background modes. Keeping the app foregrounded removes an entire
  class of bugs for zero product cost.

## Consequences

Good:
- One codebase, both platforms, fast iteration.
- No native toolchain to fight on day one.
- Foreground-only sidesteps iOS background-execution complexity entirely.

Bad:
- Less precise capture metadata; no camera intrinsics.
- The app must stay open for the whole survey.
- iOS testing carries the 7-day provisioning cycle (prefer Android early).

Mitigation:
- `BUNDLE_SCHEMA_VERSION` is asserted identical on both sides by
  `tests/integration/test_collector_contract.py`, so the two ends cannot silently drift.

## Revisit when

Camera intrinsics are needed for ray-to-ground projection (M6), or field use shows
foreground-only recording is impractical.
