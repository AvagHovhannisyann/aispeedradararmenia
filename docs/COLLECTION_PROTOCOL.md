# RoadEye — Field Collection Protocol

**Status:** written from first principles and platform research. **Not yet validated on
a real drive.** Every number here is a starting point to be corrected by measurement.

> Data quality is decided at the windshield, not in the model. An inconsistent mount
> teaches the network about camera placement instead of about road damage.

## Why the mount matters as much as the model

If the camera height, pitch or lens changes between surveys, then:

- the apparent size of a defect changes for reasons unrelated to the defect;
- the model learns spurious correlations with viewpoint;
- future ray-to-ground projection (`GEOLOCATION.md`) is invalidated, silently, because
  the rotation matrix `R` and mount height `h` are assumed constant.

Vaisala's commercial RoadAI likewise expects a smartphone mounted on the windshield of
an ordinary vehicle, with no specialist hardware — the discipline is in the procedure,
not the equipment.

## Mount requirements

```
        windshield
           \
            \   ← phone: rigid mount, rear camera, landscape
             \
              \        field of view
               \      /-------------\
                \    /               \
                 \  /                 \
    ══════════════\/═══════════════════════════ road
         car              pothole
```

| Requirement | Reason |
|---|---|
| **Rigid** mount — no suction cup that creeps, no hand-holding | Vibration blurs frames; drift invalidates calibration |
| **Rear main camera**, not selfie | Resolution and optics |
| **1× lens only** — disable automatic lens switching | Ultra-wide/tele change intrinsics mid-survey |
| **Same position** across repeat surveys | Cross-survey comparison assumes it |
| Angled down enough that road fills the lower half of frame | Ground-projection accuracy degrades with distance |
| Windshield clean **in the camera's field of view** | Smears become permanent false features |
| Landscape orientation | Wider road coverage — *verify empirically before fixing* |
| Not obstructing the driver's view | Legal and safety |

### Measure and record these once per mount setup

Written into `calibration.json` in the survey bundle:

```json
{
  "mount_height_m": 1.25,
  "camera_pitch_deg": -12.0,
  "lens": "1x",
  "orientation": "landscape",
  "vehicle": "founder-car-01",
  "measured_at": "2026-08-18T09:00:00Z",
  "method": "tape measure to camera lens centre; phone inclinometer"
}
```

A tape measure and the phone's own inclinometer are sufficient. **No calibration target
or specialist hardware is required for MVP** — and no better method should be adopted
before there is measured evidence that mount error is a limiting factor.

If the mount is moved, **record a new calibration and treat subsequent surveys as a new
configuration**. Silently changing it is worse than not recording it at all.

## Operating conditions (MVP scope)

| Condition | MVP | Why |
|---|---|---|
| Daylight | **Required** | Night is a different visual distribution needing its own data |
| Dry road | **Required** | Wet asphalt mimics potholes — a known hard negative |
| Speed | 20-60 km/h | Too slow wastes time; too fast blurs and thins coverage |
| Rain / snow | Excluded | Expand deliberately once the baseline works |
| Heavy traffic | Avoid | Vehicles occlude the road surface |

Scoping to daylight/dry is not timidity — it is refusing to average over four visual
domains before any of them works.

## Pre-drive checklist

- [ ] Phone charged or charging (video + GPS drains fast)
- [ ] **Enough free storage for the planned drive** — the app shows recordable minutes
      and refuses to start below 10. It estimates 120 MB per minute of 1080p video,
      deliberately pessimistic; a 30-minute survey therefore wants ~4 GB free
- [ ] Mount rigid; phone cannot move when the car hits a bump
- [ ] Windshield clean in the camera's field of view
- [ ] Rear camera, 1× lens, landscape
- [ ] GPS shows **good** or **fair** before starting (poor = survey will be discarded)
- [ ] Route planned; driver briefed
- [ ] Daylight, dry road

## During the survey

```
open RoadEye → tap START (stationary) → drive route → stop → tap STOP
```

**Safety, non-negotiable:** never touch or look at the phone while the vehicle is
moving. The recording screen is a status display, not a control panel; that is why the
app requires no interaction during a drive. If something goes wrong, pull over safely
first — a failed survey costs 30 minutes, and nothing else is worth more than that.

Keep the app foregrounded for the whole drive. Backgrounding it stops the recording
(deliberate MVP scope — ADR-002).

## After the first survey, measure the bitrate

The app's 120 MB/min figure is an **estimate**, chosen at the pessimistic end because
over-estimating costs a warning and under-estimating costs a truncated drive. Replace it
with a measurement as soon as there is one:

```bash
ls -l  <survey>/video.mp4          # bytes
python3 -c "print(<bytes> / 1e6 / <minutes>, 'MB per minute')"
```

Then update `VIDEO_BYTES_PER_MINUTE` in `apps/collector/src/bundle.ts`. This is the first
of several numbers in this repository that are honest guesses until a real drive replaces
them.

## After the survey

1. Transfer the bundle off the phone **manually**. There is no automatic upload, by
   design (`PRIVACY.md`).
2. Validate before trusting it:
   ```bash
   roadeye validate path/to/survey_...
   ```
3. Check the reported GPS fix count and track distance look plausible for the drive.
4. Note anything unusual — roadworks, an unusual detour, a mount bump — in the survey
   record. Context that is not written down is lost.

## Repeat surveys

For trend analysis to mean anything, hold constant: route, direction, mount position,
camera settings, and (ideally) time of day and lighting.

Record deviations rather than hiding them. A survey driven in the opposite direction is
still useful; a survey silently driven in the opposite direction corrupts comparison.

## When recording fails

| Symptom | Action |
|---|---|
| App backgrounded mid-drive | Recording stopped. Restart as a **new** survey; do not try to stitch. |
| Storage full | Survey truncated. Bundle is still partially usable — validate it. |
| GPS shows POOR throughout | Discard. Fixes worse than 25 m are dropped by the processor. |
| Phone overheated / shut down | Stop, cool, restart as a new survey. Note it. |
| Mount slipped | **Stop.** Re-mount, re-measure calibration, start a new survey. |

Partial surveys are not worthless — the bundle format is designed to degrade rather
than fail — but they must be labelled as partial.

## Naming

Survey ids are generated by the app as `survey_<UTC timestamp>_<random>`, which sorts
chronologically in a file listing. Do not rename them: the id is the join key across
the frames, detections, defects and processing-run records.
