# Vendored third-party assets

Everything in this directory is **somebody else's code**, committed deliberately. Nothing
in here is edited — a patched copy would be indistinguishable from the original at a
glance, and the next person to re-fetch would silently undo the patch.

## MapLibre GL JS 4.7.1

| | |
|---|---|
| Licence | BSD-3-Clause — full text in `maplibre-gl-LICENSE.txt` |
| Copyright | © 2023 MapLibre contributors |
| Source | https://unpkg.com/maplibre-gl@4.7.1/dist/ |
| Vendored | 2026-08-22 |

```
be9633c4d870e26fb37f1cfe5c5a77181667114003ea16207ac7850d8da8add1  maplibre-gl.js
576b085fdd9487a65a19215328c1e086c07ce5bf6da09b666b3806d3d008dae9  maplibre-gl.css
ee5fc05a0677eaf69601d2c7db0d9ecd6cc27c3abc1d0733bc9ed34707cf8ef2  maplibre-gl-LICENSE.txt
```

To re-fetch or upgrade — and then re-record the version, date and checksums above:

```bash
V=4.7.1
for f in maplibre-gl.js maplibre-gl.css; do
  curl -sSfL "https://unpkg.com/maplibre-gl@$V/dist/$f" -o "services/api/static/vendor/$f"
done
curl -sSfL "https://unpkg.com/maplibre-gl@$V/LICENSE.txt" \
  -o services/api/static/vendor/maplibre-gl-LICENSE.txt
sha256sum services/api/static/vendor/*
```

### Why this is committed rather than loaded from a CDN

It was loaded from unpkg until 2026-08-22. Three reasons it is not any more, in
increasing order of how much they matter:

1. **A municipal network will block it.** A dashboard that works on the founder's laptop
   and shows an empty grey rectangle on the customer's desk is not a working dashboard.
2. **RoadEye is offline-first**, and the dashboard needing the internet to draw a map
   contradicted that. `docs/DASHBOARD.md` went as far as claiming the opposite of what
   [ADR-010](../../../../docs/DECISIONS/ADR-010-dashboard-without-a-build-step.md) said,
   which is what a documented-but-unfixed conflict eventually does to a document.
3. **It was third-party code executed at view time from a host we do not control**, on a
   page that displays survey imagery which may contain identifiable people
   (`docs/PRIVACY.md`). Vendoring turns a standing trust relationship into a one-time,
   checksummed, reviewable artefact.

The licence permits it: BSD-3-Clause allows redistribution in source or binary form
provided the copyright notice, conditions and disclaimer travel with it — which is why
`maplibre-gl-LICENSE.txt` is here and must stay here.

### What this does *not* fix

Raster **tiles**. `?tiles=1` still fetches from `tile.openstreetmap.org`, which is donated
capacity under a policy that excludes production use. That is why the basemap is off by
default and street context is drawn from the locally held road network instead. Tiles are
a separate, unsolved problem (`docs/DASHBOARD.md`).
