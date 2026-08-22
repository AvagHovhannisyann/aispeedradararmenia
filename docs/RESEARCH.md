# RoadEye — M0 Research Record

**Purpose:** the evidence base behind `TECHNOLOGY_EVALUATION.md` and `LICENSE_AUDIT.md`.
Every claim RoadEye relies on should be traceable to a primary source and a date.

**Verified:** 2026-08-22. **Re-verify before any milestone that depends on a claim.**

> Rule: do not trust memory for versions, licenses or quotas. They change. Where a claim
> below is second-hand or contested, it says so explicitly.

---

## 1. Is the core idea technically credible?

**Yes — there is a shipping commercial product with the same physical premise.**

Vaisala's **RoadAI** uses a standard smartphone mounted on a vehicle windshield with
*no specialist hardware, calibration, or vehicle modification*, and processes the
captured footage to detect and classify road defects, markings, signs and assets with
geolocation. Reported defect coverage includes cracking, potholes, fretting, settlement,
deterioration and bleeding, with segment-level condition reporting.

Critically for us, **Vaisala anonymises every collected video before it appears in their
web app**, masking vehicles and people, and states this does not interfere with
detection of signs, cracking or potholes. That is direct commercial precedent that
anonymisation and defect detection are compatible — it validates the approach in
`PRIVACY.md`.

**Consequence:** the open question for RoadEye is *not* "can a phone see road damage".
It is "can we build a reliable, locally-trained, geolocated, auditable version at
essentially zero initial cost". That reframing drives the whole milestone plan.

Sources:
- https://www.vaisala.com/en/products/road-ai
- https://www.vaisala.com/en/industries-innovation/computer-vision-services
- https://www.xweather.com/products/roadai

---

## 2. RDD2022 — the bootstrap dataset

### Facts (verified via Figshare API, 2026-08-22)

| Field | Value |
|---|---|
| Title | RDD2022 – The multi-national Road Damage Dataset released through CRDDC'2022 |
| DOI | `10.6084/m9.figshare.21431547.v1` |
| Published | 2022-10-29 |
| Images | 47,420 from **6 countries** (Japan, India, Czech Republic, Norway, USA, China) |
| Annotations | >55,000 road-damage instances |
| Archive | `RDD2022_released_through_CRDDC2022.zip`, **13,264,172,619 bytes** |
| md5 | `b62bd51d2ffcfaa76c60f234f0cc2bb3` |
| Figshare license | **CC BY 4.0** |
| Authors' repo license | **CC BY-SA 4.0** ← contradiction, see `LICENSE_AUDIT.md` |

Citation to use:

> Arya, Deeksha; Maeda, Hiroya; Sekimoto, Yoshihide; Omata, Hiroshi; Ghosh, Sanjay Kumar;
> Toshniwal, Durga; et al. (2022). *RDD2022 - The multi-national Road Damage Dataset
> released through CRDDC'2022*. figshare. Dataset.
> https://doi.org/10.6084/m9.figshare.21431547.v1

### Class ontology (adopted verbatim by RoadEye)

From the authors' repository:

> "D00: Longitudinal Crack, D10: Transverse Crack, D20: Aligator Crack, D40: Pothole"

We adopt these four as the initial ontology rather than inventing our own, so that our
results are comparable to published benchmarks. (Note the source's spelling
"Aligator"; RoadEye normalises to `alligator_crack` and preserves the original code
`D20` as metadata.)

### The finding that matters most: domain shift is real and measured

The RDD line of research exists *because* single-country models generalised badly.
Models trained predominantly on Japanese roads degraded substantially when applied to
India and the Czech Republic — which is precisely why the dataset became multi-national.

For calibration on what "good" means: the best multi-country ensemble in CRDDC'2022
achieved roughly **F1 ≈ 0.76** across the combined six-country test set. Road distress
detection across varying countries, cameras and conditions is **not** a 99%-accuracy
solved problem.

**Two consequences for RoadEye:**
1. Any claim that a foreign pretrained model will "just work" in Yerevan is unsupported
   by the literature. Armenian fine-tuning is a requirement, not an optimisation.
2. Our own honest metrics should be compared against ~0.76 F1 as a *reference point for
   a hard problem*, not against an imagined 0.99. Setting the founder's and the
   municipality's expectations correctly is part of the engineering job.

### Privacy precedent from the dataset authors

The RDD authors state that because the dataset is public, faces and license plates that
are clearly visible were **blurred out** based on visual inspection. Independent
confirmation that redaction is the norm for public road imagery.

Sources:
- https://api.figshare.com/v2/articles/21431547 (license/metadata, queried directly)
- https://github.com/sekilab/RoadDamageDetector (README §License, line 357; class definitions)
- https://arxiv.org/abs/2209.08538 (RDD2022 paper)
- https://rmets.onlinelibrary.wiley.com/doi/10.1002/gdj3.260 (Geoscience Data Journal)
- https://crddc2022.sekilab.global/ (challenge)

---

## 3. Mobile platform findings

### Expo
- `expo-camera` supports iOS+Android, works in Expo Go, records video to app cache.
- `expo-location` provides location with accuracy, speed and heading.
- **SDK 55** adds iOS *accuracy authorization* in the permission response — tells us
  whether the user granted full or reduced accuracy. RoadEye surfaces reduced accuracy
  as a survey-level warning rather than silently recording degraded data.
- **Background location** requires extra native configuration; on iOS it needs a
  development build (not Expo Go), `Always` authorization and the `location` background
  mode in `Info.plist`. **MVP avoids this entirely** by keeping the app foregrounded.

Sources: https://docs.expo.dev/versions/latest/sdk/location/ ·
https://github.com/expo/expo/tree/sdk-54/packages/expo-camera · https://expo.dev/changelog/sdk-55

### Apple free provisioning
Free "Personal Team" allows on-device testing without the $99 program, limited to
**3 devices**, **10 App IDs**, and **7-day provisioning profile expiry** requiring
rebuild/reinstall. Store/TestFlight distribution needs the paid program.

Source: https://developer.apple.com/support/compare-memberships/ ·
https://developer.apple.com/help/account/provisioning-profiles/provisioning-profile-updates/

### iOS camera intrinsics — the geolocation unlock
`AVCaptureConnection.isCameraIntrinsicMatrixDeliverySupported` /
`...DeliveryEnabled` cause each sample buffer to carry
`kCMSampleBufferAttachmentKey_CameraIntrinsicMatrix`: a `CFData`-encoded
`matrix_float3x3` whose (0,0) and (1,1) entries are horizontal/vertical focal length
**in pixels**. Not all capture formats support it — must check `...Supported` first.

This gives us **K** directly, without a checkerboard calibration, which is exactly what
the ray-to-ground projection in `GEOLOCATION.md` needs. It is the single strongest
technical argument for an eventual native iOS collector.

Source: https://developer.apple.com/documentation/avfoundation/avcaptureconnection/iscameraintrinsicmatrixdeliveryenabled

### Android
CameraX is Google's recommended camera API, providing `Preview`, `VideoCapture` and
`ImageAnalysis` (CPU-accessible buffers for ML). Fused Location Provider combines
location technologies and supplies velocity/bearing.

For on-device inference, **LiteRT 2.x**'s `CompiledModel` API is now the high-performance
path across CPU/GPU/NPU; the legacy `Interpreter` API remains only for backwards
compatibility. Target `CompiledModel` if we go on-device on Android.

Sources: https://developer.android.com/media/camera/camerax ·
https://ai.google.dev/edge/litert/inference · https://developers.google.com/edge/litert/next/android_kotlin

---

## 4. Detection frameworks

| Finding | Source |
|---|---|
| RTMDet is distributed under **Apache-2.0** via MMDetection/MMYOLO; usable by industrial users | https://github.com/open-mmlab/mmdetection · https://roboflow.com/model/rtmdet |
| Detectron2 is **Apache-2.0** but in **maintenance mode**, no tagged release since v0.6 (2021) | https://github.com/facebookresearch/detectron2 |
| TorchVision is **BSD-3-Clause**, actively maintained, ships Faster R-CNN / RetinaNet / FCOS / SSD / Mask R-CNN | https://github.com/pytorch/vision |
| Ultralytics defaults to **AGPL-3.0** covering training code *and produced models*; Enterprise license removes the obligation | https://www.ultralytics.com/license · https://www.ultralytics.com/legal/agpl-3-0-software-license |
| Current real-time frontier is the RT-DETR/RF-DETR transformer cluster, mostly Apache-2.0 | https://blog.roboflow.com/mobile-object-detection-models/ |
| ONNX Runtime supports iOS/Android with the same API as cloud inference; official object-detection samples exist | https://onnxruntime.ai/docs/tutorials/mobile/ |

---

## 5. Annotation, geospatial, compute

| Finding | Source |
|---|---|
| CVAT Community core is **MIT**; some serverless assets/dependencies carry separate licenses | https://github.com/cvat-ai/cvat · https://docs.cvat.ai/docs/products/community/ |
| Label Studio Community is **Apache-2.0** | https://github.com/HumanSignal/label-studio/blob/develop/LICENSE |
| MapLibre GL JS is **BSD-3-Clause**; Mapbox GL JS was relicensed to a non-OSS license in Dec 2020 | https://github.com/maplibre/maplibre-gl-js/blob/main/LICENSE.txt |
| SQLite R*Tree is a virtual table; 2-D index = 5 columns (id + min/max per dimension); max 5 dimensions | https://www.sqlite.org/rtree.html |
| OSM tile policy: clear attribution, unique User-Agent, **no bulk/offline download**, fixthemap link, finite donated capacity | https://operations.osmfoundation.org/policies/tiles/ |
| ODbL is an attribution + share-alike license on **data** | https://osmfoundation.org/wiki/Licence/Licence_and_Legal_FAQ |
| Kaggle free tier ≈ **30 GPU-hours/week**, commonly P100 16GB | https://www.kaggle.com/docs/efficient-gpu-usage |
| Colab free tier: **no guaranteed GPU**; usage limits, idle timeout, max VM lifetime and GPU types **vary over time and are unpublished**; sessions cap ~12h | https://research.google.com/colaboratory/faq.html |

---

## 6. Armenian data protection (preliminary — not legal advice)

- The governing statute is the **Law on Protection of Personal Data, HO-49-N**, adopted
  **18 May 2015**, replacing the 2002 law. It regulates processing by public and private
  entities.
- The supervisory authority is the **Personal Data Protection Agency at the Ministry of
  Justice**, which launched a public website in H2 2024 offering guidance, templates and
  legislative acts.
- Armenian video-surveillance guidance treats recorded imagery of people as personal
  data and emphasises lawfulness and proportionality.
- Context worth tracking: a 2024 Interior Ministry bill proposing mandatory CCTV for
  many private entities drew significant criticism from human-rights organisations. The
  surveillance-regulation landscape is **actively changing**, so this section must be
  re-verified before any municipal deployment.

**Status: OPEN.** Items L-4 and L-5 in `LICENSE_AUDIT.md` require Armenian counsel
and/or consultation with the Agency. `PRIVACY.md` specifies a design that is
conservative by construction so that engineering is not blocked while this resolves.

Sources:
- https://natlex.ilo.org/dyn/natlex2/r/natlex/fe/details?p3_isn=101975 (HO-49-N record)
- https://www.dlapiperdataprotection.com/?t=law&c=AM
- https://practiceguides.chambers.com/practice-guides/data-protection-privacy-2025/armenia/
- https://www.hrw.org/news/2024/10/31/armenia-surveillance-bill-threatens-rights

---

## 7. Known gaps in this research

Stated explicitly so they are not mistaken for settled:

1. **RDD2022 license conflict is unresolved** (BLOCKING-1).
2. **Whether trained weights are "Adapted Material"** under CC BY-SA is legally unsettled
   and jurisdiction-dependent. We assume the strict reading.
3. **Armenian lawful basis and retention period** for public-road video are not
   determined. `PRIVACY.md` picks a conservative default; counsel must confirm.
4. **COCO checkpoint provenance** for any pretrained backbone has not been audited.
5. **No RDD2022 download has been performed** — 13.3 GiB against ~30 GiB free disk means
   ingestion must be partial/streaming. Untested.
6. **No real Yerevan footage exists yet.** Every performance expectation in this
   repository is therefore a hypothesis, not a measurement. The first honest number
   comes at M8.
