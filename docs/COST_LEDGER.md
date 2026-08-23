# RoadEye — Cost Ledger

**Question every architectural proposal must answer:** *what does this cost the founder
today?*

**Last reviewed:** 2026-08-22

## Required now — incremental cost

| Item | Cost | Note |
|---|---|---|
| Development machine | **$0** | Already owned. Windows, **GTX 1660 Ti 6 GB** (confirmed 2026-08-23) |
| Smartphone | **$0** | Already owned |
| Python, PyTorch, torchvision | **$0** | BSD-3 |
| Expo / React Native | **$0** | MIT |
| SQLite | **$0** | Public domain |
| MapLibre GL JS | **$0** | BSD-3 |
| FastAPI / Pydantic | **$0** | MIT |
| Label Studio CE / CVAT CE | **$0** | Apache-2.0 / MIT |
| Git + GitHub | **$0** | Free tier |
| Hosting (localhost) | **$0** | |
| Claude Code | already paid | Development agent, **not** a runtime dependency |
| **Total incremental** | **≈ $0** | |

Petrol for survey drives is a real cost, and the only unavoidable one at MVP. It is
small and scales with how much data you want.

## Free-tier dependent — zero cost, zero guarantee

| Service | Limit | Risk if withdrawn |
|---|---|---|
| **Kaggle GPU** | ~30 GPU-h/week, commonly P100 16 GB | Training slows to local CPU |
| **Google Colab** | **No guaranteed GPU.** Limits, idle timeout, max VM lifetime and GPU types all "vary over time" and are unpublished; sessions cap ~12 h | Backup only |
| GitHub free tier | Repo + Actions minutes | Low |
| **GitHub Actions** | **Unlimited minutes — the repository is public.** A private repo would fall back to 2,000 minutes/month | Low. The same four gates run locally in ~20 s; CI is the thing that remembers, not the thing that knows |

**Rule:** these are development resources with **no SLA**. Nothing in production may
depend on them; training scripts must checkpoint and resume, because eviction is normal.

**The public-repository trade, stated once.** Actions is free here because the repository
is public. That is a real decision and not only a billing one: every architectural choice,
threshold and licence finding in this project is world-readable, and the `Proprietary`
licence restricts *use*, not *reading*. It also raises the cost of a mistake — a survey
frame committed to a public repository is a published frame, which is why
`scripts/check_no_survey_data.py` runs on every push. Going private costs $0 on the Free
plan and would cap Actions at 2,000 minutes/month, which is far more than this uses.

**Privacy rule:** raw Armenian survey video must never be uploaded to them
(`PRIVACY.md`).

That rule used to have an awkward consequence: it forbade the only GPUs this project had
access to from touching the data the shipping model must be trained on, leaving M7 with a
laptop CPU. The founder's machine turns out to have a **GTX 1660 Ti (6 GB)**, which
resolves it at zero cost — adequate for Faster R-CNN + MobileNetV3, and local, so the
privacy rule and the compute plan stop contradicting each other. Kaggle keeps its role for
public datasets like RDD2022, where 16 GB of P100 is genuinely faster and no rule applies.

## Deferred costs — not yet incurred, foreseeable

| Item | Cost | Trigger |
|---|---|---|
| **Apple Developer Program** | $99/year | Only when a pilot needs stable iOS distribution. Free provisioning (3 devices, 10 App IDs, 7-day profile expiry) covers development |
| Phone mount | ~$10-30 | Before the first real drive — the one thing worth buying early |
| Map tiles in production | varies | `tile.openstreetmap.org` is explicitly not a production service; self-host or use a provider |
| Server hosting | ~$5-40/mo | Only when someone outside the laptop needs access |
| PostgreSQL/PostGIS | included in hosting | At production scale |
| Ultralytics Enterprise | — | **N/A — avoided by design** (`LICENSE_AUDIT.md`) |
| Legal counsel (Armenian data protection) | varies | **Before any municipal deployment.** Not optional |
| External storage for surveys | ~$50-100 one-off | When local disk fills — 1080p30 is roughly 100 MB/min |

That last row is worth planning for: a 30-minute survey is ~3 GB, so twenty surveys
fill 60 GB. Disk is the first physical constraint this project will hit.

## Deliberately avoided costs

| Avoided | How |
|---|---|
| Paid AI API calls at runtime | ADR-005 — no Anthropic/OpenAI SDK; models run locally |
| AGPL compliance obligations | Ultralytics rejected for the shipping path |
| Cloud infrastructure | Offline-first; SQLite; localhost |
| Mapbox licensing | MapLibre (BSD-3) instead |
| Paid annotation tooling | Label Studio CE / CVAT CE |
| Managed MLOps | Local experiment directories with metadata |
| Docker as a requirement | Optional, for CVAT only |

## The Claude Max misconception, stated explicitly

A Claude Max subscription **does not include Anthropic API credits.** Console/API usage
is billed separately from Pro/Max subscription pricing.

This actually points at the right architecture: Claude Code is the **development
agent**. RoadEye itself must not call any AI API at runtime. Not

```
camera → Claude API → "is this a pothole?"
```

but

```
Claude Code → builds → ordinary software + a local CV model → runs offline, $0/call
```

Anything else would add per-inference cost, latency, an internet requirement and
non-deterministic behaviour to a government-facing product.

## Review triggers

Revisit this ledger when: a new dependency is added; a free tier changes terms; the
first external user appears; or a municipal pilot is agreed.
