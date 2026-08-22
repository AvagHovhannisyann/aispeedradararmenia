# ADR-010 — Build the dashboard without a build step

**Status:** Accepted (2026-08-22). Supersedes the "React + TypeScript + MapLibre" line
in `docs/MILESTONES.md` M6.

## Context

M6 was planned as a React + TypeScript single-page app under `apps/dashboard/`, built
with Vite and served as static assets. That was written during M0, before there was any
UI in the repository.

By the time M6 arrived, two things had changed.

**A working UI already existed with no build step.** `services/api/static/review.html`
is the keyboard-driven review screen from M4: plain HTML, CSS and JavaScript served
directly by the existing FastAPI app. It is fast, it is legible, and its whole
"deployment" is `roadeye review --db yerevan.db`.

**The friction that actually costs this project is setup friction, not typing.** The
recurring failure in this repository's history has not been a shortage of abstraction. It
has been someone unable to *run* the thing — a demo whose output landed in a git-ignored
directory nobody could find, a detector whose threshold silently did nothing. A solo
founder on a borrowed laptop, in a Codespace, showing a municipality a map, is one
`npm install` away from not showing them anything.

Adding React + Vite would mean `node_modules` (hundreds of megabytes), a build step
between changing a line and seeing it, a second dev server or a build artefact to keep in
sync with the Python that serves it, and a toolchain that ages. None of that buys
anything the acceptance criterion asks for: *a non-technical person understands it
unaided and can review a defect in seconds.*

## Decision

**The dashboard is static HTML, CSS and JavaScript, served by the existing FastAPI app.**
No bundler, no framework, no `node_modules`, no build step.

1. Assets live in `services/api/static/`, beside the review UI, split into separate
   `.html`, `.css` and `.js` files rather than one large page — small modules without a
   module bundler.
2. MapLibre GL JS (BSD-3-Clause) is loaded from a CDN, as the existing map preview
   already does. The page degrades to a legible error rather than a blank screen when it
   cannot load.
3. `apps/dashboard/` is **removed** rather than left as an empty promise.

## What would change this

Written down now, so the decision can be revisited on evidence rather than taste:

- **More than one person editing the UI.** Component boundaries earn their cost once
  merges start conflicting.
- **The page passing roughly 1,500 lines**, or any single file passing ~600. That is the
  point at which "small modules, no bundler" stops being true.
- **Client-side state that outgrows the DOM** — multi-step flows, optimistic updates,
  undo across views. Filters and a detail panel do not.
- **A municipality wanting it hosted for their own staff.** Hosting changes the security
  posture entirely (this thing has no authentication), and that rewrite is the moment to
  reconsider the toolchain.

Any of those triggers is a reason to build the React app then, on a design proven in the
plain version, rather than now on a guess.

## Alternatives considered

**React + Vite as planned.** Rejected for the reasons above. Note what is *not* claimed:
that React is wrong, or heavy, or unnecessary in general. It is a cost paid up front for
benefits that arrive with scale this project does not have yet.

**Vendor MapLibre into the repository** so the dashboard works with no network at all.
Attractive — RoadEye is offline-first, and a dashboard that needs the internet to draw a
map is a poor fit for that. Deferred rather than rejected: it is ~800 KB of third-party
minified JavaScript committed to a proprietary tree, and the honest version of this is
self-hosting *tiles* as well, which is a bigger piece of work (see below). Recorded as
the open item in `docs/DASHBOARD.md`.

**Server-rendered HTML from FastAPI** (Jinja, htmx). Would work. Rejected only because a
map is inherently a client-side, stateful thing, and the existing review UI already
established the fetch-JSON-and-render pattern — consistency beat a marginal preference.

## Consequences

Good:
- `roadeye dashboard --db yerevan.db` is the entire deployment.
- Editing the UI is: change a file, reload the page.
- No JavaScript dependency tree to licence-audit, pin or update.
- The dashboard cannot drift out of sync with a built artefact, because there isn't one.

Bad:
- No type checking on the UI. Python remains `mypy`-clean; the JavaScript is not checked
  by anything, and the API contract between them is enforced only by tests on the Python
  side.
- No component reuse between the review screen and the dashboard. Some markup is
  duplicated. Accepted deliberately: two similar pages is a cheaper problem than a
  toolchain.
- **The dashboard needs the network to draw a map**, for both MapLibre and tiles. That is
  a real conflict with offline-first and is documented rather than hidden.

## Related

The tile source is a separate and unresolved problem. `tile.openstreetmap.org` is
donated capacity under a Tile Usage Policy that explicitly excludes production use, so it
is fine for a founder's laptop and **not** fine for a municipal deployment
(`docs/LICENSE_AUDIT.md`). That constraint is independent of this decision and would
apply to a React app identically.
