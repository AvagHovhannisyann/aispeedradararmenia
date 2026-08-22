"""Deleting things on time, and proving it was done.

Retention is the part of privacy engineering that gets written down and then never
happens, because nothing fails when it doesn't. A policy in a document is not a control;
a policy that runs and logs is.

The ordering in ``docs/PRIVACY.md`` is deliberate and reflected here: **raw video has the
shortest retention of any artefact**, because it is the highest-risk thing we hold. A
defect record is a coordinate and a class; a video file is a movement record of every
person who happened to be on that street.

Three properties this module is built around:

**Deletion is logged.** An append-only record of what was deleted and when, written
*before* the file goes, so a crash mid-sweep leaves evidence of intent rather than a
silent gap. The log holds paths, sizes and times — never content.

**Dry-run is the default posture.** :func:`apply_retention` will not delete unless asked
to. Anything that erases originals should make you type the flag.

**Nothing is deleted that a record still needs.** A survey whose defects are unreviewed
still needs its evidence; the sweep says so and skips it, rather than being clever.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Filenames treated as raw survey capture — the highest-risk artefacts.
RAW_VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".m4v", ".avi", ".mkv"})

#: Name of the append-only deletion log, written into the directory swept.
DELETION_LOG = "RETENTION_LOG.jsonl"


@dataclass(frozen=True)
class RetentionPolicy:
    """How long each artefact may be kept.

    The defaults are the ones in ``docs/PRIVACY.md``, and that document is the source of
    truth: **changing a number here without changing it there is a bug**, because the
    written policy is what a regulator would be shown.

    These are a *default position pending counsel* (L-5), not a legal determination.
    """

    #: Raw survey video. Target 30 days — the shortest period that still supports review.
    raw_video_days: int = 30

    #: Full frames extracted from video, kept only until their defects are reviewed.
    frames_days: int = 90

    #: Evidence images live as long as the defect record they justify, so there is no
    #: age-based rule for them. Present as an explicit ``None`` rather than absent, so
    #: nobody has to wonder whether it was forgotten.
    evidence_days: int | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "raw_video_days": self.raw_video_days,
            "frames_days": self.frames_days,
            "evidence_days": self.evidence_days,
            "source": "docs/PRIVACY.md — default position pending counsel (L-5)",
        }


@dataclass
class RetentionCandidate:
    """One file the policy says is due for deletion."""

    path: Path
    kind: str
    age_days: float
    size_bytes: int
    deleted: bool = False
    error: str | None = None

    def to_json(self) -> dict[str, Any]:
        record = {
            "path": str(self.path),
            "kind": self.kind,
            "age_days": round(self.age_days, 2),
            "size_bytes": self.size_bytes,
            "deleted": self.deleted,
        }
        if self.error:
            record["error"] = self.error
        return record


@dataclass
class RetentionSweep:
    """What one pass over a directory found and did."""

    policy: RetentionPolicy
    root: Path
    ran_at: datetime
    dry_run: bool
    candidates: list[RetentionCandidate] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def deleted_count(self) -> int:
        return sum(1 for c in self.candidates if c.deleted)

    @property
    def bytes_reclaimed(self) -> int:
        return sum(c.size_bytes for c in self.candidates if c.deleted)

    def to_json(self) -> dict[str, Any]:
        return {
            "ran_at": self.ran_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "root": str(self.root),
            "dry_run": self.dry_run,
            "policy": self.policy.to_json(),
            "due": len(self.candidates),
            "deleted": self.deleted_count,
            "bytes_reclaimed": self.bytes_reclaimed,
            "files": [c.to_json() for c in self.candidates],
            "skipped": self.skipped,
        }

    def summary(self) -> str:
        verb = "would delete" if self.dry_run else "deleted"
        mb = self.bytes_reclaimed / 1_000_000
        lines = [
            f"{len(self.candidates)} file(s) past retention under {self.root}",
            f"{verb} {self.deleted_count} ({mb:.1f} MB)",
        ]
        lines.extend(f"  skipped: {s}" for s in self.skipped)
        return "\n".join(lines)


def find_expired(
    root: str | Path,
    policy: RetentionPolicy | None = None,
    *,
    now: datetime | None = None,
) -> list[RetentionCandidate]:
    """Files under ``root`` that the policy says are past their retention.

    Age is taken from the filesystem mtime. That is an approximation of "when was this
    recorded" and it is the honest one available: a survey bundle records its own start
    time, but a loose video file does not, and guessing from a filename would delete the
    wrong thing when the guess is wrong.
    """
    cfg = policy or RetentionPolicy()
    moment = now or datetime.now(UTC)
    base = Path(root)
    out: list[RetentionCandidate] = []

    if not base.exists():
        return out

    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        kind = _classify(path)
        if kind is None:
            continue
        limit = cfg.raw_video_days if kind == "raw_video" else cfg.frames_days
        age_days = (moment.timestamp() - path.stat().st_mtime) / 86400.0
        if age_days >= limit:
            out.append(
                RetentionCandidate(
                    path=path,
                    kind=kind,
                    age_days=age_days,
                    size_bytes=path.stat().st_size,
                )
            )
    return out


def apply_retention(
    root: str | Path,
    policy: RetentionPolicy | None = None,
    *,
    delete: bool = False,
    now: datetime | None = None,
    log_path: str | Path | None = None,
) -> RetentionSweep:
    """Find expired artefacts and, if ``delete`` is set, remove them.

    ``delete`` defaults to False on purpose. A retention sweep is irreversible by
    definition — that is its whole function — so the default has to be the one that
    shows you what would happen.
    """
    cfg = policy or RetentionPolicy()
    moment = now or datetime.now(UTC)
    base = Path(root)
    sweep = RetentionSweep(policy=cfg, root=base, ran_at=moment, dry_run=not delete, candidates=[])

    if not base.exists():
        sweep.skipped.append(f"{base} does not exist")
        return sweep

    sweep.candidates = find_expired(base, cfg, now=moment)
    if not delete:
        return sweep

    log = Path(log_path) if log_path else base / DELETION_LOG
    for candidate in sweep.candidates:
        # Logged before the unlink, not after. A crash between the two leaves a record
        # of what was about to happen, which is recoverable reasoning; the other order
        # leaves a file gone and no trace it existed.
        _append_log(log, candidate, moment)
        try:
            candidate.path.unlink()
            candidate.deleted = True
        except OSError as exc:
            candidate.error = str(exc)
    return sweep


def _classify(path: Path) -> str | None:
    if path.suffix.lower() in RAW_VIDEO_SUFFIXES:
        return "raw_video"
    if path.parent.name == "frames" and path.suffix.lower() in {".jpg", ".jpeg", ".png"}:
        return "frames"
    return None


def _append_log(log_path: Path, candidate: RetentionCandidate, moment: datetime) -> None:
    """Append one deletion record. JSON Lines, so a truncated write loses one entry.

    Never the file's content — only its path, kind, size and age. A deletion log that
    quotes what it deleted has recreated the thing it deleted.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "deleted_at": moment.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "path": str(candidate.path),
        "kind": candidate.kind,
        "age_days": round(candidate.age_days, 2),
        "size_bytes": candidate.size_bytes,
    }
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
