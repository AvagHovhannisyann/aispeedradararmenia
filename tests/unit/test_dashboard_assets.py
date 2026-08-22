"""The dashboard must load every asset from itself.

RoadEye is offline-first, and until 2026-08-22 its dashboard fetched MapLibre from unpkg
at view time — so the page loaded on a laptop with no internet and the map did not. The
library is vendored now.

This is the test that keeps it vendored. A CDN `<script src>` is a single line, it works
perfectly on the machine of whoever adds it, and nothing else in the suite would notice:
the failure only appears on a municipal network that blocks the CDN, or on a laptop in a
basement, which is to say in front of the customer.

It is also a supply-chain check. Assets loaded from a remote host are third-party code
executing on a page that displays survey imagery which may contain identifiable people
(`docs/PRIVACY.md`).

Anchor `<a href>` links to openstreetmap.org are deliberately *not* flagged: ODbL
attribution requires them, and a link a reader may click is not an asset the page loads.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[2] / "services/api/static"
PAGES = ["dashboard.html", "review.html"]

#: Attributes that make the browser go and fetch something. `<a href>` is excluded by
#: requiring `rel=` or `src=`, which is why link and script are matched separately.
SCRIPT_SRC = re.compile(r"<script\b[^>]*\bsrc=[\"']([^\"']+)[\"']", re.I)
LINK_HREF = re.compile(r"<link\b[^>]*\bhref=[\"']([^\"']+)[\"']", re.I)
IMG_SRC = re.compile(r"<img\b[^>]*\bsrc=[\"']([^\"']+)[\"']", re.I)
CSS_IMPORT = re.compile(r"@import\s+(?:url\()?[\"']([^\"']+)[\"']", re.I)

REMOTE = re.compile(r"^(https?:)?//", re.I)


def assets_in(text: str) -> list[str]:
    found: list[str] = []
    for pattern in (SCRIPT_SRC, LINK_HREF, IMG_SRC, CSS_IMPORT):
        found.extend(pattern.findall(text))
    return found


class TestNoRemoteAssets:
    @pytest.mark.parametrize("page", PAGES)
    def test_a_page_loads_nothing_from_another_host(self, page):
        path = STATIC / page
        if not path.exists():  # pragma: no cover - review.html is optional here
            pytest.skip(f"{page} not present")

        remote = [a for a in assets_in(path.read_text(encoding="utf-8")) if REMOTE.match(a)]

        assert not remote, (
            f"{page} loads {remote} from a remote host. Vendor it under "
            "services/api/static/vendor/ instead — this page has to work on a laptop "
            "with no internet, and it displays survey imagery."
        )

    @pytest.mark.parametrize("stylesheet", ["dashboard.css", "vendor/maplibre-gl.css"])
    def test_a_stylesheet_pulls_in_nothing_remote(self, stylesheet):
        """Webfonts are the usual way this creeps back. `docs/DASHBOARD.md` explains why
        the Armenian font stack leads with faces the system already has."""
        remote = [
            a
            for a in assets_in((STATIC / stylesheet).read_text(encoding="utf-8"))
            if REMOTE.match(a)
        ]
        assert not remote, f"{stylesheet} fetches {remote}"


class TestTheVendoredLibraryIsReal:
    def test_maplibre_is_present_and_carries_its_licence(self):
        """BSD-3-Clause permits redistribution only with the copyright notice, the
        conditions and the disclaimer. Shipping the library without the licence file
        beside it would make the vendoring itself a licence violation."""
        licence = (STATIC / "vendor/maplibre-gl-LICENSE.txt").read_text(encoding="utf-8")

        assert "MapLibre contributors" in licence
        assert "Redistribution and use in source and binary forms" in licence
        assert "THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS" in licence.upper()

    def test_the_javascript_is_maplibre_and_not_an_error_page(self):
        """A failed `curl` writes a 404 body to the destination file just as happily as
        it writes a library, and the page would then fail only in a browser."""
        js = (STATIC / "vendor/maplibre-gl.js").read_text(encoding="utf-8", errors="replace")

        assert "MapLibre GL JS" in js[:500]
        assert "3-Clause BSD" in js[:500]
        assert len(js) > 500_000, "suspiciously small for a map renderer"
