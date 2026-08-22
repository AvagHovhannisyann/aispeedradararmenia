"""Fail if survey data, a database or model weights have been committed.

`CLAUDE.md` rule 9 says never to commit survey video, frames, GPS logs, databases or
model weights, and notes that `.gitignore` is a safety net rather than permission. This
is the check that makes the rule enforceable instead of remembered.

It matters more than it looks, for two reasons.

**Git does not forget.** Deleting a file in a later commit leaves it in history. The
remedy is rewriting published history, which breaks every clone — so the only cheap
moment to catch this is before the push.

**The repository is public.** RoadEye is intended to be a proprietary product, and its
surveys will contain Yerevan streets with identifiable people and vehicles in them
(`docs/PRIVACY.md`). A frame committed here is a frame published to the world, and no
retention policy can reach it.

    .venv/bin/python scripts/check_no_survey_data.py

Exits non-zero and names every offending path. Run by CI on every push.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

#: Extension -> why it must not be here. Grouped by the sentence a reader needs, not by
#: file type: the point of the failure message is that somebody understands the risk in
#: the ten seconds before they reach for `git rm`.
FORBIDDEN_SUFFIXES: dict[str, str] = {
    ".mp4": "survey video",
    ".mov": "survey video",
    ".avi": "survey video",
    ".mkv": "survey video",
    ".m4v": "survey video",
    ".db": "a database — surveys, defects and review decisions",
    ".sqlite": "a database — surveys, defects and review decisions",
    ".sqlite3": "a database — surveys, defects and review decisions",
    ".pt": "model weights",
    ".pth": "model weights",
    ".onnx": "model weights",
    ".tflite": "model weights",
    ".safetensors": "model weights",
    ".weights": "model weights",
}

#: Filenames that are survey data whatever their extension. A bundle's `manifest.json`
#: is deliberately absent: it holds no imagery and no track, and a committed one would be
#: a useful fixture. This list is for files that say where somebody drove.
FORBIDDEN_NAMES: dict[str, str] = {
    "locations.jsonl": "a GPS log — where somebody actually drove",
}

#: Images are frames until proven otherwise. A screenshot for the documentation is a
#: real need, so one reviewed location is allowed and everywhere else is not — rather
#: than an allowlist of individual files, which rots into a list nobody reads.
IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp"})
IMAGE_HOME = "docs/images/"

#: Nothing here is legitimately large. A survey frame is 100 KB-1 MB, so this catches a
#: frame renamed to something the suffix list misses, without tripping on prose.
MAX_TRACKED_BYTES = 2 * 1024 * 1024


def tracked_files(root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [path for path in out.split("\0") if path]


def _reason(root: Path, path: str) -> str | None:
    """Why this path may not be tracked, or ``None`` if it is fine."""
    name = path.rsplit("/", 1)[-1]
    suffix = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""

    if by_suffix := FORBIDDEN_SUFFIXES.get(suffix):
        return by_suffix
    if by_name := FORBIDDEN_NAMES.get(name.lower()):
        return by_name
    if suffix in IMAGE_SUFFIXES and not path.startswith(IMAGE_HOME):
        return f"an image outside {IMAGE_HOME} — probably a survey frame"

    # A tracked path can be missing in a partial checkout; that is not this check's
    # business.
    full = root / path
    if full.is_file() and (size := full.stat().st_size) > MAX_TRACKED_BYTES:
        return f"{size / 1024 / 1024:.1f} MB — too large to be source or prose"
    return None


def offences(root: Path, paths: list[str]) -> list[tuple[str, str]]:
    return [(path, reason) for path in paths if (reason := _reason(root, path)) is not None]


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    found = offences(root, tracked_files(root))

    if not found:
        return 0

    print("Survey data, weights or a database are tracked by git:\n", file=sys.stderr)
    for path, reason in sorted(found):
        print(f"  {path}\n      {reason}", file=sys.stderr)
    print(
        "\nRemove them before pushing (CLAUDE.md rule 9). Git keeps history, and this\n"
        "repository is public — a committed frame is a published frame, and no retention\n"
        "policy can reach it afterwards.\n"
        "\n"
        "  git rm --cached <path> && echo '<path>' >> .gitignore\n"
        "\n"
        "If it has already been pushed, removing it from history is the only fix, and it\n"
        "invalidates every existing clone.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
