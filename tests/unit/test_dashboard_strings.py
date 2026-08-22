"""The Armenian and English string tables must stay in step.

RoadEye's dashboard is Armenian by default because its readers work for Yerevan's
municipality (`docs/DASHBOARD.md`). That promise rests entirely on one `STRINGS` table
in `dashboard.js`, and its lookup falls back to English:

    return (STRINGS[state.lang] && STRINGS[state.lang][key]) || STRINGS.en[key] || key;

The fallback is right — an English word beats a raw key like `coverageOf` on screen — but
it is *silent*. Add a key to `en` and forget `hy`, and the Armenian interface quietly
starts showing English to the one reader it was built for. Nothing on the page says so,
and nobody who does not read Armenian would notice.

So it is checked here, in Python, with no browser and no npm: the file is text, and the
question is only whether two sets of keys match. This is the cheapest possible guard on
the product's most visible commitment.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DASHBOARD_JS = Path(__file__).resolve().parents[2] / "services/api/static/dashboard.js"

#: A key line inside a language table: four spaces, an identifier, a colon. Continuation
#: lines of a concatenated string are indented further and so never match.
KEY_LINE = re.compile(r"^    (\w+):", re.M)


def keys_for(language: str) -> list[str]:
    source = DASHBOARD_JS.read_text(encoding="utf-8")
    start = source.index(f"\n  {language}: {{\n")
    end = source.index("\n  },\n", start)
    return KEY_LINE.findall(source[start:end])


class TestStringTables:
    def test_the_file_is_where_the_dashboard_expects_it(self):
        assert DASHBOARD_JS.exists()

    @pytest.mark.parametrize("language", ["hy", "en"])
    def test_a_table_defines_each_key_once(self, language):
        """A repeated key is not a syntax error in JavaScript — the last one silently
        wins, so a stale translation can sit above the real one indefinitely."""
        found = keys_for(language)
        duplicates = sorted({key for key in found if found.count(key) > 1})
        assert not duplicates, f"{language} defines these twice: {duplicates}"

    def test_armenian_covers_every_english_string(self):
        """The failure this file exists for. An untranslated key does not break the
        page; it just serves English to an Armenian reader, and says nothing."""
        missing = sorted(set(keys_for("en")) - set(keys_for("hy")))
        assert not missing, f"no Armenian translation for: {missing}"

    def test_english_covers_every_armenian_string(self):
        """The other direction matters too: English is the *fallback* table, so a key
        only Armenian has degrades to the raw key — `coverageOf` on screen."""
        missing = sorted(set(keys_for("hy")) - set(keys_for("en")))
        assert not missing, f"no English fallback for: {missing}"
