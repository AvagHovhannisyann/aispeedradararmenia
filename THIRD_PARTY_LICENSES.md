# Third-Party Licences

**Rule:** a new dependency is added to this file **in the same commit** that introduces
it. A dependency whose licence has not been verified counts as **rejected**, not as
"probably fine".

RoadEye is intended to become a proprietary commercial product, so five *separate*
questions must each be answered for anything we depend on: framework licence, model
implementation licence, pretrained checkpoint licence, dataset licence, and map-data
licence. See `docs/LICENSE_AUDIT.md`.

**Last verified:** 2026-08-22

## Currently installed (runtime)

| Component | Version | Licence | Notes |
|---|---|---|---|
| **pydantic** | ≥2.5,<3 | MIT | Only hard runtime dependency |

That is the entire required dependency set. The processing core, its CLI and 314 of its
459 tests run on Python's standard library plus pydantic. Map matching added no
dependency: OSM XML is parsed with `xml.etree`, Overpass with `json` and `urllib`, and
the road-network file is `gzip` + `json`.

## Optional extras (declared, not required)

| Extra | Component | Licence | Purpose |
|---|---|---|---|
| `video` | PyAV | BSD-3-Clause | Real video decoding |
| `video`, `quality`, `vision` | numpy | BSD-3-Clause | Arrays, image-quality scoring |
| `vision` | torch | BSD-3-Clause | Inference/training |
| `vision` | torchvision | BSD-3-Clause | **M3 baseline detector**, and the M5 redaction detector |
| `api` | FastAPI | MIT | Local API |
| `api` | uvicorn | BSD-3-Clause | ASGI server |
| `dev` | pytest | MIT | Tests |
| `dev` | ruff | MIT | Lint/format |
| `dev` | mypy | MIT | Type checking |
| `dev` | httpx | BSD-3-Clause | Drives FastAPI's TestClient in-process |
| `review`, `vision` | Pillow | MIT-CMU (HPND) | Evidence images and annotation drawing |
| — (browser) | MapLibre GL JS 4.7.1 | BSD-3-Clause | **In use** by the M6 dashboard. Loaded from unpkg at view time, not vendored — see ADR-010 |

PyAV bundles/links FFmpeg, which is **LGPL-2.1+ or GPL-2+ depending on build options**.
It is an optional extra invoked as a library for decoding only. Before distributing any
bundled artefact that includes it, confirm the FFmpeg build configuration — a GPL build
would impose obligations an LGPL build does not.

## Collector app (not yet installed)

| Component | Licence |
|---|---|
| React, React Native | MIT |
| Expo, expo-camera, expo-location, expo-file-system, expo-keep-awake | MIT |
| TypeScript | Apache-2.0 |

## Planned / evaluated, not yet adopted

| Component | Licence | Status |
|---|---|---|
| **MMDetection / RTMDet** | Apache-2.0 | EXPERIMENT — likely shipping model. Checkpoint licences must be audited separately |
| RT-DETR / RF-DETR family | mostly Apache-2.0 | EXPERIMENT — verify each checkpoint |
| Detectron2 | Apache-2.0 | DEFER — maintenance mode, no tagged release since v0.6 (2021) |
| ONNX / ONNX Runtime | MIT | Mobile deployment path |
| LiteRT | Apache-2.0 | Android on-device (target the `CompiledModel` API) |
| Label Studio Community | Apache-2.0 | Annotation |
| CVAT Community | MIT | Annotation. **Caveat:** some serverless assets/dependencies carry separate licences — audit before enabling AI-assisted annotation |
| SQLite (+ R*Tree) | Public domain | In use via Python stdlib |
| PostgreSQL / PostGIS | PostgreSQL / GPL-2.0 | Future. Used as a network service, not linked |

## REJECTED

| Component | Licence | Why |
|---|---|---|
| **Ultralytics YOLO** (v8/11/26) | AGPL-3.0 or paid Enterprise | AGPL covers the training code *and produced models*; the network clause reaches a hosted municipal dashboard. Incompatible with the stated business model at zero budget. See ADR-009 |
| Mapbox GL JS | Mapbox License (non-OSS since Dec 2020) | MapLibre is the BSD-3 fork |
| Anthropic / OpenAI SDKs | — | RoadEye calls no AI API at runtime (ADR-005) |

## Datasets

| Dataset | Stated licence | Status |
|---|---|---|
| **RDD2022** | **DISPUTED** — Figshare DOI record says **CC BY 4.0**; the authors' repository says **CC BY-SA 4.0** | **BLOCKING-1.** Assume the stricter reading. Models with RDD2022 lineage are marked `distribution_allowed=False` |
| Armenian survey data | Proprietary (ours) | Not yet collected |

Required RDD2022 citation:

> Arya, Deeksha; Maeda, Hiroya; Sekimoto, Yoshihide; Omata, Hiroshi; Ghosh, Sanjay
> Kumar; Toshniwal, Durga; et al. (2022). *RDD2022 - The multi-national Road Damage
> Dataset released through CRDDC'2022*. figshare. Dataset.
> https://doi.org/10.6084/m9.figshare.21431547.v1

## Map data

| Source | Licence | Obligations |
|---|---|---|
| **OpenStreetMap** | **ODbL** | Attribution **and share-alike on data**. Exports that lean on OSM-derived geometry must carry "© OpenStreetMap contributors" |
| `tile.openstreetmap.org` | Tile Usage Policy (not a licence) | Visible attribution, unique User-Agent, **no bulk/offline download**, fixthemap link. Finite donated capacity — **not a production service** |

Open question L-3: whether storing OSM-derived segment identifiers alongside proprietary
defect records creates a Derivative Database under ODbL. The schema keeps
`RoadSegmentRef` separable to preserve the option of detaching it.

## Pretrained checkpoints

| Checkpoint | Origin | Code licence | Weights licence | Use |
|---|---|---|---|---|
| `fasterrcnn_mobilenet_v3_large_fpn` (`COCO_V1`) | `download.pytorch.org/models/` | BSD-3-Clause (torchvision) | **Unstated by upstream** — see below | **Redaction only.** Finds people and vehicles so they can be destroyed |

**The weights licence is an open question (L-6), and it does not block this use.**
torchvision publishes its detection checkpoints without an explicit weights licence
distinct from the repository's BSD-3. They are COCO-trained; COCO annotations are
CC BY 4.0 and its images are subject to Flickr terms.

This checkpoint is used **locally, to destroy data**. It is never redistributed, never
shipped inside a product, never used to produce a defect that reaches a customer, and
contributes nothing to any model we train. Should L-6 resolve badly, the remedy is to
swap the detector behind `RegionDetector` — which is why that Protocol exists.

The position is deliberately different from a *road-damage* checkpoint, which would be
part of the product and would need L-6 answered before shipping. Before adopting any such
backbone, record its URL and stated licence in `ModelVersion.weights_origin` /
`weights_license`.
