/*
  RoadEye dashboard behaviour.

  Four things here are load-bearing rather than cosmetic.

  1. ARMENIAN IS THE DEFAULT. The people who will use this work for Yerevan's
     municipality; English is the toggle, not the other way round. Every user-facing
     string lives in STRINGS below, so a translation error is a one-line fix rather than
     a hunt through markup.

  2. A defect is drawn as a RING SIZED IN METRES, not a fixed pin. The ring is the
     stated uncertainty of the position, so it grows and shrinks as you zoom, exactly as
     a real distance on the ground does. A fixed-size pin is a lie: it looks the same at
     z12 and z19, which invites a reader to take six decimal places for centimetres when
     the underlying fix was 8 m of consumer GPS.

  3. PROBABLE and VERIFIED are never added together. They are different claims — one is
     a machine guess, the other a person's judgement — and the moment they share a
     number the product is overstating itself to a customer.

  4. The page reports its own provenance. If the data came from the fake detector, the
     banner says so, because synthetic markers on a real map of Yerevan look exactly
     like a working product.
*/

"use strict";

/* ---------------------------------------------------------------- language */

const STRINGS = {
  hy: {
    subtitle: "վնասների քարտեզ",
    reviewQueue: "Ստուգման հերթ →",

    filter: "Զտիչ",
    defectType: "Վնասի տեսակ",
    allTypes: "Բոլոր տեսակները",
    reviewState: "Ստուգման վիճակ",
    allStates: "Բոլոր վիճակները",
    probableLong: "Հավանական — դեռ չստուգված",
    verifiedLong: "Հաստատված մարդու կողմից",
    rejectedLong: "Մերժված մարդու կողմից",
    severity: "Ծանրություն",
    anySeverity: "Ցանկացած ծանրություն",
    unassessed: "Չգնահատված",
    low: "Ցածր",
    medium: "Միջին",
    high: "Բարձր",
    survey: "Երթուղի",
    allSurveys: "Բոլոր երթուղիները",
    minConfidence: "Նվազագույն վստահություն",
    clearFilters: "Մաքրել զտիչները",
    legend: "Պայմանանշաններ",

    pothole: "Փոս",
    alligator_crack: "Ցանցաձև ճաք",
    longitudinal_crack: "Երկայնական ճաք",
    transverse_crack: "Լայնական ճաք",

    countVerified: "հաստատված",
    countProbable: "հավանական, չստուգված",
    countRejected: "մերժված",
    countShown: "ցուցադրված այս զտիչով",

    ringNote:
      "Յուրաքանչյուր վնաս գծված է օղակով, որը ցույց է տալիս, թե որքան կարող է շեղվել " +
      "նրա իրական դիրքը։ Միայն կետը կենթադրեր ճշտություն, որը հեռախոսի GPS-ը չունի։",

    mapFailedTitle: "Քարտեզը չհաջողվեց բեռնել",
    mapFailedBody:
      "Քարտեզի գրադարանը բեռնվում է ինտերնետից։ Վնասների տվյալները տեղական են և չեն տուժել։",

    nothingSelected: "Ոչ մի վնաս ընտրված չէ",
    nothingSelectedBody:
      "Սեղմեք քարտեզի նշանի վրա՝ տեսնելու լուսանկարը, ծագումը և հաստատելու կամ մերժելու այն։",

    noPhoto: "Այս վնասի համար լուսանկար չի պահպանվել։",
    confidence: "Վստահություն",
    street: "Փողոց",
    notMatched: "համապատասխանեցված չէ",
    position: "Դիրք",
    couldBeOffBy: "Կարող է շեղվել",
    locatedBy: "Տեղորոշման եղանակ",
    seen: "Դիտվել է",
    times: "անգամ",
    firstSeen: "Առաջին դիտումը",
    model: "Մոդել",
    run: "Մշակում",

    isThisReal: "Սա իրակա՞ն է",
    yesItIs: "Այո, առկա է",
    noFalseAlarm: "Ոչ, կեղծ ազդանշան",
    changeType: "Իրականում այլ տեսակ է…",
    setSeverity: "Նշել ծանրությունը…",
    saving: "Պահպանվում է…",
    saved: "Պահպանվեց։",
    couldNotSave: "Չհաջողվեց պահպանել՝",
    couldNotLoad: "Չհաջողվեց բեռնել վնասները",

    note: "Ուշադրություն՝",
    truncated: "Վնասները չափազանց շատ են՝ բոլորը գծված չեն։",
    warn_synthetic_detector:
      "Տվյալները ստացվել են սինթետիկ հայտնաբերիչից ({models})։ Այս նշանները ոչինչ չեն " +
      "ասում որևէ իրական ճանապարհի մասին։",
    warn_none_verified:
      "Այստեղ ոչ մի վնաս մարդու կողմից հաստատված չէ։ Ամեն ինչ մեքենայի ենթադրություն է։",
    positionsNote: "Դիրքերը գնահատականներ են՝ նշված անորոշությամբ, ոչ գեոդեզիական ճշտության։",
    tilesNote:
      "Քարտեզի սալիկները՝ © OpenStreetMap-ի մասնակիցներ — կամավորական ծառայություն, " +
      "ոչ արտադրական օգտագործման համար։",

    method_phone_gps: "հեռախոսի GPS",
    method_interpolated_phone_gps: "հեռախոսի GPS, ինտերպոլացված",
    method_road_segment_matched: "համապատասխանեցված ճանապարհի առանցքին",
    method_ground_projected: "պրոյեկտված ճանապարհի մակերևույթին",
    method_manual_correction: "տեղադրված մարդու կողմից",

    status_probable: "Հավանական",
    status_verified: "Հաստատված",
    status_rejected: "Մերժված",

    severity_unassessed: "չգնահատված",
    severity_low: "ցածր",
    severity_medium: "միջին",
    severity_high: "բարձր",
    source_human: "մարդ",
    source_model: "մոդել",
    source_rule: "կանոն",
    source_other: "այլ",
  },

  en: {
    subtitle: "defect map",
    reviewQueue: "Review queue →",

    filter: "Filter",
    defectType: "Defect type",
    allTypes: "All types",
    reviewState: "Review state",
    allStates: "All states",
    probableLong: "Probable — not yet checked",
    verifiedLong: "Verified by a person",
    rejectedLong: "Rejected by a person",
    severity: "Severity",
    anySeverity: "Any severity",
    unassessed: "Not assessed",
    low: "Low",
    medium: "Medium",
    high: "High",
    survey: "Survey",
    allSurveys: "All surveys",
    minConfidence: "Minimum confidence",
    clearFilters: "Clear filters",
    legend: "Legend",

    pothole: "Pothole",
    alligator_crack: "Alligator crack",
    longitudinal_crack: "Longitudinal crack",
    transverse_crack: "Transverse crack",

    countVerified: "verified by a person",
    countProbable: "probable, unchecked",
    countRejected: "rejected",
    countShown: "shown by this filter",

    ringNote:
      "Each defect is drawn with a ring showing how far out its true position could be. " +
      "A dot alone would imply a precision the phone's GPS does not have.",

    mapFailedTitle: "The map could not load",
    mapFailedBody:
      "The map library comes from the internet. The defect data is local and unaffected.",

    nothingSelected: "No defect selected",
    nothingSelectedBody:
      "Click a marker on the map to see its photograph, where it came from, and to approve or reject it.",

    noPhoto: "No photograph was saved for this defect.",
    confidence: "Confidence",
    street: "Street",
    notMatched: "not matched",
    position: "Position",
    couldBeOffBy: "Could be off by",
    locatedBy: "Located by",
    seen: "Seen",
    times: "time(s)",
    firstSeen: "First seen",
    model: "Model",
    run: "Run",

    isThisReal: "Is this real?",
    yesItIs: "Yes, it's there",
    noFalseAlarm: "No, false alarm",
    changeType: "It's actually a different type…",
    setSeverity: "Set severity…",
    saving: "Saving…",
    saved: "Saved.",
    couldNotSave: "Could not save:",
    couldNotLoad: "Could not load defects",

    note: "Note:",
    truncated: "Too many defects to draw them all; some are not shown.",
    warn_synthetic_detector:
      "Produced by a synthetic detector ({models}). These markers describe nothing " +
      "about any real road.",
    warn_none_verified:
      "No defect here has been verified by a person. Everything shown is a machine guess.",
    positionsNote: "Positions are estimates with the stated uncertainty; they are not survey-grade.",
    tilesNote:
      "Map tiles © OpenStreetMap contributors — a volunteer service, not for production use.",

    method_phone_gps: "phone GPS",
    method_interpolated_phone_gps: "phone GPS, interpolated between fixes",
    method_road_segment_matched: "snapped to the road centreline",
    method_ground_projected: "projected onto the road surface",
    method_manual_correction: "placed by a person",

    status_probable: "Probable",
    status_verified: "Verified",
    status_rejected: "Rejected",

    severity_unassessed: "not assessed",
    severity_low: "low",
    severity_medium: "medium",
    severity_high: "high",
    source_human: "person",
    source_model: "model",
    source_rule: "rule",
    source_other: "other",
  },
};

/* Armenian by default. The toggle is remembered per browser, and a stored value is
   validated rather than trusted — localStorage survives a rename of these keys. */
function initialLang() {
  try {
    const stored = localStorage.getItem("roadeye.lang");
    if (stored && STRINGS[stored]) return stored;
  } catch {
    /* Private mode and blocked site data both throw. Armenian is the right default. */
  }
  return "hy";
}

const state = {
  features: [],
  selected: null,
  map: null,
  mapReady: false,
  lang: initialLang(),
  roadAttribution: null,
  lastAttribution: null,
};

/** Translate. Falls back to the key itself, which is visible in testing and harmless. */
function t(key) {
  return (STRINGS[state.lang] && STRINGS[state.lang][key]) || STRINGS.en[key] || key;
}

const CLASS_COLOR = {
  pothole: "#d1341f",
  alligator_crack: "#b06f00",
  longitudinal_crack: "#12795a",
  transverse_crack: "#5b4bb5",
};

const el = (id) => document.getElementById(id);

function applyLanguage() {
  document.documentElement.lang = state.lang;
  for (const node of document.querySelectorAll("[data-i18n]")) {
    node.textContent = t(node.dataset.i18n);
  }
  for (const button of document.querySelectorAll(".langswitch button")) {
    button.classList.toggle("active", button.dataset.lang === state.lang);
  }
  // The survey list and the detail panel are built in JS, so they need rebuilding
  // rather than a textContent swap.
  el("f-survey").dataset.filled = "";
  load();
}

/* ------------------------------------------------------------------ fetching */

function filterQuery() {
  const params = new URLSearchParams();
  const add = (key, value) => { if (value) params.set(key, value); };
  add("damage_class", el("f-class").value);
  add("status", el("f-status").value);
  add("severity", el("f-severity").value);
  add("survey_id", el("f-survey").value);
  const confidence = Number(el("f-confidence").value) / 100;
  if (confidence > 0) params.set("min_confidence", String(confidence));
  return params.toString();
}

async function load() {
  let data;
  try {
    const response = await fetch(`/api/map?${filterQuery()}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    data = await response.json();
  } catch (err) {
    el("banners").innerHTML =
      `<div class="banner warn"><strong>${escapeHtml(t("note"))}</strong>` +
      `<span>${escapeHtml(t("couldNotLoad"))} (${escapeHtml(err.message)})</span></div>`;
    return;
  }

  state.features = data.features || [];
  const meta = data.roadeye || {};

  renderCounts(meta.totals || {}, meta.shown || {});
  renderLegend();
  renderSurveys(meta.surveys || []);
  renderBanners(meta);
  renderAttribution(data.attribution);
  drawFeatures();

  if (state.selected) {
    const still = state.features.find((f) => f.properties.defect_id === state.selected);
    if (still) renderDetail(still.properties, still.geometry.coordinates);
  }

  // Fit only on the first load. Refitting on every filter change would yank the map
  // away from wherever the reader had scrolled to, which makes filtering feel hostile.
  if (!state.fitted && state.features.length) {
    fitToFeatures();
    state.fitted = true;
  }
}

/* -------------------------------------------------------------------- header */

function renderCounts(totals, shown) {
  const status = totals.by_status || {};
  const parts = [
    ["verified", status.verified || 0, t("countVerified")],
    ["probable", status.probable || 0, t("countProbable")],
    ["rejected", status.rejected || 0, t("countRejected")],
  ];
  let html = parts
    .map(([cls, n, label]) =>
      `<div class="count ${cls}"><b>${n}</b><span>${escapeHtml(label)}</span></div>`)
    .join("");

  // Shown-vs-total, so nobody reads a filtered screen as the whole picture.
  if ((shown.total || 0) !== (totals.total || 0)) {
    html += `<div class="count"><b>${shown.total || 0}</b><span>${escapeHtml(t("countShown"))}</span></div>`;
  }
  el("counts").innerHTML = html;
}

function renderLegend() {
  const counts = {};
  for (const f of state.features) {
    const c = f.properties.damage_class;
    counts[c] = (counts[c] || 0) + 1;
  }
  el("legend").innerHTML = Object.keys(CLASS_COLOR)
    .map((key) => {
      const n = counts[key] || 0;
      return (
        `<li class="${n ? "" : "empty"}">` +
        `<span class="swatch" style="background:${CLASS_COLOR[key]}"></span>` +
        `${escapeHtml(t(key))}<span class="n">${n}</span></li>`
      );
    })
    .join("");
}

function renderSurveys(surveys) {
  const select = el("f-survey");
  if (select.dataset.filled === String(surveys.length)) return;
  const current = select.value;
  select.innerHTML =
    `<option value="">${escapeHtml(t("allSurveys"))}</option>` +
    surveys.map((s) => `<option value="${escapeAttr(s)}">${escapeHtml(s)}</option>`).join("");
  select.value = current;
  select.dataset.filled = String(surveys.length);
}

function renderBanners(meta) {
  // Codes, not prose: the API cannot know which language this reader has.
  const warnings = ((meta.provenance && meta.provenance.warnings) || []).map((w) =>
    t(`warn_${w.code}`).replace(/\{(\w+)\}/g, (_, key) => w[key] ?? "")
  );
  if (meta.truncated) warnings.push(t("truncated"));
  el("banners").innerHTML = warnings
    .map(
      (w) =>
        `<div class="banner warn"><strong>${escapeHtml(t("note"))}</strong>` +
        `<span>${escapeHtml(w)}</span></div>`
    )
    .join("");
}

function renderAttribution(attribution) {
  state.lastAttribution = attribution || state.lastAttribution;
  const parts = [];
  if (tilesEnabled()) parts.push(t("tilesNote"));
  // ODbL attribution for the road geometry, whether it reached us as street names on
  // defects or as the lines drawn behind them. The obligation is the same either way.
  for (const notice of [state.lastAttribution, state.roadAttribution]) {
    if (notice && !parts.includes(notice)) parts.push(notice);
  }
  parts.push(t("positionsNote"));
  el("attribution").textContent = parts.join("  ");
}

/* ----------------------------------------------------------------------- map */

function tilesEnabled() {
  return new URLSearchParams(location.search).get("tiles") === "1";
}

/*
  The basemap is optional, and off unless asked for (?tiles=1).

  tile.openstreetmap.org is donated capacity under a usage policy that excludes
  production use, so a municipal dashboard must not lean on it. Street context comes
  instead from the road network `roadeye roads` already downloaded — the same ODbL data,
  held locally, drawn as lines. That works on a laptop with no internet at all, which is
  what offline-first is supposed to mean.
*/
function basemapStyle() {
  if (!tilesEnabled()) {
    return {
      version: 8,
      sources: {},
      layers: [{ id: "bg", type: "background", paint: { "background-color": "#eceff3" } }],
    };
  }
  return {
    version: 8,
    sources: {
      osm: {
        type: "raster",
        tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
        tileSize: 256,
        maxzoom: 19,
        attribution:
          '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      },
    },
    layers: [{ id: "osm", type: "raster", source: "osm" }],
  };
}

function initMap() {
  if (typeof maplibregl === "undefined") {
    el("maperror").hidden = false;
    return;
  }
  const map = new maplibregl.Map({
    container: "map",
    style: basemapStyle(),
    center: [44.515, 40.185],
    zoom: 13,
    attributionControl: false,
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");

  map.on("load", () => {
    // Streets first, so defects draw on top of them.
    map.addSource("roads", { type: "geojson", data: emptyCollection() });
    map.addLayer({
      id: "road-casing",
      type: "line",
      source: "roads",
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        "line-color": "#cdd4dd",
        "line-width": ["interpolate", ["linear"], ["zoom"], 12, 4, 18, 16],
      },
    });
    map.addLayer({
      id: "road-line",
      type: "line",
      source: "roads",
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        "line-color": "#ffffff",
        "line-width": ["interpolate", ["linear"], ["zoom"], 12, 2, 18, 12],
      },
    });
    // Deliberately no street-name labels on the map. A MapLibre symbol layer needs a
    // `glyphs` font endpoint, which is another network dependency — and avoiding those
    // is the entire reason streets are drawn from a local file. The street name is on
    // the defect's detail panel instead, where a reader needs it anyway.

    map.addSource("defects", { type: "geojson", data: emptyCollection() });

    // The uncertainty ring, in METRES. MapLibre has no metre unit, so the radius is
    // recomputed from the current zoom on every move — one metre is a known number of
    // pixels at a given latitude and zoom.
    map.addLayer({
      id: "defect-uncertainty",
      type: "circle",
      source: "defects",
      paint: {
        "circle-radius": ["get", "_radius_px"],
        "circle-color": ["get", "_color"],
        "circle-opacity": 0.15,
        "circle-stroke-width": 1.5,
        "circle-stroke-color": ["get", "_color"],
        "circle-stroke-opacity": 0.55,
      },
    });

    map.addLayer({
      id: "defect-point",
      type: "circle",
      source: "defects",
      paint: {
        // Kept deliberately small — and smaller than the ring at every useful zoom. A
        // fat dot would sit on top of the uncertainty it is supposed to be qualified
        // by, which is how the honesty feature becomes invisible.
        "circle-radius": ["case", ["get", "_selected"], 6, 3],
        "circle-color": ["get", "_color"],
        "circle-stroke-width": ["case", ["get", "_selected"], 2.5, 1],
        "circle-stroke-color": "#ffffff",
        // Rejected defects stay on the map but recede. Removing them would hide the
        // reviewer's own work, which is the record of what was checked.
        "circle-opacity": ["case", ["==", ["get", "status"], "rejected"], 0.35, 1],
      },
    });

    state.mapReady = true;
    // The map came up, so any earlier tile grumble is moot. Without this the error
    // panel latches on: one transient failure during startup would hide a working map
    // for the rest of the session.
    el("maperror").hidden = true;
    map.on("click", "defect-point", (e) => select(e.features[0].properties.defect_id));
    map.on("mouseenter", "defect-point", () => (map.getCanvas().style.cursor = "pointer"));
    map.on("mouseleave", "defect-point", () => (map.getCanvas().style.cursor = ""));
    map.on("zoomend", drawFeatures);
    map.on("moveend", drawFeatures);
    drawFeatures();
    loadRoads();
  });

  map.on("error", (e) => {
    // Only before the map has come up. Afterwards a failed tile is a hole in the
    // backdrop, not a reason to replace a working map with an error page.
    if (state.mapReady) return;
    const message = String((e && e.error && e.error.message) || "");
    if (/tile|fetch|network|style/i.test(message)) el("maperror").hidden = false;
  });

  state.map = map;
}

async function loadRoads() {
  try {
    const response = await fetch("/api/roads");
    if (!response.ok) return;
    const data = await response.json();
    if (!data.features.length) return;
    state.map.getSource("roads").setData(data);
    state.roadAttribution = data.attribution || null;
    renderAttribution(null);
  } catch {
    // Streets are decoration. Losing them must not take the defects with them.
  }
}

/** Pixels per metre at a given latitude and zoom, for Web Mercator 256px tiles. */
function pixelsPerMetre(latitude, zoom) {
  const metresPerPixel =
    (156543.03392 * Math.cos((latitude * Math.PI) / 180)) / Math.pow(2, zoom);
  return 1 / metresPerPixel;
}

function drawFeatures() {
  if (!state.mapReady) return;
  const zoom = state.map.getZoom();
  const features = state.features.map((f) => {
    const props = f.properties;
    const latitude = f.geometry.coordinates[1];
    const metres = Number(props.location_uncertainty_m) || 0;
    return {
      ...f,
      properties: {
        ...props,
        _color: CLASS_COLOR[props.damage_class] || "#666",
        _selected: props.defect_id === state.selected,
        // Floor of 4 px so a very confident defect still shows a ring rather than
        // silently rendering as a bare point — the absence of a ring would read as
        // "no uncertainty", which is never true.
        _radius_px: Math.max(4, metres * pixelsPerMetre(latitude, zoom)),
      },
    };
  });
  state.map.getSource("defects").setData({ type: "FeatureCollection", features });
}

function fitToFeatures() {
  if (!state.mapReady || !state.features.length) return;
  const lons = state.features.map((f) => f.geometry.coordinates[0]);
  const lats = state.features.map((f) => f.geometry.coordinates[1]);
  state.map.fitBounds(
    [
      [Math.min(...lons), Math.min(...lats)],
      [Math.max(...lons), Math.max(...lats)],
    ],
    { padding: 70, maxZoom: 17 }
  );
}

/* ------------------------------------------------------------- detail panel */

function select(defectId) {
  state.selected = defectId;
  drawFeatures();
  const feature = state.features.find((f) => f.properties.defect_id === defectId);
  if (feature) renderDetail(feature.properties, feature.geometry.coordinates);
}

function renderDetail(p, coords) {
  el("detail-empty").hidden = true;
  const body = el("detail-body");
  body.hidden = false;

  const colour = CLASS_COLOR[p.damage_class] || "#666";
  const fact = (label, value, em) =>
    `<dt>${escapeHtml(label)}</dt><dd${em ? ' class="em"' : ""}>${value}</dd>`;

  body.innerHTML = `
    <div class="title">
      <span class="swatch" style="background:${colour}"></span>${escapeHtml(t(p.damage_class))}
    </div>
    <div><span class="pill ${escapeAttr(p.status)}">${escapeHtml(t("status_" + p.status))}</span></div>

    ${p.representative_image
      ? `<img src="/api/evidence/${encodeURIComponent(p.representative_image)}" alt="">`
      : `<div class="noimage">${escapeHtml(t("noPhoto"))}</div>`}

    <dl class="facts">
      ${fact(t("confidence"), `${Math.round((p.confidence || 0) * 100)}%`)}
      ${fact(t("severity"), escapeHtml(t("severity_" + p.severity)) +
        (p.severity !== "unassessed"
          ? ` <span class="muted">(${escapeHtml(t("source_" + p.severity_source))})</span>`
          : ""))}
      ${fact(t("street"),
        p.road_name ? escapeHtml(p.road_name) : `<span class="muted">${escapeHtml(t("notMatched"))}</span>`,
        Boolean(p.road_name))}
      ${fact(t("position"), `${coords[1].toFixed(6)}, ${coords[0].toFixed(6)}`)}
      ${fact(t("couldBeOffBy"), `&plusmn;${escapeHtml(String(p.location_uncertainty_m))} m`, true)}
      ${fact(t("locatedBy"), escapeHtml(t("method_" + p.location_method) || p.location_method))}
      ${fact(t("seen"), `${p.observation_count} ${escapeHtml(t("times"))}`)}
      ${fact(t("firstSeen"), escapeHtml(String(p.first_seen).slice(0, 10)))}
      ${fact(t("model"), escapeHtml(p.model_id || "—"))}
      ${fact(t("run"), escapeHtml(p.processing_run_id || "—"))}
    </dl>

    <h2>${escapeHtml(t("isThisReal"))}</h2>
    <div class="actions">
      <button class="approve" data-action="approve">${escapeHtml(t("yesItIs"))}</button>
      <button class="reject" data-action="reject">${escapeHtml(t("noFalseAlarm"))}</button>
      <select data-role="reclass">
        <option value="">${escapeHtml(t("changeType"))}</option>
        ${Object.keys(CLASS_COLOR)
          .map((k) => `<option value="${k}">${escapeHtml(t(k))}</option>`)
          .join("")}
      </select>
      <select data-role="severity">
        <option value="">${escapeHtml(t("setSeverity"))}</option>
        <option value="low">${escapeHtml(t("low"))}</option>
        <option value="medium">${escapeHtml(t("medium"))}</option>
        <option value="high">${escapeHtml(t("high"))}</option>
      </select>
    </div>
    <div class="saved" id="saved"></div>
  `;

  body.querySelectorAll("button[data-action]").forEach((button) => {
    button.onclick = () => review(p.defect_id, { action: button.dataset.action });
  });
  body.querySelector('[data-role="reclass"]').onchange = (e) => {
    if (e.target.value) {
      review(p.defect_id, { action: "change_class", damage_class: e.target.value });
    }
  };
  body.querySelector('[data-role="severity"]').onchange = (e) => {
    if (e.target.value) {
      review(p.defect_id, { action: "change_severity", severity: e.target.value });
    }
  };
}

async function review(defectId, payload) {
  const saved = el("saved");
  saved.className = "saved";
  saved.textContent = t("saving");
  try {
    const response = await fetch(`/api/defects/${encodeURIComponent(defectId)}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reviewer: "dashboard", ...payload }),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    await load();
    el("saved").textContent = t("saved");
  } catch (err) {
    saved.className = "saved error";
    saved.textContent = `${t("couldNotSave")} ${err.message}`;
  }
}

/* ------------------------------------------------------------------- helpers */

function emptyCollection() {
  return { type: "FeatureCollection", features: [] };
}

function escapeHtml(value) {
  return String(value == null ? "" : value).replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
  );
}
const escapeAttr = escapeHtml;

/* --------------------------------------------------------------------- start */

for (const id of ["f-class", "f-status", "f-severity", "f-survey"]) {
  el(id).onchange = load;
}
el("f-confidence").oninput = (e) => {
  el("f-confidence-out").textContent = `${e.target.value}%`;
};
el("f-confidence").onchange = load;
el("f-reset").onclick = () => {
  for (const id of ["f-class", "f-status", "f-severity", "f-survey"]) el(id).value = "";
  el("f-confidence").value = 0;
  el("f-confidence-out").textContent = "0%";
  load();
};

for (const button of document.querySelectorAll(".langswitch button")) {
  button.onclick = () => {
    state.lang = button.dataset.lang;
    try {
      localStorage.setItem("roadeye.lang", state.lang);
    } catch {
      /* Blocked site data. The choice simply does not persist. */
    }
    applyLanguage();
  };
}

initMap();
applyLanguage();
