"""Run the test suite as if only `pip install -e .` had ever been run.

The README, CLAUDE.md and ARCHITECTURE.md all make the same promise: most of RoadEye's
tests need no optional dependency, so the suite still runs on a laptop with no ML stack,
no ffmpeg and no network. A promise nobody checks is a promise that quietly stops being
true — the founder's machine has the extras installed, so a test that secretly depends on
one passes there and fails on the bare machine the claim is about.

Building a second virtualenv to check would work and takes minutes. This takes three
seconds: a meta-path finder that refuses every optional import, so `pytest.importorskip`
skips exactly as it would on a bare install.

    PYTHONPATH=scripts .venv/bin/python -m pytest -p bare_install_plugin

It has already earned its keep. `test_missing_weights_raises_detector_error` asserted the
message "weights not found", which the constructor only reaches once torch imports; on a
bare machine it says "torch is required" instead, and the test failed.

This is a *development* tool. It is never imported by the package, and the default
`pytest` run does not load it.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from importlib.abc import MetaPathFinder
from importlib.machinery import ModuleSpec
from types import ModuleType

#: Distribution name in pyproject.toml -> the name you `import`. Mostly identical; the
#: interesting rows are the ones that are not, which is why this is a map rather than a
#: set. `tests/unit/test_bare_install_plugin.py` fails if an extra names a distribution
#: that is missing here — the alternative is a checker that quietly stops covering a
#: dependency somebody added six months ago, which is the exact failure it was built to
#: prevent, one level up.
DISTRIBUTION_IMPORTS: dict[str, str] = {
    "av": "av",  # video
    "numpy": "numpy",  # video, quality, vision
    "torch": "torch",  # vision
    "torchvision": "torchvision",  # vision
    "pillow": "PIL",  # review, vision
    "fastapi": "fastapi",  # api, review
    "uvicorn": "uvicorn",  # api, review
    "httpx": "httpx",  # dev — drives the FastAPI TestClient in-process
}

#: What the blocker hides. `starlette` is here and not above because no extra names it:
#: it arrives under FastAPI, and leaving it importable would let a test that only touches
#: `starlette.testclient` pass on a machine that has neither.
OPTIONAL_MODULES = frozenset(set(DISTRIBUTION_IMPORTS.values()) | {"starlette"})


class OptionalDependencyBlocker(MetaPathFinder):
    """Raises ModuleNotFoundError for anything an extra would have installed.

    Raising from `find_spec` rather than returning None is deliberate: returning None
    would let a later finder resolve the module, and the point is that nothing should.
    ModuleNotFoundError subclasses ImportError, so every `try: import torch / except
    ImportError` path behaves precisely as it does on a machine without it.
    """

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None = None,
        target: ModuleType | None = None,
    ) -> ModuleSpec | None:
        root = fullname.partition(".")[0]
        if root in OPTIONAL_MODULES:
            raise ModuleNotFoundError(f"No module named {root!r}", name=root)
        return None


def pytest_configure(config: object) -> None:
    """Install the blocker before collection, and evict anything already imported."""
    for name in list(sys.modules):
        if name.partition(".")[0] in OPTIONAL_MODULES:
            del sys.modules[name]
    sys.meta_path.insert(0, OptionalDependencyBlocker())
