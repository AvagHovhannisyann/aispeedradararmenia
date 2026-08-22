"""Run the collector's TypeScript tests from the Python suite.

The collector's pure logic is tested by `node --test`, which needs no `npm install` —
Node 22 strips types on the fly. Running it from here means one `pytest` covers both
halves of the bundle contract, instead of a JavaScript suite nobody remembers to run.

**Skipped, not failed, when Node is absent.** `CLAUDE.md` requires the suite to pass on
a bare install with no optional dependency, and Node is exactly that. A hard requirement
here would mean a Python-only contributor could not run the tests at all.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS_GLOB = "apps/collector/tests/*.test.ts"

node = shutil.which("node")

pytestmark = [
    pytest.mark.skipif(node is None, reason="node is not installed"),
    pytest.mark.skipif(
        not (REPO_ROOT / "apps" / "collector" / "tests").is_dir(),
        reason="collector app not present in this checkout",
    ),
]


def node_major() -> int:
    assert node is not None
    out = subprocess.run([node, "--version"], capture_output=True, text=True, check=False)
    try:
        return int(out.stdout.strip().lstrip("v").split(".")[0])
    except ValueError:  # pragma: no cover - unparseable version string
        return 0


def test_collector_typescript_tests_pass():
    """Runs the same command a developer runs: `npm test` in apps/collector.

    Type stripping needs Node 22.6+. Below that the tests are skipped rather than
    reported as broken — the collector is fine, the runner is simply too old.
    """
    if node_major() < 22:
        pytest.skip(f"node {node_major()} is too old for --experimental-strip-types")

    assert node is not None
    result = subprocess.run(
        [
            node,
            "--experimental-strip-types",
            "--disable-warning=ExperimentalWarning",
            "--test",
            TESTS_GLOB,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )

    if result.returncode != 0:
        # The TAP output is what says which assertion failed; a bare exit code sends the
        # reader to the wrong place.
        pytest.fail(
            f"collector TypeScript tests failed:\n{result.stdout[-4000:]}\n{result.stderr[-2000:]}"
        )

    # A passing run that executed nothing is the failure mode worth guarding: a moved
    # directory or a changed glob would silently report success.
    assert "# pass " in result.stdout
    passed = int(result.stdout.split("# pass ")[1].split("\n")[0])
    assert passed > 0, f"node ran no collector tests:\n{result.stdout[-2000:]}"
