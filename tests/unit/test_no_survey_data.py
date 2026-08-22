"""The guard that stops survey data reaching a public repository.

`CLAUDE.md` rule 9 forbids committing survey video, frames, GPS logs, databases or model
weights. `.gitignore` is the safety net; `scripts/check_no_survey_data.py` is the check
that makes the rule enforceable, and this is the check on the check.

Worth testing rather than trusting, because its failure is silent and permanent: git
keeps history, this repository is public, and a survey frame of a Yerevan street may
contain identifiable people (`docs/PRIVACY.md`). A guard that quietly stopped matching
would look exactly like a clean repository.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from check_no_survey_data import (  # noqa: E402
    MAX_TRACKED_BYTES,
    offences,
    tracked_files,
)

REPO = Path(__file__).resolve().parents[2]


class TestTheRepositoryItself:
    def test_nothing_forbidden_is_tracked_right_now(self):
        """The assertion the whole script exists to make."""
        found = offences(REPO, tracked_files(REPO))
        assert found == [], f"survey data or weights are tracked: {found}"


class TestWhatItCatches:
    @pytest.mark.parametrize(
        "path",
        [
            "demo_output/survey/video.mp4",
            "surveys/drive.MOV",
            "yerevan.db",
            "data/roadeye.sqlite3",
            "models/best.pt",
            "models/detector.onnx",
            "models/exported.safetensors",
            "demo_output/survey/locations.jsonl",
            "evidence/d1_context.jpg",
            "frames/000123.PNG",
        ],
    )
    def test_a_forbidden_path_is_reported(self, tmp_path, path):
        assert offences(tmp_path, [path]), f"{path} should have been caught"

    def test_the_reason_is_stated_not_just_the_path(self):
        """A bare list of filenames does not tell somebody why it matters, and this
        failure needs to be understood before the next `git commit --amend`."""
        (path, reason) = offences(REPO, ["surveys/drive.mp4"])[0]
        assert path == "surveys/drive.mp4"
        assert "video" in reason

    def test_a_large_file_is_caught_whatever_it_is_called(self, tmp_path):
        """Renaming a frame to `notes.txt` defeats a suffix list. Nothing in this
        repository is legitimately megabytes long."""
        big = tmp_path / "notes.txt"
        big.write_bytes(b"\0" * (MAX_TRACKED_BYTES + 1))

        found = offences(tmp_path, ["notes.txt"])

        assert found and "too large" in found[0][1]


class TestWhatItAllows:
    @pytest.mark.parametrize(
        "path",
        [
            "src/roadeye/cli.py",
            "docs/PRIVACY.md",
            "apps/collector/app.json",
            "demo_output/survey/manifest.json",
            "docs/images/dashboard.png",
            "package-lock.json",
        ],
    )
    def test_ordinary_source_and_prose_pass(self, tmp_path, path):
        assert offences(tmp_path, [path]) == []

    def test_documentation_screenshots_have_one_home(self, tmp_path):
        """An image in `docs/images/` is documentation someone chose to add. The same
        image anywhere else in a road-survey repository is a frame until proven
        otherwise — one reviewed location beats an allowlist nobody re-reads."""
        assert offences(tmp_path, ["docs/images/streets-panel.png"]) == []
        assert offences(tmp_path, ["docs/streets-panel.png"]) != []
