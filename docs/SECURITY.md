# RoadEye — Security

**Status:** MVP is localhost-only. These are the practices that hold regardless.

MVP threat model is modest — one laptop, one operator, no network service. But RoadEye
handles **evidence** that may support municipal decisions and **video that may contain
personal data**, so some habits must be right from the start rather than retrofitted.

## Implemented

### Input validation at the boundary

Survey bundles come from a phone and are, formally, untrusted input.

- **Survey ids are restricted to `[A-Za-z0-9._-]+`** and rejected otherwise. Ids reach
  filesystem paths and export filenames; refusing `../` and separators once at the
  boundary is cheaper and safer than sanitising at every use site. Tested against
  traversal attempts.
- **Schema versions newer than the build are refused.** Guessing at an unknown format
  would silently misinterpret a survey — worse than refusing it.
- **Malformed JSON/JSONL lines are skipped and counted**, never executed or trusted.
- **Domain models reject unknown fields** (`extra="forbid"`), so unexpected input fails
  loudly.

### SQL

- All queries are parameterised.
- Table names cannot be parameterised in SQL, so `Database.count()` uses an
  **allowlist** rather than interpolating caller input. Tested with an injection-shaped
  argument.
- Foreign keys are explicitly enabled (SQLite defaults them off).

### Evidence integrity

- Raw survey bundles are **read-only inputs**. The pipeline never mutates them.
- `Survey` is immutable after ingest.
- `reviews` is append-only — there is no update or delete method, not merely a
  convention.
- Every defect traces to a `ProcessingRun` carrying the full config and git commit.

### Secrets

- No secrets in the repository. No API keys are needed — RoadEye calls no paid service
  at runtime (ADR-005).
- `.gitignore` excludes databases, video, model weights and survey directories.

## Not yet implemented

| Control | Needed by |
|---|---|
| Authentication / authorisation on the API | First non-localhost deployment |
| Access logging on evidence images | M6 (`PRIVACY.md`) |
| Encryption at rest for survey data | Municipal deployment |
| Manifest checksums verified on ingest | When bundles are transferred between machines |
| Rate limiting | First public endpoint |
| Signed model artefacts | When models are distributed |

The API is **not built**. When it is, it must not bind beyond localhost without
authentication.

## Rules

1. **Never commit** survey video, frames, GPS logs, databases or model weights.
2. **Never disable TLS verification** to work around a proxy or certificate error.
3. **Never construct SQL by string interpolation**, including table names — use the
   allowlist pattern.
4. **Never trust bundle contents** as paths, commands or code.
5. **Never mutate raw evidence.** Derived artefacts go in new files.
6. **Never add a dependency without recording its licence** in
   `THIRD_PARTY_LICENSES.md` in the same commit.

## Reporting

Pre-pilot, report issues directly to the founder. Before any municipal deployment,
establish a disclosure contact and process — a government customer will ask.
