# RoadEye — Privacy by Design

**Status:** design position. Legal basis **NOT YET ESTABLISHED** — see Open Questions.
**Last verified:** 2026-08-22

> This document is engineering policy, **not legal advice**. Items L-4 and L-5 must be
> resolved with Armenian counsel and/or the Personal Data Protection Agency before any
> municipal deployment.

## The problem, stated plainly

A windshield camera driving through Yerevan records:

- pedestrians' faces
- vehicle license plates
- people entering and leaving buildings
- residential addresses and property
- who was where, and when

That last item is the serious one. A road-condition dataset is incidentally a **movement
record of identifiable people**. "It was filmed in public" is not a legal analysis and
is not a defence.

## Legal context (preliminary)

- Armenia's governing statute is the **Law on Protection of Personal Data, HO-49-N**,
  adopted 18 May 2015, regulating processing by public and private entities.
- The supervisory body is the **Personal Data Protection Agency at the Ministry of
  Justice**, which launched a public guidance site in H2 2024.
- Armenian video-surveillance guidance treats recorded imagery of people as personal
  data and emphasises lawfulness and proportionality.
- The Armenian Constitution requires personal-data processing to be in good faith, for a
  lawful purpose, on the basis of consent or another legitimate basis provided by law.
- **The landscape is actively changing.** A 2024 Interior Ministry bill proposing
  mandatory CCTV for many private entities drew significant human-rights criticism.
  Re-verify before deployment.

## Commercial precedent

Vaisala's RoadAI **anonymises every collected video before it appears in their web
app**, masking vehicles and people, and states this does not interfere with detecting
signs, cracking or potholes.

The RDD2022 dataset authors likewise blurred faces and license plates before public
release, based on visual inspection.

Both confirm the same thing: **anonymisation and road-defect detection are compatible.**
There is no engineering trade-off to agonise over here.

## What RoadEye will NOT do

Hard architectural boundaries, not preferences. Crossing any of them is a different
product with a different legal analysis:

| Prohibited in V1 | Why |
|---|---|
| Face recognition | Biometric processing; far higher legal bar |
| Person identification or re-identification | Not needed to find a pothole |
| Licence-plate recognition (ALPR) | Turns a road survey into vehicle surveillance |
| Plate-database matching | Requires authority we do not have |
| Individual movement tracking | Direct privacy harm |
| Automated traffic enforcement | Administrative law, evidence rules, appeals |
| Uploading raw video to third-party services | Includes free GPU services (Colab/Kaggle) |

The last row has a concrete engineering consequence: **raw Armenian survey video must
not be uploaded to free compute platforms for training.** Only anonymised, cropped,
road-surface-only derivatives may leave the local machine, and only after the retention
and redaction pipeline below exists.

## The temptation to avoid

At some point it will occur to someone that RoadEye could detect vehicles blocking bus
stops and issue fines automatically. Resist it, and not only for legal reasons:

| Road damage | Automated enforcement |
|---|---|
| Physical infrastructure | Individual conduct |
| No need to identify anyone | Requires identifying a person |
| Low controversy | High controversy |
| A wrong detection wastes an inspector's time | A wrong detection wrongly fines a citizen |
| Obvious municipal need | Requires legal authority, evidence standards, appeals |

Build the infrastructure-inspection company first. The enforcement product, if ever, is
a separate company-level decision with separate counsel.

## Design: data minimisation as architecture

```
raw video (device)
      |  manual transfer, never automatic upload
      v
local processing on the founder's machine
      |
      +--> road-surface crops around detections   <-- retained (evidence)
      +--> full frames                            <-- retained only while needed
      +--> raw video                              <-- deleted per retention policy
      |
      v
anonymisation: blur faces + plates/vehicles in anything retained
      |
      v
defect records: coordinates, class, confidence, uncertainty
      (contain no personal data)
```

The key structural point: **the defect database itself contains no personal data.** A
defect is a coordinate, a class, a confidence and an uncertainty. Personal data exists
only in the *evidence images*, which is a much smaller, more controllable surface than
"all our survey footage".

### Retention (default position, pending counsel)

| Artefact | Default retention | Rationale |
|---|---|---|
| Raw survey video | **Shortest period supporting review** — target 30 days | Highest-risk artefact; delete first |
| Evidence crops (anonymised) | Life of the defect record | Needed to justify a work order |
| Full frames | Until defects verified, then delete | Transitional |
| Defect records | Indefinite | No personal data |
| GPS tracks | Life of the survey record | Reveals the *driver's* movements — treat as sensitive |

GPS tracks deserve a note: they are a precise movement record of whoever drove. For a
municipal fleet that is an employment-monitoring question; for a volunteer driver it is
a personal one. Either way it needs a stated basis.

## Implementation plan

`src/roadeye/privacy/` is currently a placeholder package. That is honest: the
anonymisation pipeline is **not built**. Planned:

| Milestone | Capability |
|---|---|
| M4 | Retention policy enforcement — delete raw video on schedule, log the deletion |
| M4 | Redaction interface (`Anonymizer` protocol) mirroring the detector seam |
| M5 | Face + plate blurring using a permissively licensed detector (licence-audited) |
| M6 | Access logging on evidence images |
| M6 | Deletion-request handling (find and purge everything for a given time/place) |
| Pre-pilot | Data Protection Impact Assessment; counsel review |

**Until face/plate blurring exists, survey video must not be shared outside the
founder's machine — not in demos, not in pull requests, not in a pitch deck.** A
screenshot of a Yerevan street with a readable plate is a disclosure.

## Open questions for counsel

| ID | Question |
|---|---|
| L-4 | What is the lawful basis under HO-49-N for recording public-road video that incidentally captures identifiable people and plates? |
| L-5 | What retention period and access controls are required for raw survey video? |
| L-7 | Does a municipal contract change the basis (public task) versus private research? |
| L-8 | Is a DPIA (or Armenian equivalent) required before a municipal pilot? |
| L-9 | What notice, if any, is owed to people recorded — signage on the survey vehicle? |
| L-10 | Are GPS traces of a named driver subject to employment-monitoring rules? |

## Rules for anyone working on this repository

1. **Never commit survey video, frames or GPS logs.** `.gitignore` blocks the obvious
   patterns; that is a safety net, not permission to try.
2. **Never upload raw survey data to a third-party service**, including free GPU
   platforms and AI APIs.
3. **Never add face or plate *recognition*.** Detection-for-blurring only, and blurring
   must not retain the identifying crop.
4. **Never widen retention** without updating this document in the same commit.
5. When in doubt, **collect less**.
