# RoadEye — Technology Evaluation & Decision Matrix

**Status:** M0 output. Decisions here are binding until superseded by an ADR.
**Last verified:** 2026-08-22

Every row carries a recommendation: **USE** (adopt now) / **EXPERIMENT** (spike, no
commitment) / **DEFER** (revisit at a named milestone) / **REJECT** (do not use).

---

## Verified host environment

Measured on the development container, 2026-08-22:

| Property | Value | Consequence |
|---|---|---|
| OS | Ubuntu 24.04.4 LTS, kernel 6.18 | Fine |
| Python | 3.11.15 | Target **>=3.11**. Note: not 3.12+, so avoid 3.12-only syntax |
| Node | v22.22.2, npm 10.9.7 | Modern enough for Expo |
| Git | 2.43.0 | Fine |
| Docker | 29.3.1 | Available but **not required** to run RoadEye |
| **GPU** | **none** (`nvidia-smi` absent) | **All core tests must run CPU-only** |
| CPU / RAM | 4 cores / 15 GiB | Training locally is impractical; inference is fine |
| Disk free | ~30 GiB | **RDD2022 alone is 13.3 GiB zipped** — see note |
| **ffmpeg** | **NOT INSTALLED** | Video decode must be an optional, pluggable capability |

Two consequences drive real architectural decisions:

1. **No GPU + no ffmpeg** ⇒ the pipeline core must be testable with **synthetic data and
   a fake detector**. Video decoding and neural inference are *plugins behind
   interfaces*, never hard imports in the domain layer. This is enforced by tests.
2. **~30 GiB disk vs 13.3 GiB dataset** ⇒ dataset ingestion must support
   **per-country partial download**. Downloading all of RDD2022 and unzipping it would
   fill the disk. The ingestion tool must stream and verify checksums rather than
   assuming a full local copy.

> This container is ephemeral and is **not** the founder's laptop. Re-run
> `scripts/check_env.py` on the real development machine; its output belongs in the
> pilot record.

---

## 1. Mobile collector

| Option | License | Verdict | Reasoning |
|---|---|---|---|
| **Expo + React Native + TypeScript** | MIT | **USE (MVP)** | One codebase, iOS+Android, no native toolchain to fight on day one. `expo-camera` records video to app cache; `expo-location` provides lat/lon/accuracy/speed/heading. Fastest path to a real drive. |
| **expo-camera** | MIT | **USE** | Supported on iOS+Android, available in Expo Go, records video. Sufficient for MVP. |
| **expo-location** | MIT | **USE (foreground only)** | Foreground updates need no native config. SDK 55 added iOS accuracy-authorization in the permission response — useful, we should surface reduced-accuracy mode as a survey warning. |
| expo-location **background mode** | MIT | **DEFER → M1.5** | Requires a development build on iOS (not Expo Go) plus `Info.plist` background modes and Always authorization. **The MVP deliberately does not need it** — the app stays foregrounded during a survey. This single decision removes a large class of iOS background-execution bugs. |
| **CameraX** (Android native) | Apache-2.0 | **DEFER → M1.5** | Google's recommended camera API. `Preview` + `VideoCapture` + `ImageAnalysis` (CPU-accessible buffers for ML). The right answer for a serious Android collector. |
| **AVFoundation** (iOS native) | Apple SDK | **DEFER → M1.5** | Needed for precise capture metadata. Critically, `AVCaptureConnection.isCameraIntrinsicMatrixDeliveryEnabled` delivers a per-frame `matrix_float3x3` via the `kCMSampleBufferAttachmentKey_CameraIntrinsicMatrix` attachment — the (0,0)/(1,1) entries are focal lengths in pixels. **This is the unlock for accurate defect geolocation** (see `GEOLOCATION.md`) and is the strongest single reason to eventually go native on iOS. |
| **Core ML + Vision** (iOS) | Apple SDK | **DEFER → M2 (on-device)** | The production path for real-time iOS inference. |
| **LiteRT** (ex TensorFlow Lite) | Apache-2.0 | **DEFER → M2 (on-device)** | Google now positions the **`CompiledModel` API** (LiteRT 2.x) as the high-performance path across CPU/GPU/NPU; the older `Interpreter` API remains only for backwards compatibility. If/when we go on-device on Android, target `CompiledModel`, not `Interpreter`. |
| **ONNX Runtime Mobile** | MIT | **EXPERIMENT** | Attractive because one exported ONNX artifact serves iOS, Android and desktop with the same API. Best cross-platform hedge; MIT is the friendliest license in the mobile set. |
| Native-first collector from day one | — | **REJECT (for now)** | Doubles platform work before we have a single real drive. Revisit at M1.5. |

**Decision (ADR-002):** Expo for MVP. Architect the survey bundle format so a future
native collector produces byte-identical bundles — the bundle schema, not the app, is
the contract.

**Cost note:** iOS free provisioning imposes a 7-day reinstall cycle (3 devices, 10 App
IDs). Android sideloading has no such friction, so **prefer an Android device for early
field testing** if one is available.

---

## 2. Object detection stack

This is where the brief's instinct was right but the specific recommendation needs
adjustment.

| Option | License | Verdict | Reasoning |
|---|---|---|---|
| **TorchVision detection** | BSD-3-Clause | **USE (M3 baseline)** | Actively maintained, zero extra dependencies beyond torch, ships Faster R-CNN / RetinaNet / FCOS / SSD / Mask R-CNN. On a CPU-only zero-budget machine this **just installs and works**. The correct first baseline. |
| **MMDetection / RTMDet** | Apache-2.0 | **EXPERIMENT (M3/M7)** | RTMDet has an excellent parameter/accuracy trade-off from tiny to XL and is genuinely a strong candidate for the shipping model. **But** `mmcv` commonly requires compilation matched to a specific torch/CUDA build, which is a well-known multi-day time sink on a CPU-only box. Spike it in an isolated environment; do not let it block M3. |
| **Detectron2** | Apache-2.0 | **DEFER** | License is fine, but it is in **maintenance mode** — no tagged release since v0.6 in 2021. Do not start a 2026 company on it. |
| **RT-DETR / RF-DETR family** | Apache-2.0 (mostly) | **EXPERIMENT (M7)** | The current real-time frontier is a cluster of transformer detectors descending from RT-DETR, and they are mostly Apache-2.0. Worth evaluating once we have an Armenian test set worth optimising against. Verify each checkpoint's license individually. |
| **Ultralytics YOLO** | AGPL-3.0 / paid Enterprise | **REJECT** | See `LICENSE_AUDIT.md` BLOCKING-2. AGPL-3.0 covers the training code *and the models it produces*; the network clause bites a municipal SaaS dashboard. Convenient tutorials are not worth the company. |

**Decision (ADR-004):** the domain layer depends on a `RoadDamageDetector` **Protocol**,
never on a framework. Concrete adapters (`TorchvisionDetector`, `MMDetDetector`,
`OnnxDetector`, `FakeDetector`) are interchangeable. Framework objects must never leak
into domain models. This is what makes the above table's uncertainty affordable — being
wrong about the detector costs one adapter, not a rewrite.

### Detection vs segmentation

- **Potholes** → bounding boxes are sufficient and are the MVP hero class.
- **Cracks / lane markings** → boxes are a poor fit (a thin diagonal crack's box is
  mostly not-crack). Segmentation becomes valuable for area/length/density estimates.

The `Detection` model therefore carries an **optional `mask` field from day one**, kept
`None` in MVP. This avoids a schema migration later at the cost of one nullable field
now.

---

## 3. Annotation

| Option | License | Verdict | Reasoning |
|---|---|---|---|
| **Label Studio Community** | Apache-2.0 | **USE (start here)** | `pip install label-studio`, no Docker required. Lowest friction for the first few thousand Armenian labels. |
| **CVAT Community** | MIT | **EXPERIMENT** | Stronger for *video* annotation and interpolation between keyframes, which suits our data. Cost: Docker-based setup. **Audit before enabling AI-assisted annotation** — serverless assets/dependencies carry separate licenses and could reintroduce a non-permissive model. |
| Built-in RoadEye review UI | ours | **USE** | Not a replacement for a labelling tool. Its job is fast **approve/reject/correct** of model output, which is a different and higher-value loop (see `ML_STRATEGY.md`). |

---

## 4. Geospatial & storage

| Option | License | Verdict | Reasoning |
|---|---|---|---|
| **SQLite** | Public domain | **USE (MVP)** | Zero-install, single file, transactional. Correct for a one-laptop MVP. |
| **SQLite R*Tree** | Public domain | **USE** | Virtual-table spatial index; entries are min/max pairs per dimension (2-D ⇒ 5 columns). Exactly right for "find defects near this point" without a GIS server. |
| **PostgreSQL + PostGIS** | PostgreSQL / GPL-2.0 | **DEFER → production** | The obvious destination. Deliberately not now: it would add an install step and a running service for zero MVP benefit. Migration path documented in `DATA_MODEL.md`. |
| **SpatiaLite** | MPL/GPL/LGPL tri-license | **DEFER** | "PostGIS without a server". Tempting, but the tri-license needs care and R*Tree already covers MVP needs. |
| **MapLibre GL JS** | BSD-3-Clause | **USE** | Vector maps in the browser, safe for closed-source products. The direct reason we avoid Mapbox GL JS (non-OSS since Dec 2020). |
| **OpenStreetMap data** | ODbL | **USE, with care** | Attribution + share-alike **on data**. Keep OSM-derived segment refs separable from our defect records — see `LICENSE_AUDIT.md`. |
| `tile.openstreetmap.org` in production | policy | **REJECT** | Finite donated capacity, explicitly not a production service; offline/bulk use prohibited. Prototype only, with compliant User-Agent, visible attribution and a fixthemap link. |
| Dedicated map-matching server (OSRM/Valhalla) | BSD / various | **DEFER** | A local nearest-segment + heading-compatibility matcher is sufficient for MVP and has no ops cost. |

---

## 5. Backend, dashboard, tooling

| Component | Choice | License | Verdict |
|---|---|---|---|
| API | **FastAPI + Pydantic v2** | MIT | **USE** — typed, self-documenting, localhost-first. Pydantic 2.13.4 verified installed. |
| Dashboard | **React + TypeScript + MapLibre** | MIT / BSD-3 | **USE** |
| Python tests | **pytest** | MIT | **USE** — must pass CPU-only, no ffmpeg, no network |
| Video decode | **PyAV or imageio-ffmpeg**, behind an interface | BSD-3 / various | **EXPERIMENT** — ffmpeg is *absent* in this environment, so decoding is an optional extra (`pip install roadeye[video]`), never a core import |
| Docker | — | — | **DEFER** — only for CVAT/reproducibility. Running the RoadEye MVP must never require it |
| Anthropic SDK | — | — | **REJECT** — see ADR-005; RoadEye must not require any paid AI API at runtime |

---

## 6. Free compute

| Service | Verdict | Reasoning |
|---|---|---|
| Local CPU | **USE** | Adequate for inference, pipeline dev, and the whole test suite |
| **Kaggle** | **USE (primary training)** | ~30 GPU-h/week, commonly P100 16GB |
| **Colab** | **USE (backup)** | Free tier explicitly guarantees nothing: GPU type, idle timeout and max VM lifetime all "vary over time" and are unpublished |

**Hard rule:** free GPU services are development resources with **no SLA**. Training
scripts must checkpoint often and resume cleanly. Nothing in production may depend on
them. And per `PRIVACY.md`, **raw Armenian survey video must not be uploaded to them.**

---

## Summary of decisions that differ from the founder's initial brief

The brief was largely right. Four adjustments are worth stating plainly:

1. **RDD2022's license is contradictory** (CC BY 4.0 on Figshare vs CC BY-SA 4.0 in the
   authors' repo). The brief assumed this needed "verification"; it turns out to be an
   actual conflict. Treated as BLOCKING-1 and quarantined architecturally.
2. **Start with torchvision, not MMDetection.** The brief's licensing logic for
   preferring MMDetection over Ultralytics is correct and stands. But on a CPU-only
   zero-budget machine, `mmcv` build friction is a serious risk to the "get on the road
   fast" goal. torchvision is BSD-3, installs cleanly, and is enough for a baseline.
   RTMDet stays as the leading candidate for the *shipping* model.
3. **Detectron2 is effectively unmaintained** (no release since 2021) and should not be
   a candidate at all.
4. **Prefer Android for first field tests** where possible — it avoids Apple's 7-day
   free-provisioning reinstall cycle entirely.
