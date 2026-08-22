# Architecture Decision Records

One file per significant, hard-to-reverse decision. Format: context, decision,
alternatives, consequences, status.

An ADR is not documentation of what the code does — it records *why* a choice was made,
so that a future reader (or a future Claude Code session) can tell the difference
between a deliberate constraint and an accident, and knows what evidence would justify
revisiting it.

| ADR | Decision | Status |
|---|---|---|
| [001](ADR-001-offline-first.md) | Offline-first processing; the phone only collects | Accepted |
| [002](ADR-002-expo-collector.md) | Expo/React Native collector for MVP | Accepted |
| [003](ADR-003-sqlite-mvp.md) | SQLite + R*Tree for MVP storage | Accepted |
| [004](ADR-004-detector-abstraction.md) | Detector behind a Protocol | Accepted |
| [005](ADR-005-no-runtime-llm.md) | No runtime LLM/AI API dependency | Accepted |
| [006](ADR-006-human-verification.md) | Human verification before municipal action | Accepted |
| [007](ADR-007-privacy-retention.md) | Privacy-first retention and anonymisation | Accepted |
| [008](ADR-008-route-disjoint-splits.md) | Route-disjoint ML evaluation splits | Accepted |
| [009](ADR-009-reject-ultralytics.md) | Reject Ultralytics; quarantine RDD2022 lineage | Accepted |
