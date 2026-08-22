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

That is the entire required dependency set. The processing core, its CLI and its 201
tests run on Python's standard library plus pydantic.

## Optional extras (declared, not required)

| Extra | Component | Licence | Purpose |
|---|---|---|---|
| `video` | PyAV | BSD-3-Clause | Real video decoding |
| `video`, `quality`, `vision` | numpy | BSD-3-Clause | Arrays, image-quality scoring |
| `vision` | torch | BSD-3-Clause | Inference/training |
| `vision` | torchvision | BSD-3-Clause | **M3 baseline detector** |
| `api` | FastAPI | MIT | Local API |
| `api` | uvicorn | BSD-3-Clause | ASGI server |
| `dev` | pytest | MIT | Tests |
| `dev` | ruff | MIT | Lint/format |
| `dev` | mypy | MIT | Type checking |

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
| MapLibre GL JS | BSD-3-Clause | Dashboard maps |
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

**None in use.** Before any pretrained backbone is adopted, record its URL and stated
licence in `ModelVersion.weights_origin` / `weights_license`. Many detection checkpoints
are COCO-trained; COCO annotations are CC BY 4.0 and its images are subject to Flickr
terms. Tracked as L-6.
