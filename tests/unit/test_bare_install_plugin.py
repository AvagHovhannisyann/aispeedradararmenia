"""The checker that checks the bare-install checker.

`scripts/bare_install_plugin.py` reproduces a machine that ran `pip install -e .` and
nothing else, so the repeated claim that most of RoadEye needs no optional dependency can
be verified in three seconds instead of trusted. It has already earned that: it caught a
test asserting a message the torchvision adapter only reaches once torch imports.

Its worth rests entirely on one list. A distribution added to an extra and not added to
`DISTRIBUTION_IMPORTS` is a dependency the checker silently stops hiding — the run still
passes, still says "bare install", and is now quietly wrong about one library. That is the
same failure the plugin exists to prevent, one level up, so it is checked here rather than
left to whoever adds the next extra remembering.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from bare_install_plugin import (  # noqa: E402
    DISTRIBUTION_IMPORTS,
    OPTIONAL_MODULES,
    OptionalDependencyBlocker,
)

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def declared_distributions() -> set[str]:
    """Every distribution named in an optional extra, normalised.

    `uvicorn[standard]>=0.27` is the distribution `uvicorn`; PEP 503 says names compare
    case-insensitively with `-`, `_` and `.` folded together.
    """
    config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    extras = config["project"]["optional-dependencies"]

    names: set[str] = set()
    for requirements in extras.values():
        for requirement in requirements:
            head = requirement.split(";")[0].strip()
            for separator in ("[", ">", "<", "=", "!", "~", " "):
                head = head.split(separator)[0]
            names.add(head.strip().lower().replace("_", "-").replace(".", "-"))
    return names


#: Testing tooling, not something the product imports. Hiding pytest from pytest would be
#: an interesting way to spend an afternoon.
NOT_A_RUNTIME_IMPORT = {"pytest", "pytest-cov", "ruff", "mypy"}


class TestTheListStaysInStep:
    def test_every_optional_distribution_is_accounted_for(self):
        missing = declared_distributions() - NOT_A_RUNTIME_IMPORT - set(DISTRIBUTION_IMPORTS)
        assert not missing, (
            f"these are in a pyproject extra but not in DISTRIBUTION_IMPORTS: {sorted(missing)}. "
            "Until they are, the bare-install check does not hide them and quietly stops "
            "covering them."
        )

    def test_nothing_is_listed_that_no_extra_declares(self):
        """A stale entry is harmless at runtime and misleading to read — it says an extra
        exists that does not."""
        stale = set(DISTRIBUTION_IMPORTS) - declared_distributions()
        assert not stale, f"no extra declares: {sorted(stale)}"

    def test_starlette_is_hidden_even_though_no_extra_names_it(self):
        """It arrives under FastAPI. Left importable, a test touching only
        `starlette.testclient` would pass on a machine that has neither."""
        assert "starlette" in OPTIONAL_MODULES


class TestTheBlocker:
    def test_refuses_an_optional_module_and_its_submodules(self):
        blocker = OptionalDependencyBlocker()

        for name in ("numpy", "numpy.linalg", "PIL.Image"):
            try:
                blocker.find_spec(name)
            except ModuleNotFoundError:
                continue
            raise AssertionError(f"{name} should have been blocked")

    def test_lets_everything_else_through(self):
        """Returning None hands the import back to the finders after it. Raising here
        would make the plugin hide the standard library too, and the suite would fail for
        reasons that say nothing about optional dependencies."""
        blocker = OptionalDependencyBlocker()

        assert blocker.find_spec("json") is None
        assert blocker.find_spec("roadeye.domain.models") is None
        assert blocker.find_spec("pydantic") is None, "pydantic is required, never hidden"
