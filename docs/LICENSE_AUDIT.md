# RoadEye — License Audit

**Status:** M0 (research). Living document.
**Last verified:** 2026-08-22
**Owner:** Founder + engineering lead.

> This document is an engineering risk register, **not legal advice**. Items marked
> **BLOCKING** must be resolved by a qualified lawyer before RoadEye is sold,
> sublicensed, or deployed as a proprietary product. Nothing here should be treated
> as a legal conclusion.

## Why this document exists first

RoadEye intends to become a **proprietary commercial product sold to governments and
municipalities**. In that context, four *separate* licensing questions must each be
answered independently, and they are routinely confused:

| # | Concern | Example | Contaminates? |
|---|---|---|---|
| 1 | **Framework license** | PyTorch (BSD-3) | Usually safe |
| 2 | **Model implementation license** | Ultralytics code (AGPL-3.0) | **Can be viral** |
| 3 | **Pretrained checkpoint license** | COCO-pretrained weights | Often unstated |
| 4 | **Dataset license** | RDD2022 (disputed, see below) | **Can be share-alike** |
| 5 | **Map data license** | OpenStreetMap (ODbL) | Share-alike on *data* |

A permissively licensed framework trained on a share-alike dataset using a viral model
implementation produces an artifact whose license is the **intersection of all
constraints**, not the most convenient one.

---

## BLOCKING-1 — RDD2022 has two contradictory published licenses

This is the most important finding of the M0 research phase.

RDD2022 is the dataset the entire bootstrap plan depends on. **Its license is published
two different ways by the same authors**, and the two answers have very different
commercial consequences.

### Evidence A — Figshare (the canonical DOI record) says CC BY 4.0

Queried `https://api.figshare.com/v2/articles/21431547` on 2026-08-22:

```json
"license": {
  "value": 1,
  "name": "CC BY 4.0",
  "url": "https://creativecommons.org/licenses/by/4.0/"
}
```

- DOI: `10.6084/m9.figshare.21431547.v1`
- Published: 2022-10-29
- Total size: 13,267,424,441 bytes (~13.3 GB)
- Primary file: `RDD2022_released_through_CRDDC2022.zip`, md5 `b62bd51d2ffcfaa76c60f234f0cc2bb3`

CC BY 4.0 is **attribution-only**. Under it, a commercially trained proprietary model
would be unproblematic provided attribution is given.

### Evidence B — the authors' own repository says CC BY-SA 4.0

`https://github.com/sekilab/RoadDamageDetector` `README.md`, line 357, verbatim:

> "Images on this dataset are available under the [Creative Commons
> Attribution-ShareAlike 4.0 International License](http://creativecommons.org/licenses/by-sa/4.0/)
> (CC BY-SA 4.0)."

CC BY-SA 4.0 adds **ShareAlike**. If a trained model or its weights were held to be
"Adapted Material", distributing those weights could oblige RoadEye to license them
under CC BY-SA 4.0 — i.e. **give the model away**. That is incompatible with selling a
proprietary detector.

### Why this is genuinely unresolved, not just pedantry

Whether trained weights are a derivative/adaptation of training images is an **open
legal question** with no settled answer, and the answer varies by jurisdiction.
Creative Commons' own guidance notes the ShareAlike condition is triggered on public
sharing of the work *or an adaptation of it*, while separately observing that the
application of copyright to AI training is contested and jurisdiction-dependent. Some
commentators argue weights are not derivative works at all; others advise assuming they
are unless the dataset explicitly says otherwise.

We therefore cannot resolve this by reasoning. It must be resolved by **evidence or
counsel**.

### Decision (engineering, reversible)

**Assume the stricter license (CC BY-SA 4.0) until proven otherwise.** Architecturally
this costs us almost nothing if we decide it now, and costs us the company if we
discover it late.

Concretely, the repository enforces a **model provenance quarantine**:

```
models/
  road_damage/
    rdd_bootstrap_v001/     <-- taint: RDD2022. RESEARCH/INTERNAL ONLY.
      metadata.json           distribution_allowed: false
    armenia_v001/           <-- taint: Armenian data we own.
      metadata.json           distribution_allowed: true (subject to review)
```

Every model record carries a mandatory `training_data_licenses` list and a
`distribution_allowed` boolean. Any model whose lineage includes RDD2022 is marked
`distribution_allowed: false` and may be used for **internal evaluation and
pseudo-labelling only** until BLOCKING-1 is resolved.

### Actions required (founder)

1. **Email the RDD2022 corresponding authors** (Deeksha Arya / Yoshihide Sekimoto,
   University of Tokyo) asking them to state, in writing, which license governs the
   images and whether they consider trained model weights to be Adapted Material.
   The Figshare/README contradiction is a reasonable and polite thing to ask about.
2. Retain their answer in `docs/legal/` as evidence.
3. Have counsel review before any commercial distribution of an RDD2022-derived model.

### Mitigation that removes the risk entirely

The strategic answer is the one the business wants anyway: **the shipping model should
be trained on Armenian data that RoadEye owns.** RDD2022's role is to bootstrap a
*labelling assistant*, not to be the product. Once the Armenian dataset is large enough
to train from a permissively licensed (e.g. COCO/ImageNet-pretrained, Apache-2.0/BSD)
backbone without RDD2022, the taint is severed by construction. This is tracked as a
first-class milestone goal (M7), not an afterthought.

---

## BLOCKING-2 — Ultralytics (YOLOv8/YOLO11/YOLO26) is AGPL-3.0 by default

The most common road-damage tutorials on the internet use `pip install ultralytics`.
Following them would be a serious commercial mistake.

Verified 2026-08-22 (ultralytics.com/license, ultralytics.com/legal/agpl-3-0-software-license):

- All Ultralytics YOLO code **and the models produced by that training code** default to
  **AGPL-3.0**.
- AGPL-3.0 compliance means publicly releasing the complete corresponding source of the
  **entire derivative work** — the larger application, modifications, scripts, configs,
  and where applicable the model weights.
- AGPL's network clause means even *serving* the model over an API can trigger the
  source-disclosure obligation. A municipal SaaS dashboard is exactly that scenario.
- An **Enterprise License** (paid, priced by Ultralytics) removes these obligations.

**Decision: REJECT for RoadEye's shipping path.** Not because AGPL is bad software
licensing, but because it is incompatible with the stated business model at zero budget.
Ultralytics may be used **only** for throwaway local curiosity benchmarks that never
touch the product, and even that is discouraged to avoid accidental code reuse.

This is enforced socially in `CLAUDE.md` and mechanically by not adding it to any
dependency manifest.

### The LICENSE file is not the answer. The dependency list is.

Surveyed on 2026-08-22, after a road-damage demo reel pointed at a GitHub account with
354 public repositories. Three of them ship Ultralytics YOLOv8 under an **MIT LICENSE
file**, and one of those commits `yolov8n.pt` — an Ultralytics-produced weight — into the
MIT-labelled tree. One of the three advertises pothole detection, which is exactly what
made it tempting.

Nobody there appears to be acting in bad faith. AGPL propagation is simply not obvious,
`pip install ultralytics` does not warn, and MIT is what GitHub offers first.

The consequence for us is concrete: **an author cannot grant rights they do not hold.**
Taking that code under its stated MIT terms would leave RoadEye carrying an AGPL
obligation while believing it had a permissive one — the worst version of this failure,
because it looks resolved.

So the check order is fixed:

1. `requirements.txt` / `pyproject.toml` / `package.json` — **first**
2. imports in the actual source, for anything the manifest missed
3. `LICENSE` — **last**, and only meaningful once the first two agree with it

A permissive licence sitting on top of a copyleft dependency is not a permissive
component. It is an unresolved conflict, and it counts as rejected under rule 11.

---

## Cleared components (permissive, safe for a proprietary product)

| Component | License | Verified | Notes |
|---|---|---|---|
| **PyTorch** | BSD-3-Clause | 2026-08-22 | Safe. |
| **TorchVision** | BSD-3-Clause | 2026-08-22 | Actively maintained; ships Faster R-CNN, RetinaNet, FCOS, SSD, Mask R-CNN. **Recommended MVP baseline.** |
| **MMDetection / RTMDet** | Apache-2.0 | 2026-08-22 | RTMDet distributed under Apache-2.0 via MMDetection; usable by industrial users. See caveat below. |
| **Detectron2** | Apache-2.0 | 2026-08-22 | License fine, but **in maintenance mode** — no tagged release since v0.6 (2021). DEFER. |
| **ONNX / ONNX Runtime** | MIT | 2026-08-22 | Safe. Mobile deployment path. |
| **MapLibre GL JS** | BSD-3-Clause | 2026-08-22 | Safe for closed-source SaaS; retain copyright notice. Explicitly the reason we do **not** use Mapbox GL JS, relicensed to a non-OSS "Mapbox License" in Dec 2020. |
| **CVAT Community** | MIT | 2026-08-22 | Core is MIT. **Caveat:** some serverless assets/dependencies carry separate licenses — audit before using any AI-assisted auto-annotation function whose model may be non-permissive. |
| **Label Studio Community** | Apache-2.0 | 2026-08-22 | Safe. Simpler to run than CVAT. |
| **SQLite (+ R*Tree)** | Public domain | 2026-08-22 | Safe. |
| **PostgreSQL / PostGIS** | PostgreSQL / GPL-2.0 | 2026-08-22 | PostGIS is GPL-2.0 but used as a **database server over a network protocol**, not linked into our binary. Standard industry practice; confirm with counsel before shipping an appliance that bundles it. |
| **FastAPI / Pydantic / Starlette** | MIT | 2026-08-22 | Safe. |
| **React / React Native / Expo** | MIT | 2026-08-22 | Safe. |

### Caveat on MMDetection/RTMDet

Apache-2.0 covers the **implementation**. The **pretrained checkpoints** in the model
zoo are a separate question and are commonly trained on **COCO**, whose images are
subject to Flickr terms and whose annotations are CC BY 4.0. Before shipping any model
initialised from a downloaded checkpoint, record the checkpoint URL and its stated
license in the model registry. This is why `ModelVersion.training_data_licenses` is a
required field, not an optional one.

There is also a **practical** caveat: MMDetection depends on `mmcv`, which frequently
requires compilation against a specific torch/CUDA combination. On a zero-budget,
CPU-only laptop this is a known source of multi-day setup failures. See
`docs/TECHNOLOGY_EVALUATION.md` — we recommend **torchvision first, MMDetection as an
experiment track**.

---

## OpenStreetMap — two distinct obligations, often conflated

| Concern | Obligation |
|---|---|
| **Map data** (geometry we use for map-matching) | **ODbL**: attribution + share-alike **on the data**. If we materially enhance OSM geometry and publish works based on it, share-alike may apply to the derived database. |
| **Tile images** from `tile.openstreetmap.org` | **Tile Usage Policy**, a separate operational policy, not a license. |

Verified 2026-08-22 from `operations.osmfoundation.org/policies/tiles/`:

- Must display clear attribution, e.g. "© OpenStreetMap contributors", not hidden behind
  a toggle or off-screen.
- Must send a **clear, unique User-Agent** identifying the app with contact info.
- **Bulk/offline downloading is prohibited** — "download city/country for offline use"
  style features are explicitly disallowed.
- Must provide a "Report a map issue" link to `openstreetmap.org/fixthemap`.
- Capacity is finite and donated; it is **not** a free production map service.

**Critical architectural consequence:** RoadEye's *defect coordinates are our own data*
and are not derived from OSM. But **map-matched road-segment identifiers are derived
from OSM geometry**. To keep the ODbL share-alike question away from our proprietary
defect database, the schema stores map-matching results in a **separable** way:

- `Defect.location` (lat/lon) — **ours**, produced by our sensor and pipeline.
- `Defect.road_segment_ref` — an *external reference* (source + id), explicitly marked
  as OSM-derived and separable from the defect record.

This is why `DATA_MODEL.md` keeps `road` as a referenced entity rather than denormalised
columns. It costs nothing now and preserves options later.

**Decision:** prototype with light OSM tile usage under full policy compliance;
production must use self-hosted tiles or a paid provider. Tracked in
`docs/COST_LEDGER.md` as a known future cost.

---

## Free compute — quotas, not infrastructure

| Service | Verified 2026-08-22 |
|---|---|
| **Kaggle** | ~30 GPU-hours/week free (commonly P100 16GB); session caps apply; quota varies with availability. |
| **Google Colab** | Free tier explicitly provides **no guarantee** of a GPU or a particular GPU type. Google states usage limits, idle timeouts, max VM lifetime and GPU availability "vary over time" and are deliberately unpublished. Free sessions cap around 12h and may end sooner. |

**Decision:** treat both as *development resources with no SLA*. RoadEye must never have
a production dependency on them. Training scripts must checkpoint frequently and resume
cleanly, because eviction is normal, not exceptional.

Also note: both services' terms should be checked before uploading any Armenian survey
footage, which may contain personal data (see `docs/PRIVACY.md`). Default position:
**raw Armenian video never leaves local storage.**

---

## Apple developer costs

Verified 2026-08-22 (developer.apple.com/support/compare-memberships, Apple provisioning docs):

- A free Apple Account ("Personal Team") allows on-device testing for personal use.
- Limits: **3 registered devices per platform**, **10 App IDs at a time**, and
  provisioning profiles **expire 7 days from issuance**, requiring rebuild/reinstall.
- App Store / TestFlight distribution requires the paid Apple Developer Program ($99/yr).

**Decision:** MVP and internal field trials run on free provisioning, accepting the
7-day reinstall cycle. The $99 is deferred until a municipal pilot needs stable
distribution — at which point it is trivially justified. Recorded in `COST_LEDGER.md`.

Android sideloading has no equivalent fee, which is a real argument for **testing on
Android first** if a suitable device is available.

---

## Outstanding questions for counsel

| ID | Question | Blocks |
|---|---|---|
| L-1 | Which RDD2022 license governs — CC BY 4.0 or CC BY-SA 4.0? | Commercial distribution of any RDD-derived model |
| L-2 | Are trained weights "Adapted Material" under CC BY-SA 4.0 in the relevant jurisdiction? | Same as L-1 |
| L-3 | Does storing OSM-derived segment IDs alongside proprietary defects create an ODbL Derivative Database? | Productising the defect database |
| L-4 | What is the lawful basis under Armenian law HO-49-N for recording public-road video containing identifiable people/plates? | Any municipal deployment |
| L-5 | Retention period and access controls required for raw survey video under Armenian law | Any municipal deployment |
| L-6 | COCO checkpoint provenance for any pretrained backbone we ship | Shipping a pretrained-initialised model. **Does not block M5 redaction** — see below |

L-4 and L-5 are elaborated in `docs/PRIVACY.md`.

### L-3 is now live, and what we did about it

Map matching shipped in M5, so OSM-derived identifiers exist in the defect database from
today rather than hypothetically. L-3 is still unanswered; these four decisions hold the
question open instead of quietly settling it.

1. **No OSM data is committed to this repository.** Extracts are fetched at run time into
   the git-ignored `data/roads/`, and the test suite uses invented geometry. A cached
   extract inside a proprietary tree would create the ambiguity, not merely risk it.
2. **`RoadSegmentRef` stays a separable reference**, not denormalised columns, so
   OSM-derived identifiers can be detached if L-3 resolves unfavourably.
3. **Attribution is derived from the data, never remembered by a caller.** A defect
   carrying `road.source == "osm"` puts the ODbL notice into the export itself — a
   top-level member in GeoJSON, a `.ATTRIBUTION.txt` sidecar beside a CSV, because a
   spreadsheet emailed to a municipality travels alone.
4. **An export that touched no OSM data carries no OSM notice.** Attributing
   unconditionally would be a false statement about provenance in the one field whose
   whole job is provenance.

Details in `docs/MAP_MATCHING.md`.

### L-6 and the redaction checkpoint

M5's redaction uses torchvision's COCO-pretrained `fasterrcnn_mobilenet_v3_large_fpn` to
find people and vehicles. That is a pretrained checkpoint, so L-6 applies — and it does
not block this use, for a reason worth writing down rather than assuming:

**The checkpoint is used locally, to destroy data.** It is never redistributed, never
shipped inside a product, never used to produce a defect that reaches a customer, and
contributes nothing to any model we train. A model that *removes* information from our
own files is a different exposure from one whose outputs we sell.

That distinction is the whole argument, so it has a limit: the moment a COCO-derived
checkpoint is used to produce a **defect** rather than to erase a pedestrian, L-6 must be
answered first. `RegionDetector` is a Protocol so the redaction model can be swapped if
L-6 resolves badly.

Note also what was *not* chosen. A licence-plate detector would have meant either
Ultralytics (BLOCKING-2) or training our own, and localising a plate is the first half of
ALPR — prohibited by ADR-007. Detecting whole vehicles avoids both.

---

## Maintenance rule

`THIRD_PARTY_LICENSES.md` must be updated **in the same commit** as any new dependency.
A dependency with an unknown or unverified license is treated as **REJECTED** until
verified — not as "probably fine".
