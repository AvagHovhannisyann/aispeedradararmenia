# ADR-005 — No runtime LLM or AI API dependency

**Status:** Accepted (2026-08-22)

## Context

RoadEye is being built with Claude Code. It would be easy to conflate "built with AI"
and "runs on AI", and to reach for an API call at inference time.

Also relevant: a Claude Max subscription **does not include Anthropic API credits** —
Console/API usage is billed separately.

## Decision

**Claude Code is the development agent. RoadEye calls no AI API at runtime.**

No Anthropic (or other provider) SDK appears in any dependency manifest. Road frames are
never sent to a hosted model.

## Alternatives considered

- **Vision-LLM inference per frame.** Rejected: per-inference cost, latency, an internet
  requirement, non-deterministic output, and sending street video containing personal
  data to a third party (`PRIVACY.md`).
- **LLM-assisted review triage.** Deferred; would need its own privacy analysis.

## Consequences

Good:
- $0 marginal inference cost, forever.
- Works offline — including in a vehicle with no signal.
- Deterministic and reproducible, which a government-facing audit trail requires.
- No survey imagery leaves the machine.

Bad:
- We must train and maintain our own detector.
- No "free" capability jumps from a provider's model upgrades.

## Enforcement

Absence of the dependency is the enforcement, restated in `CLAUDE.md` so future sessions
do not add one casually.
