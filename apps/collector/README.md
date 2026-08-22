# RoadEye Collector

An Expo/React Native app that records forward-facing road video with synchronised GPS
and writes a **survey bundle** the offline pipeline can process.

> **This app is not the product.** The *bundle format* is. A future native iOS or
> Android collector is compatible when `roadeye validate` accepts its output. See
> `docs/DECISIONS/ADR-002-expo-collector.md`.

## Status

M1 scaffold. It has **not yet been run on a real device** — see "Before the first
drive" below. Nothing in this repository has been validated against real road footage.

Four bugs were found and fixed by reading it closely, each of which would have cost a
whole survey:

| Bug | What it would have done |
|---|---|
| `stop()` wrote the manifest without awaiting `recordAsync()` | Declared the bundle complete while the video was still in flight. Close the app on "done" and the drive is gone |
| Concurrent flushes into a read-modify-write append | Two overlapping writes, the second silently discarding the first's GPS fixes |
| A fixed 2 GB free-space floor | About 17 minutes of 1080p video, against an acceptance criterion of 30. The phone fills partway through and truncates |
| The permissions button only re-requested the camera | Decline location once and you are stuck on a screen whose only button cannot fix what it is complaining about |

They are pinned by `tests/integration/test_collector_contract.py`. Source-reading is
weaker than running the thing, and much better than nothing.

## Setup

```bash
cd apps/collector
npm install
npm start
```

## Testing it without a phone

The bundle contract lives in `src/bundle.ts`, which has **no `expo-*` imports** — so it
runs under Node with no install at all:

```bash
npm test          # or, from the repo root:
node --experimental-strip-types --test 'apps/collector/tests/*.test.ts'
```

`pytest` runs the same suite (`tests/integration/test_collector_js.py`), skipping it when
Node is absent. One command covers both halves of the contract.

That split matters more here than anywhere else in the repository: this is the one
component that has never run on a device, so it is the one place where a bug is found by
a wasted 30-minute drive rather than by a stack trace. `src/survey.ts` holds the
filesystem calls and is kept as thin and as dumb as possible, because it is the part that
cannot be checked here.

Then open the project in Expo Go (Android) or a development build (iOS).

**Prefer Android for early field testing if you have a device.** Apple's free
provisioning ("Personal Team") expires provisioning profiles after 7 days and caps you
at 3 devices and 10 App IDs, so an iPhone needs reinstalling roughly weekly until
someone pays the $99/year. Android sideloading has no equivalent friction.

## What it produces

```
survey_2026-08-18T10-42-11-123Z_a1b2c3/
├── route.json        # route id, timings, and the recording time anchor
├── locations.jsonl   # one GPS fix per line
├── device.json       # OS, orientation, location accuracy authorization
├── manifest.json     # schema_version + file inventory
└── video.mp4         # the raw evidence
```

Validate a bundle after copying it off the phone:

```bash
roadeye validate path/to/survey_2026-08-18T10-42-11-123Z_a1b2c3
```

## Two things this app gets deliberately right

**1. Locations are appended as JSON Lines, not buffered into a JSON array.**
Phones run out of storage and get force-quit. A truncated JSONL file loses one sample;
a truncated JSON array is unparseable and loses the entire drive.

**2. `recording_start_epoch_ms` is captured once, at recording start, and never
recomputed.** It is the anchor for all downstream time arithmetic. The moment the user
taps START and the moment the camera actually begins are different instants, and
conflating them offsets every defect in the survey — at 50 km/h, one second is ~14 m.

## Deliberate MVP limitations

| Limitation | Why |
|---|---|
| Foreground only — app must stay open | iOS background location needs a development build, Always authorization and background modes. The MVP does not need any of it. |
| No on-device inference | The phone collects evidence; the laptop does the thinking (ADR-001). |
| No upload | Raw video may contain identifiable people and plates. It stays on the device until manually transferred (`docs/PRIVACY.md`). |
| Landscape orientation assumed | Must be confirmed empirically on a real mount before being fixed as protocol. |

## Before the first drive

Read `docs/COLLECTION_PROTOCOL.md`. Mount rigidity and camera angle affect result
quality at least as much as the neural network does, and an inconsistent mount teaches
the model about camera placement instead of about road damage.

Quick checklist:

- Phone rigidly mounted, rear camera, 1x lens (no zoom switching)
- Windshield clean in the camera's field of view
- ≥2 GB free storage and a charged/charging phone
- Daylight, dry road for MVP surveys
- Start recording **before** moving; stop **after** stopping
