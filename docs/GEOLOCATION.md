# RoadEye — Geolocation

**Status:** V0 implemented (interpolated phone GPS). V1.5+ specified, not built.

## The distinction the whole system rests on

> **The phone's GPS reports where the *camera* is. It never reports where the *defect* is.**

A camera mounted on a windshield sees road 5-30 m ahead of the vehicle. Assigning the
current fix to a pothole detected at the top of the frame places it tens of metres from
where a repair crew would need to go — reliably, in a consistent direction, in a way
that looks entirely plausible on a map.

The data model therefore keeps two ideas apart, permanently:

| Concept | Meaning |
|---|---|
| `observation_location` | Where the camera was when the frame was captured. |
| `estimated_defect_location` | Where we believe the defect physically is. |

Every coordinate carries a `LocationMethod` and an `uncertainty_m`. Both are required
fields — a bare lat/lon cannot be constructed. This is enforced in
`roadeye/domain/models.py::GeoPoint` and tested.

## The accuracy ladder

Each rung is a real improvement, and each is independently shippable. We are on rung 2.

| Rung | `LocationMethod` | Typical error | Status |
|---|---|---|---|
| 0 | `PHONE_GPS` | 5-20 m + camera-to-defect offset | fallback only |
| 1 | `INTERPOLATED_PHONE_GPS` | 5-15 m + offset | **implemented** |
| 2 | `ROAD_SEGMENT_MATCHED` | **no better** — but on the correct street | **implemented** (`docs/MAP_MATCHING.md`) |
| 3 | `GROUND_PROJECTED` | 1-3 m (optimistic) | M6+ |
| 4 | `MANUAL_CORRECTION` | as good as the reviewer | implemented (schema) |

Rung 2 is worth spelling out, because it is the one that invites a false claim. Snapping
a fix onto a road centreline does **not** reduce its error: the along-road position is
still only as good as the GPS fix, and we do not know which lane or which side. What it
buys is a *street name*, which is what makes a defect dispatchable. So a matched
coordinate's `uncertainty_m` is never lower than it was, and grows to cover the distance
the point was moved — otherwise the true position could fall outside our own stated
circle.

Deliberately, **rung 3 is not on the MVP path.** Building inverse-perspective geometry
before a single real drive exists would be months of work against unmeasured error.

## V0/V1 — interpolation (implemented)

Given a detection at video time `t_v`:

```
t_abs = recording_start_epoch_ms + t_v * 1000
```

Find the bracketing fixes `P₀` at `T₀` and `P₁` at `T₁`, then

$$\alpha = \frac{t_{abs} - T_0}{T_1 - T_0}, \qquad P(t) = P_0 + \alpha (P_1 - P_0)$$

Implementation notes that are not obvious:

- **Heading is interpolated circularly.** Linear interpolation from 350° to 10° passes
  through 180° — pointing the vehicle the wrong way down the street. `interpolate_bearing`
  walks the shorter arc.
- **Gaps are flagged, not bridged.** Interpolating across a 40-second GPS outage draws a
  confident straight line through buildings. The estimate is still returned, with
  `is_trustworthy=False` and a stated reason, so the *caller* decides.
- **Uncertainty combines two sources**, added rather than in quadrature (deliberately
  pessimistic):

$$u = \underbrace{a_0 + \alpha(a_1 - a_0)}_{\text{fix accuracy}} + \underbrace{0.25 \cdot d(P_0,P_1) \cdot 4\alpha(1-\alpha)}_{\text{interpolation error}}$$

  The second term is zero at each anchor and peaks midway, which is where a
  straight-line assumption is most wrong on a curve.

## V1.5+ — ray-to-ground projection (specified, not built)

This is how rung 3 works. Documented now so the data model does not have to change
later; **not scheduled before M6.**

### Step 1 — choose the ground-contact pixel

For a pothole, the **bottom-centre** of the bounding box approximates the near rim,
where the defect meets the road plane:

$$(u, v) = \left(\frac{x_1 + x_2}{2},\; y_2\right)$$

Implemented as `BoundingBox.ground_contact`. The box centre would float above the road
surface and bias every estimate away from the camera.

With segmentation masks (M7+) this improves to the mask's lowest point.

### Step 2 — pixel to camera ray

With intrinsic matrix

$$K = \begin{bmatrix} f_x & 0 & c_x \\ 0 & f_y & c_y \\ 0 & 0 & 1 \end{bmatrix}$$

the ray direction in camera coordinates is

$$\mathbf{r}_c = K^{-1} \begin{bmatrix} u \\ v \\ 1 \end{bmatrix}$$

**Where `K` comes from — the single most useful platform fact in this document:**
iOS can hand it to us directly. Setting
`AVCaptureConnection.isCameraIntrinsicMatrixDeliveryEnabled` (after checking
`...DeliverySupported`, since not all capture formats support it) attaches
`kCMSampleBufferAttachmentKey_CameraIntrinsicMatrix` to every sample buffer: a
`CFData`-encoded `matrix_float3x3` whose (0,0) and (1,1) entries are `fₓ` and `f_y` in
pixels.

That removes checkerboard calibration from the critical path entirely, and is the
strongest technical argument for an eventual native iOS collector.

### Step 3 — rotate into vehicle coordinates

With camera mount rotation `R` (from pitch θ, roll φ, yaw ψ relative to the vehicle):

$$\mathbf{r}_w = R\,\mathbf{r}_c$$

Pitch dominates. Roll and yaw are second-order for a well-mounted phone, which is why
`COLLECTION_PROTOCOL.md` insists on a rigid mount: a mount that shifts invalidates `R`
silently, and every subsequent position inherits the error.

### Step 4 — intersect with the ground plane

Assume the road immediately ahead is locally planar at height `h` below the camera
(the mount height). For camera origin `C` and ray `r_w = (r_x, r_y, r_z)`:

$$\lambda = \frac{h}{-r_z}, \qquad P_{veh} = C + \lambda \mathbf{r}_w$$

giving forward offset `d_f` and lateral offset `d_l` in vehicle coordinates.

**This step is where the error budget is spent.** The flat-plane assumption fails on
crowned roads, hills and speed bumps, and the error grows roughly with the *square* of
distance — a 1° pitch error at 20 m ahead is far worse than at 5 m. Practical
consequences:

- Prefer detections in the **lower half** of the frame (nearer the vehicle).
- Weight the uncertainty by estimated forward distance.
- Never report a projected position without its (larger) uncertainty.

### Step 5 — offsets to geographic coordinates

With vehicle heading `ψ_v` (degrees clockwise from north):

$$\text{bearing} = \psi_v + \operatorname{atan2}(d_l, d_f), \qquad
\text{distance} = \sqrt{d_f^2 + d_l^2}$$

then `destination_point(vehicle_position, bearing, distance)` — already implemented and
tested in `roadeye/geolocation/geodesy.py`.

### Step 6 — snap to a road segment and report uncertainty

Map-match the result (below), and report `uncertainty_m` combining GPS error, heading
error, pitch/mount error and the ground-plane assumption. **Never publish a projected
coordinate without it.**

## Map matching (M5)

Raw GPS drifts sideways; a fix can land on the sidewalk or the wrong carriageway. Since
we know the vehicle was travelling *along* a road at a known heading, we can do better
than trusting the raw point.

MVP algorithm — no routing server, no extra service:

1. Query candidate segments near the fix (R*Tree bounding box, then true distance).
2. Score each by perpendicular distance (`point_to_segment_distance_m`, which clamps to
   the finite segment rather than an infinite great circle).
3. Score by heading compatibility (`bearing_difference_deg` between vehicle heading and
   segment bearing) — this is what disambiguates parallel streets.
4. Prefer continuity with the previous frame's match: a survey is a continuous path, so
   jumping streets between consecutive frames is evidence of a bad match.
5. Record `match_distance_m` and `heading_delta_deg` on the result so a weak match is
   visible rather than hidden.

**Licensing constraint that shapes the schema:** road geometry comes from
OpenStreetMap under ODbL, which carries share-alike obligations *on data*. Our defect
coordinates are our own. `RoadSegmentRef` is therefore a separable reference (source +
id) rather than denormalised columns, preserving the option to detach OSM-derived
identifiers from a proprietary defect database. See `LICENSE_AUDIT.md` (L-3).

## What we will not claim

**Depth.** A single monocular RGB frame does not yield trustworthy physical depth. We
will not tell a municipality "this hole is 7.3 cm deep". Supported iPhones can provide
real depth data in certain configurations (including LiDAR-capable devices), but that is
an optional research path, never an MVP dependency.

**Centimetre positions.** Consumer GPS is metres. Every published coordinate carries
its uncertainty, and six decimal places in an export (~0.11 m) is a formatting artefact,
not a precision claim — which is exactly why the export includes
`location_uncertainty_m` in the same row.

## Error budget (estimates, unmeasured)

Stated so they can be *disproved* by measurement at M8, not treated as results.

| Source | V1 (now) | V3 (projected) |
|---|---|---|
| GPS fix | 5-15 m | 5-15 m |
| Interpolation | 0-3 m | 0-3 m |
| Camera-to-defect offset | **5-30 m (uncorrected)** | 0.5-2 m |
| Ground-plane assumption | — | 0.3-3 m |
| Heading error | — | 0.5-2 m |
| **Total (typical)** | **10-40 m** | **6-20 m** |

Note what this table says honestly: at V1 the dominant error is the *uncorrected camera
offset*, not GPS. Rung 3 attacks the biggest term. But even a perfect projection leaves
consumer GPS as the floor — which is why "which road segment" is a more defensible
product claim than "which square metre".
