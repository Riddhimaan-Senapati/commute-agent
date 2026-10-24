"use strict";

/* Pareto Commute Agent — web UI (WS9). Vanilla JS, no build step.
 * One state object; render functions read from it. */

const state = {
  status: null,          // GET /api/status
  trips: [],             // ShowcaseTrip[]
  trip: null,            // {origin, destination, depart_at}
  plan: null,            // PlanResult
  previousSummaries: [], // route summaries from the plan before the last alert change
  highlightedId: null,
  conversationId: null,
  alertHeaders: {},      // alert id -> header, from GET /api/alerts
};

const COLORS = ["#0072B2", "#E69F00", "#009E73", "#D55E00", "#CC79A7", "#56B4E9", "#000000", "#F0E442"];

const LEG_STYLE = {
  walk: { dashArray: "4 6", weight: 4 }, bike: { weight: 4 }, bluebikes: { weight: 4 },
  drive: { dashArray: "1 8", weight: 5 },
  subway: { weight: 7 }, light_rail: { weight: 7 }, bus: { weight: 6 }, commuter_rail: { weight: 7 }, ferry: { weight: 7 },
};

const ATTRIBUTION = "© OpenStreetMap contributors";

// Objective display: key -> {label, unit, decimals}
const OBJECTIVE_META = [
  { key: "time_min", label: "Time", unit: "min", decimals: 0 },
  { key: "co2_g", label: "CO₂", unit: "g CO₂", decimals: 0 },
  { key: "crash_risk", label: "Crash", unit: "risk index", decimals: 1 },
  { key: "disruption_risk", label: "Disruption", unit: "0 to 1", decimals: 2 },
];

// Module-level handles that are not part of the serializable state.
let map = null;
let tileLayer = null;
let basemapLayer = null;
let routeLayers = {};   // routeId -> array of L.Polyline
let chart = null;

/* ---------------- networking ---------------- */

// The server keeps one alert source per session id, so each tab has its own.
const SESSION_ID = (() => {
  try {
    let id = sessionStorage.getItem("commute-session-id");
    if (!id) {
      id = crypto.randomUUID();
      sessionStorage.setItem("commute-session-id", id);
    }
    return id;
  } catch (_) {
    return crypto.randomUUID();
  }
})();

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}), "X-Session-Id": SESSION_ID };
  const res = await fetch(path, { ...options, headers });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (body && body.detail) detail = body.detail;
    } catch (_) { /* non-JSON error body */ }
    throw new Error(`${path}: ${res.status} ${detail}`);
  }
  if (res.status === 204) return null;
  return res.json();
}

function toast(message) {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.hidden = true; }, 5000);
}

/* ---------------- init ---------------- */

async function init() {
  initMap();

  try {
    state.status = await api("/api/status");
  } catch (err) {
    toast(`Status unavailable: ${err.message}`);
  }
  renderStatus();

  try {
    const trips = await api("/api/showcase-trips");
    state.trips = (trips && trips.trips) || [];
  } catch (err) {
    toast(`Showcase trips unavailable: ${err.message}`);
    state.trips = [];
  }
  renderTripSelector();

  await initAlertSources();

  // Load the first showcase trip, if any, and plan it.
  if (state.trips.length > 0) {
    document.getElementById("trip-select").value = "0";
    await loadTrip(tripToQuery(state.trips[0]));
  }

  wireControls();
}

function tripToQuery(trip) {
  return { origin: trip.origin, destination: trip.destination, depart_at: null };
}

function renderStatus() {
  const el = document.getElementById("run-mode");
  if (!state.status) { el.textContent = "unknown"; return; }
  el.textContent = state.status.run_mode || "unknown";
  // The identity notice is already in the header markup; confirm it matches the server.
}

function renderTripSelector() {
  const sel = document.getElementById("trip-select");
  sel.textContent = "";
  if (state.trips.length === 0) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "No showcase trips";
    sel.appendChild(opt);
    return;
  }
  state.trips.forEach((trip, i) => {
    const opt = document.createElement("option");
    opt.value = String(i);
    opt.textContent = trip.name || `${trip.origin_name} → ${trip.destination_name}`;
    sel.appendChild(opt);
  });
}

async function initAlertSources() {
  const sel = document.getElementById("alert-select");
  let sources = null;
  try {
    sources = await api("/api/alert-sources");
  } catch (_) {
    // Before the API exists, leave the default "none" option in place.
    return;
  }
  sel.textContent = "";
  const liveEnabled = !!sources.live_enabled;
  (sources.sources || ["none"]).forEach((name) => {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    if (name === "live" && !liveEnabled) opt.disabled = true;
    sel.appendChild(opt);
  });
  if (sources.active) sel.value = sources.active;
}

function wireControls() {
  document.getElementById("trip-select").addEventListener("change", (e) => {
    const i = parseInt(e.target.value, 10);
    if (Number.isInteger(i) && state.trips[i]) loadTrip(tripToQuery(state.trips[i]));
  });

  document.getElementById("alert-select").addEventListener("change", (e) => {
    onAlertSourceChange(e.target.value);
  });

  document.getElementById("chat-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const input = document.getElementById("chat-input");
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    sendChat(text);
  });
}

/* ---------------- trip + plan ---------------- */

async function loadTrip(trip) {
  state.trip = trip;
  state.previousSummaries = [];
  await requestPlan();
}

async function requestPlan() {
  if (!state.trip) return;
  const body = {
    origin: state.trip.origin,
    destination: state.trip.destination,
    depart_at: state.trip.depart_at || null,
  };
  try {
    state.plan = await api("/api/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (err) {
    toast(`Plan failed: ${err.message}`);
    return;
  }
  // A plan carries only alert ids; the active source's AlertsView maps them to headers.
  try {
    const view = await api("/api/alerts");
    state.alertHeaders = Object.fromEntries((view.alerts || []).map((a) => [a.id, a.header]));
  } catch (err) {
    state.alertHeaders = {};
    toast(`Alert headers unavailable: ${err.message}`);
  }
  renderPlan();
}

function renderPlan() {
  renderMap();
  renderChart();
  renderRouteList();
}

/* ---------------- map ---------------- */

function initMap() {
  map = L.map("map", { zoomControl: true }).setView([42.33, -71.07], 12);
  // Both offline and live modes credit OpenStreetMap.
  map.attributionControl.addAttribution(ATTRIBUTION);
}

function setBasemapMode(runMode) {
  // Live mode adds the OSM tile layer; offline mode draws the local basemap and loads no tiles.
  if (runMode === "live") {
    if (!tileLayer) {
      tileLayer = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: ATTRIBUTION,
      });
    }
    if (!map.hasLayer(tileLayer)) tileLayer.addTo(map);
    if (basemapLayer && map.hasLayer(basemapLayer)) map.removeLayer(basemapLayer);
  } else {
    if (tileLayer && map.hasLayer(tileLayer)) map.removeLayer(tileLayer);
    loadBasemap();
  }
}

async function loadBasemap() {
  if (basemapLayer) { if (!map.hasLayer(basemapLayer)) basemapLayer.addTo(map); return; }
  try {
    const geo = await api("/basemap.geojson");
    basemapLayer = L.geoJSON(geo, {
      style: { color: "#bdbdbd", weight: 1, opacity: 0.8 },
      interactive: false,
    }).addTo(map);
  } catch (_) {
    // Basemap is optional for development; absence should not break the page.
  }
}

function renderMap() {
  const plan = state.plan;
  if (!plan) return;

  setBasemapMode(plan.run_mode);

  // Clear old route layers.
  Object.values(routeLayers).forEach((lines) => lines.forEach((ln) => map.removeLayer(ln)));
  routeLayers = {};

  const bounds = [];

  plan.routes.forEach((route, index) => {
    const color = COLORS[index % COLORS.length];
    const lines = [];
    route.legs.forEach((leg) => {
      const latlngs = (leg.coords || []).map(([lon, lat]) => [lat, lon]);
      if (latlngs.length < 2) return;
      const style = LEG_STYLE[leg.mode] || { weight: 4 };
      const line = L.polyline(latlngs, {
        color,
        weight: style.weight,
        dashArray: style.dashArray || null,
        opacity: 0.9,
      }).addTo(map);
      line.on("mouseover", () => highlight(route.id));
      lines.push(line);
      latlngs.forEach((ll) => bounds.push(ll));
    });
    routeLayers[route.id] = lines;
  });

  if (bounds.length > 0) {
    map.fitBounds(bounds, { padding: [30, 30], maxZoom: 15 });
  }
  applyHighlightToMap();
}

function applyHighlightToMap() {
  Object.entries(routeLayers).forEach(([routeId, lines]) => {
    const on = routeId === state.highlightedId;
    lines.forEach((ln) => ln.setStyle({ opacity: on ? 1 : (state.highlightedId ? 0.3 : 0.9) }));
    if (on) lines.forEach((ln) => ln.bringToFront());
  });
}

/* ---------------- chart ---------------- */

function renderChart() {
  const plan = state.plan;
  if (!plan) return;
  const ctx = document.getElementById("chart").getContext("2d");

  const points = plan.routes.map((route, index) => ({
    x: route.objectives.time_min,
    y: route.objectives.co2_g,
    routeId: route.id,
    index,
  }));
  const colors = plan.routes.map((_, i) => COLORS[i % COLORS.length]);

  const data = {
    datasets: [{
      label: "Routes",
      data: points,
      backgroundColor: colors,
      borderColor: colors,
      pointRadius: 7,
      pointHoverRadius: 10,
    }],
  };

  const options = {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    scales: {
      x: { title: { display: true, text: "Time (min)" }, beginAtZero: true },
      y: { title: { display: true, text: "CO₂ (g)" }, beginAtZero: true },
    },
    plugins: {
      legend: { display: false },
      tooltip: {
        callbacks: {
          label: (item) => {
            const r = plan.routes[item.raw.index];
            return `${r.summary}: ${fmt(r.objectives.time_min, 0)} min, ${fmt(r.objectives.co2_g, 0)} g CO₂`;
          },
        },
      },
    },
    onHover: (event, elements) => {
      if (elements && elements.length > 0) {
        const idx = elements[0].index;
        const r = plan.routes[idx];
        if (r) highlight(r.id);
      }
    },
  };

  if (chart) chart.destroy();
  chart = new Chart(ctx, { type: "scatter", data, options });
}

function applyHighlightToChart() {
  if (!chart || !state.plan) return;
  const radii = state.plan.routes.map((r) => (r.id === state.highlightedId ? 11 : 7));
  chart.data.datasets[0].pointRadius = radii;
  chart.update("none");
}

/* ---------------- route list ---------------- */

function fmt(v, decimals) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return Number(v).toFixed(decimals);
}

function renderRouteList() {
  const container = document.getElementById("route-list");
  container.textContent = "";
  const plan = state.plan;
  if (!plan || plan.routes.length === 0) {
    const p = document.createElement("p");
    p.textContent = "No routes.";
    container.appendChild(p);
    return;
  }

  plan.routes.forEach((route, index) => {
    const color = COLORS[index % COLORS.length];

    // One listitem per route. Inside it the route button, the Sources <details>, the
    // alert headers, and the "Choose this route" button are siblings, so each is its own
    // keyboard stop (a <button> may not contain interactive content).
    const item = document.createElement("div");
    item.className = "route-item";
    item.dataset.routeId = route.id;
    item.style.borderLeftColor = color;
    item.setAttribute("role", "listitem");

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "route-btn";
    btn.dataset.routeId = route.id;
    btn.setAttribute("aria-label", `Route: ${route.summary}`);

    // Title row
    const title = document.createElement("div");
    title.className = "route-title";
    const name = document.createElement("span");
    name.textContent = route.summary;
    const kind = document.createElement("span");
    kind.className = "route-kind";
    kind.textContent = route.kind;
    title.appendChild(name);
    title.appendChild(kind);
    btn.appendChild(title);

    // Labels + unknown-exposure badge
    if ((route.labels && route.labels.length) || route.unknown_exposure_share > 0) {
      const labels = document.createElement("div");
      labels.className = "labels";
      (route.labels || []).forEach((lbl) => {
        const chip = document.createElement("span");
        chip.className = "label-chip";
        chip.textContent = lbl.replace(/_/g, " ");
        labels.appendChild(chip);
      });
      if (route.unknown_exposure_share > 0) {
        const badge = document.createElement("span");
        badge.className = "badge-unknown";
        badge.textContent = "exposure partly unknown";
        badge.title = `Unknown exposure share: ${fmt(route.unknown_exposure_share, 2)}`;
        labels.appendChild(badge);
      }
      btn.appendChild(labels);
    }

    // Four objectives with units
    const objs = document.createElement("div");
    objs.className = "objectives";
    OBJECTIVE_META.forEach((m) => {
      const row = document.createElement("div");
      row.className = "obj-row";
      const n = document.createElement("span");
      n.className = "obj-name";
      n.textContent = m.label;
      const v = document.createElement("span");
      v.className = "obj-val";
      v.textContent = `${fmt(route.objectives[m.key], m.decimals)} ${m.unit}`;
      row.appendChild(n);
      row.appendChild(v);
      objs.appendChild(row);
    });
    btn.appendChild(objs);
    item.appendChild(btn);

    // Source labels (collapsible)
    if (route.sources) {
      const det = document.createElement("details");
      det.className = "sources";
      const sum = document.createElement("summary");
      sum.textContent = "Sources";
      det.appendChild(sum);
      const dl = document.createElement("dl");
      OBJECTIVE_META.forEach((m) => {
        if (!route.sources[m.key]) return;
        const dt = document.createElement("dt");
        dt.textContent = m.label;
        const dd = document.createElement("dd");
        dd.textContent = route.sources[m.key];
        dl.appendChild(dt);
        dl.appendChild(dd);
      });
      det.appendChild(dl);
      item.appendChild(det);
    }

    // Affecting alert headers
    if (route.alert_ids && route.alert_ids.length > 0) {
      const al = document.createElement("ul");
      al.className = "route-alerts";
      al.setAttribute("aria-label", "Alerts on this route");
      route.alert_ids.forEach((id) => {
        const li = document.createElement("li");
        li.textContent = state.alertHeaders[id] || id;
        al.appendChild(li);
      });
      item.appendChild(al);
    }

    // Highlight wiring: focus and mouseenter both call highlight().
    item.addEventListener("mouseenter", () => highlight(route.id));
    btn.addEventListener("focus", () => highlight(route.id));
    btn.addEventListener("click", () => highlight(route.id));

    // "Choose this route" button, keyboard reachable on its own.
    const chooseWrap = document.createElement("div");
    chooseWrap.className = "choose-wrap";
    const choose = document.createElement("button");
    choose.type = "button";
    choose.className = "choose-btn";
    choose.textContent = "Choose this route";
    choose.setAttribute("aria-label", `Choose route ${route.summary}`);
    choose.addEventListener("click", () => chooseRoute(route.id));
    chooseWrap.appendChild(choose);
    item.appendChild(chooseWrap);

    container.appendChild(item);
  });

  applyHighlightToList();
}

function applyHighlightToList() {
  document.querySelectorAll(".route-item").forEach((item) => {
    item.classList.toggle("highlighted", item.dataset.routeId === state.highlightedId);
  });
}

/* ---------------- highlight (single source of truth) ---------------- */

function highlight(routeId) {
  if (state.highlightedId === routeId) return;
  state.highlightedId = routeId;
  applyHighlightToMap();
  applyHighlightToChart();
  applyHighlightToList();
}

/* ---------------- alert source ---------------- */

async function onAlertSourceChange(name) {
  const before = state.plan ? state.plan.routes.map((r) => r.summary) : [];
  let sources;
  try {
    sources = await api("/api/alert-source", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
  } catch (err) {
    toast(`Alert source change failed: ${err.message}`);
    return;
  }
  if (sources && sources.active) {
    document.getElementById("alert-select").value = sources.active;
  }
  // Re-plan the current trip under the new alert source.
  await requestPlan();
  const after = state.plan ? state.plan.routes.map((r) => r.summary) : [];
  renderParetoChange(before, after);
  state.previousSummaries = after;
}

function renderParetoChange(before, after) {
  const el = document.getElementById("pareto-change");
  el.textContent = "";
  const beforeSet = new Set(before);
  const afterSet = new Set(after);
  const entered = after.filter((s) => !beforeSet.has(s));
  const left = before.filter((s) => !afterSet.has(s));

  if (entered.length === 0 && left.length === 0) {
    el.textContent = "Pareto set unchanged.";
    return;
  }
  if (entered.length > 0) {
    const div = document.createElement("div");
    div.className = "entered";
    div.textContent = `Entered: ${entered.join(", ")}`;
    el.appendChild(div);
  }
  if (left.length > 0) {
    const div = document.createElement("div");
    div.className = "left";
    div.textContent = `Left: ${left.join(", ")}`;
    el.appendChild(div);
  }
}

/* ---------------- choose route (preference learning) ---------------- */

async function chooseRoute(routeId) {
  if (!state.plan) return;
  try {
    await api("/api/choice", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query_id: state.plan.query_id, route_id: routeId }),
    });
    toast("Preference recorded.");
  } catch (err) {
    toast(`Choice failed: ${err.message}`);
  }
}

/* ---------------- chat ---------------- */

async function sendChat(text) {
  appendChatMessage("You", text, false);
  let reply;
  try {
    reply = await api("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversation_id: state.conversationId, message: text }),
    });
  } catch (err) {
    appendChatMessage("Agent", `Error: ${err.message}`, false);
    return;
  }
  state.conversationId = reply.conversation_id;
  renderReply(reply);
}

function renderReply(reply) {
  appendChatMessage("Agent", reply.text, true, reply.unsupported_numbers || []);
}

function appendChatMessage(who, text, markUnsupported, unsupported) {
  const log = document.getElementById("chat-log");
  const msg = document.createElement("div");
  msg.className = "chat-msg " + (who === "You" ? "user" : "agent");

  const whoEl = document.createElement("span");
  whoEl.className = "who";
  whoEl.textContent = who;
  msg.appendChild(whoEl);

  const body = document.createElement("div");
  body.className = "body";
  if (markUnsupported) {
    renderGroundedText(body, text, unsupported || []);
  } else {
    body.textContent = text; // user text, never innerHTML
  }
  msg.appendChild(body);

  log.appendChild(msg);
  log.scrollTop = log.scrollHeight;
}

/* Render model text with textContent and wrap each unsupported number in
 * <mark title="Not found in tool outputs"> by splitting text nodes.
 * Never assigns innerHTML from model output. */
function renderGroundedText(container, text, unsupportedNumbers) {
  container.textContent = text;
  if (!unsupportedNumbers || unsupportedNumbers.length === 0) return;

  // De-duplicate and sort longest-first so "10.5" is matched before "10".
  const needles = Array.from(new Set(unsupportedNumbers.map(String)))
    .filter((n) => n.length > 0)
    .sort((a, b) => b.length - a.length);

  needles.forEach((needle) => markOccurrences(container, needle));
}

function markOccurrences(container, needle) {
  // Walk existing text nodes; split each on the needle and wrap matches in <mark>.
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, null);
  const textNodes = [];
  let node;
  while ((node = walker.nextNode())) textNodes.push(node);

  textNodes.forEach((textNode) => {
    const value = textNode.nodeValue;
    let idx = value.indexOf(needle);
    if (idx === -1) return;

    const frag = document.createDocumentFragment();
    let cursor = 0;
    while (idx !== -1) {
      if (idx > cursor) {
        frag.appendChild(document.createTextNode(value.slice(cursor, idx)));
      }
      const mark = document.createElement("mark");
      mark.title = "Not found in tool outputs";
      mark.textContent = value.slice(idx, idx + needle.length);
      frag.appendChild(mark);
      cursor = idx + needle.length;
      idx = value.indexOf(needle, cursor);
    }
    if (cursor < value.length) {
      frag.appendChild(document.createTextNode(value.slice(cursor)));
    }
    textNode.parentNode.replaceChild(frag, textNode);
  });
}

/* ---------------- boot ---------------- */

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}

// Expose selected pure functions for local testing without a build step.
if (typeof window !== "undefined") {
  window.__commute = { state, COLORS, LEG_STYLE, renderGroundedText, renderParetoChange, fmt };
}
