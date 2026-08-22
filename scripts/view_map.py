#!/usr/bin/env python3
"""Show the defects on a map in your browser — no downloading, no external upload.

    python3 scripts/view_map.py

Reads a RoadEye database, writes a self-contained HTML page with the defects embedded
in it, and serves that page on a local port. In a Codespace the port is forwarded
automatically and a "Open in Browser" prompt appears.

This exists because the alternative — find the exported file in a tree, download it,
upload it to a third-party site — is three chances to get lost, and the last step
sends survey data to someone else's server. For real Armenian surveys that would be
unacceptable (``docs/PRIVACY.md``); building the habit now with synthetic data is
cheaper than retrofitting it later.

This is a **development preview**, not the M6 dashboard. It draws map tiles from
OpenStreetMap's public servers, which are donated capacity explicitly not intended as
a production service — fine for a handful of views while developing, not for anything
shipped. Production needs self-hosted or paid tiles (``docs/LICENSE_AUDIT.md``).
"""

from __future__ import annotations

import argparse
import http.server
import json
import socketserver
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from roadeye.reporting.export import summarize, to_geojson  # noqa: E402
from roadeye.storage.db import Database  # noqa: E402

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RoadEye — defect preview</title>
<link href="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css" rel="stylesheet">
<script src="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js"></script>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { margin: 0; font: 14px/1.5 system-ui, -apple-system, sans-serif; }
  #map { position: absolute; inset: 0; }
  .panel {
    position: absolute; top: 12px; left: 12px; z-index: 2; width: 290px; max-width: calc(100vw - 24px);
    background: rgba(255,255,255,.96); border-radius: 10px; padding: 14px 16px;
    box-shadow: 0 2px 14px rgba(0,0,0,.25); max-height: calc(100vh - 24px); overflow: auto;
  }
  @media (prefers-color-scheme: dark) {
    .panel { background: rgba(28,28,30,.96); color: #eee; }
  }
  .panel h1 { margin: 0 0 2px; font-size: 16px; }
  .sub { color: #888; font-size: 12px; margin-bottom: 10px; }
  .warn {
    background: #fff3cd; color: #6b5200; border-left: 3px solid #e0a800;
    padding: 8px 10px; border-radius: 4px; font-size: 12px; margin-bottom: 12px;
  }
  @media (prefers-color-scheme: dark) { .warn { background: #3a3016; color: #ffd97a; } }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  td { padding: 2px 0; }
  td:last-child { text-align: right; font-variant-numeric: tabular-nums; font-weight: 600; }
  .maplibregl-popup-content { font: 13px system-ui, sans-serif; max-width: 280px; }
  .maplibregl-popup-content b { display: block; margin-bottom: 4px; }
  .maplibregl-popup-content dl { margin: 0; display: grid; grid-template-columns: auto 1fr; gap: 1px 8px; }
  .maplibregl-popup-content dt { color: #777; }
  .maplibregl-popup-content dd { margin: 0; font-variant-numeric: tabular-nums; }
  #err { position:absolute; inset:0; display:none; place-items:center; padding:24px; text-align:center; }
</style>
</head>
<body>
<div id="map"></div>
<div id="err"><div>
  <h2>Map tiles could not load</h2>
  <p>The defect data is fine — only the background map failed (usually no internet).<br>
  The raw data is in <code>demo_output/demo.geojson</code>.</p>
</div></div>

<div class="panel">
  <h1>RoadEye</h1>
  <div class="sub">defect preview</div>
  <div class="warn">
    <b>Synthetic data.</b> These markers came from a <b>fake</b> detector on a
    simulated drive with no camera footage. They say nothing about any real road.
  </div>
  <table id="summary"></table>
  <p class="sub" style="margin-top:10px">Click a marker for its full record.</p>
</div>

<script>
const DATA = __GEOJSON__;
const SUMMARY = __SUMMARY__;

const rows = [
  ["Defects", SUMMARY.total],
  ["Potholes", (SUMMARY.by_class && SUMMARY.by_class.pothole) || 0],
  ["Probable (unverified)", (SUMMARY.by_status && SUMMARY.by_status.probable) || 0],
  ["Human-verified", (SUMMARY.by_status && SUMMARY.by_status.verified) || 0],
  ["Mean position error", SUMMARY.mean_location_uncertainty_m + " m"],
];
document.getElementById("summary").innerHTML =
  rows.map(r => `<tr><td>${r[0]}</td><td>${r[1]}</td></tr>`).join("");

const coords = DATA.features.map(f => f.geometry.coordinates);
const lons = coords.map(c => c[0]), lats = coords.map(c => c[1]);
const center = coords.length
  ? [(Math.min(...lons)+Math.max(...lons))/2, (Math.min(...lats)+Math.max(...lats))/2]
  : [44.5149, 40.1823];

const map = new maplibregl.Map({
  container: "map",
  center, zoom: 15,
  style: {
    version: 8,
    sources: {
      osm: {
        type: "raster",
        tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
        tileSize: 256,
        maxzoom: 19,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
      }
    },
    layers: [{ id: "osm", type: "raster", source: "osm" }]
  }
});
map.addControl(new maplibregl.NavigationControl(), "top-right");
map.on("error", e => { if (e && e.error && /tile/i.test(String(e.error.message||""))) {
  document.getElementById("err").style.display = "grid";
}});

map.on("load", () => {
  map.addSource("defects", { type: "geojson", data: DATA });

  // Radius encodes reported position uncertainty, so the picture cannot imply more
  // precision than the data has.
  map.addLayer({
    id: "uncertainty", type: "circle", source: "defects",
    paint: {
      "circle-radius": {
        stops: [[14, 4], [18, 40]]
      },
      "circle-color": "#e53935", "circle-opacity": 0.12
    }
  });
  map.addLayer({
    id: "defects", type: "circle", source: "defects",
    paint: {
      "circle-radius": 7,
      "circle-color": ["match", ["get", "status"],
        "verified", "#2e7d32", "rejected", "#9e9e9e", "#e53935"],
      "circle-stroke-width": 2, "circle-stroke-color": "#fff"
    }
  });

  if (coords.length) {
    map.fitBounds(
      [[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]],
      { padding: 90, maxZoom: 17 }
    );
  }

  map.on("click", "defects", e => {
    const p = e.features[0].properties;
    const c = e.features[0].geometry.coordinates;
    const row = (k, v) => `<dt>${k}</dt><dd>${v}</dd>`;
    new maplibregl.Popup({ maxWidth: "300px" })
      .setLngLat(c)
      .setHTML(
        `<b>${p.damage_class} &middot; ${p.defect_id}</b><dl>` +
        row("confidence", (p.confidence * 100).toFixed(0) + "%") +
        row("status", p.status) +
        row("severity", p.severity + " (" + p.severity_source + ")") +
        row("position", c[1].toFixed(6) + ", " + c[0].toFixed(6)) +
        row("uncertainty", "&plusmn;" + p.location_uncertainty_m + " m") +
        row("observations", p.observation_count) +
        row("first seen", String(p.first_seen).replace("T", " ").replace("Z", "")) +
        row("model", p.model_id) +
        row("run", p.processing_run_id) +
        `</dl>`
      )
      .addTo(map);
  });
  map.on("mouseenter", "defects", () => map.getCanvas().style.cursor = "pointer");
  map.on("mouseleave", "defects", () => map.getCanvas().style.cursor = "");
});
</script>
</body>
</html>
"""


def build_page(db_path: Path) -> tuple[str, int]:
    with Database(db_path) as db:
        defects = db.list_defects()
    geojson = to_geojson(defects, attribution="© OpenStreetMap contributors, ODbL")
    summary = summarize(defects)
    html = PAGE.replace("__GEOJSON__", json.dumps(geojson)).replace(
        "__SUMMARY__", json.dumps(summary)
    )
    return html, len(defects)


def main() -> int:
    parser = argparse.ArgumentParser(description="Preview RoadEye defects on a map.")
    parser.add_argument("--db", default="demo_output/demo.db", help="RoadEye database")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-serve", action="store_true", help="write the HTML and exit")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"No database at {db_path}", file=sys.stderr)
        print("Run ./scripts/demo.sh first.", file=sys.stderr)
        return 1

    html, count = build_page(db_path)
    out_dir = db_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(html, encoding="utf-8")

    print(f"Built a map of {count} defects -> {out_dir / 'index.html'}")
    if args.no_serve:
        return 0

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(out_dir), **kw)

        def log_message(self, *a):  # keep the terminal readable
            pass

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", args.port), Handler) as httpd:
        print()
        print("=" * 62)
        print(f"  Map is running on port {args.port}")
        print()
        print("  In a Codespace: a popup says 'Open in Browser' — click it.")
        print("  If you missed it, open the PORTS tab next to the terminal")
        print(f"  and click the globe icon on port {args.port}.")
        print()
        print(f"  Locally: http://localhost:{args.port}")
        print()
        print("  Press Ctrl+C to stop.")
        print("=" * 62)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
