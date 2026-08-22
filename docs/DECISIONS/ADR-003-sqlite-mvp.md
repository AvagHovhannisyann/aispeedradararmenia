# ADR-003 — SQLite + R*Tree for MVP storage

**Status:** Accepted (2026-08-22)

## Context

RoadEye needs spatial queries ("defects near this point") and transactional writes. The
production answer is PostgreSQL + PostGIS. The MVP runs on one laptop with no budget.

## Decision

**SQLite with the R*Tree module** for MVP. Document the PostGIS migration path; do not
build an abstraction layer for it.

## Alternatives considered

- **PostgreSQL + PostGIS now.** Correct destination, wrong time: adds an install step
  and a running service for zero MVP benefit.
- **SpatiaLite.** "PostGIS without a server" — attractive, but a tri-licence (MPL/GPL/
  LGPL) needing care, and R*Tree already covers our queries.
- **A generic ORM abstraction** to ease the future port. Rejected: a speculative
  abstraction costs more than the eventual port and obscures the code meanwhile.
- **Flat files.** No transactions, no spatial index.

## Consequences

Good:
- Zero install, single file, transactional, trivially backed up.
- R*Tree gives real spatial indexing with no server.

Bad:
- Single-writer; no concurrent multi-user access.
- Weak typing; enum values are validated in Python, not the database.
- A future port is real work (though roughly a day at this schema size).

Notes:
- Foreign keys must be enabled explicitly — SQLite defaults them **off**, and without
  the pragma cascading deletes silently do nothing.
- R*Tree stores bounding boxes, so radius queries must **index-then-refine**: widen the
  query box, then filter by true distance.

## Revisit when

More than one concurrent writer, a hosted deployment, or genuine multi-user access.
