/* =============================================================================
 * hero.js
 * The landing-page centrepiece: one question, answered live.
 *
 * A visitor should be able to see the point of the project without reading
 * anything — press a question, watch the graph answer it, watch the answer land
 * on the map. The full three-pane editor lives on the Explorer page; this is
 * deliberately a single interaction.
 *
 * Same in-browser stack as the Explorer: MapLibre for geometry, Oxigraph WASM
 * for SPARQL. Nothing is pre-computed.
 * ========================================================================= */

const DATA = "data/";
const NS = "https://w3id.org/iitb/wdn#";

const ESRI_BASE =
  "https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/" +
  "World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}";
const ESRI_REF =
  "https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/" +
  "World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}";
const ESRI_ATTR = "Tiles &copy; Esri and the GIS user community";

const DMA_COLOURS = ["#e63946", "#457b9d", "#f4a261", "#2a9d8f", "#8e44ad"];

/* The questions. The first is the one the whole project exists to answer. */
const QUESTIONS = [
  {
    label: "pH sensors on dead ends, lowest pressure first",
    plain: "Which pH sensors sit on dead ends where modelled pressure falls " +
           "in the bottom fifth of their district?",
    why: "Four layers at once: the sensor inventory, the network topology, " +
         "the EPANET result and the district zoning. In a spreadsheet world " +
         "this is a week of cross-referencing.",
    sparql: `PREFIX wdn: <${NS}>
PREFIX sosa: <http://www.w3.org/ns/sosa/>

SELECT ?node ?pressure ?dma ?elevation WHERE {
  ?station wdn:deployedAt ?node ;
           sosa:hosts     ?sensor .
  ?sensor  a wdn:PHSensor .
  ?node    a wdn:DeadEnd ;
           wdn:modelledPressure ?pressure ;
           wdn:elevation        ?elevation ;
           wdn:inDMA            ?dma .
}
ORDER BY ?pressure`,
  },
  {
    label: "Large mains with no monitoring",
    plain: "Which mains of 200 mm and above have no monitoring station at " +
           "either end?",
    why: "The gap analysis. Asked of the graph, not of a person who happens " +
         "to remember where the sensors went.",
    sparql: `PREFIX wdn: <${NS}>

SELECT ?pipe ?diameter ?node WHERE {
  ?pipe a wdn:Pipe ;
        wdn:diameter  ?diameter ;
        wdn:startNode ?node .
  FILTER (?diameter >= 200)
  FILTER NOT EXISTS { ?s wdn:deployedAt ?node }
}
ORDER BY DESC(?diameter)`,
  },
  {
    label: "Nodes meeting all four placement criteria",
    plain: "Which nodes satisfied every criterion in the placement analysis?",
    why: "The rationale is data, not a comment in a script. Each criterion a " +
         "node met is a triple you can count.",
    sparql: `PREFIX wdn: <${NS}>

SELECT ?node (COUNT(DISTINCT ?c) AS ?criteria) WHERE {
  ?node wdn:satisfiesCriterion ?c .
}
GROUP BY ?node
HAVING (COUNT(DISTINCT ?c) = 4)`,
  },
  {
    label: "Is any station mis-provisioned?",
    plain: "Does any station host a temperature-compensated sensor but no " +
           "temperature probe?",
    why: "Zero rows is the passing result. pH, TDS and dissolved oxygen all " +
         "need temperature to be meaningful, and the graph knows it.",
    sparql: `PREFIX wdn: <${NS}>
PREFIX sosa: <http://www.w3.org/ns/sosa/>

SELECT ?station WHERE {
  ?station sosa:hosts ?dependent .
  ?dependent wdn:compensatedBy ?t .
  FILTER NOT EXISTS {
    ?station sosa:hosts ?temp . ?temp a wdn:TemperatureSensor
  }
}`,
  },
];

const short = (t) => (t || "").replace(NS, "wdn:")
  .replace("http://www.w3.org/ns/sosa/", "sosa:");

const S = { store: null, map: null, active: 0, ready: false };

/* ------------------------------------------------------------------ boot -- */
async function boot() {
  const tabs = document.getElementById("hero-tabs");
  if (!tabs) return;

  QUESTIONS.forEach((q, i) => {
    const b = document.createElement("button");
    b.className = "qtab" + (i === 0 ? " on" : "");
    b.textContent = q.label;
    b.onclick = () => select(i);
    tabs.appendChild(b);
  });
  render(0);

  const [network, junctions, sensors, boundary, manifest] = await Promise.all([
    fetch(DATA + "network.geojson").then((r) => r.json()),
    fetch(DATA + "junctions.geojson").then((r) => r.json()),
    fetch(DATA + "sensors.geojson").then((r) => r.json()),
    fetch(DATA + "boundary.geojson").then((r) => r.json()),
    fetch(DATA + "manifest.json").then((r) => r.json()),
  ]);
  initMap({ network, junctions, sensors, boundary }, manifest);

  try {
    const mod = await import(
      "https://cdn.jsdelivr.net/npm/oxigraph@0.5.11/web.js");
    if (mod.default) await mod.default();
    const ttl = await fetch(DATA + "graph.ttl").then((r) => r.text());
    S.store = new mod.Store();
    S.store.load(ttl, { format: "text/turtle", base_iri: NS });
    S.ready = true;
    setStatus(`${(S.store.size || manifest.counts.triples).toLocaleString()} ` +
              "triples loaded in your browser", "ok");
    document.getElementById("hero-run").disabled = false;
    run();                                   // answer the first question at once
  } catch (e) {
    console.error(e);
    setStatus("The triplestore did not start: " + e.message, "err");
  }
}

function setStatus(msg, kind = "") {
  const el = document.getElementById("hero-status");
  if (el) { el.textContent = msg; el.className = "wdn-status " + kind; }
}

function select(i) {
  S.active = i;
  [...document.querySelectorAll("#hero-tabs .qtab")]
    .forEach((b, j) => b.classList.toggle("on", i === j));
  render(i);
  if (S.ready) run();
}

function render(i) {
  const q = QUESTIONS[i];
  document.getElementById("hero-question").textContent = q.plain;
  document.getElementById("hero-why").textContent = q.why;
  document.getElementById("hero-sparql").textContent = q.sparql;
  document.getElementById("hero-results").innerHTML =
    '<p class="muted">Loading…</p>';
}

/* ------------------------------------------------------------------- map -- */
function initMap(d, manifest) {
  const map = new maplibregl.Map({
    container: "hero-map",
    style: {
      version: 8,
      sources: {
        esri: { type: "raster", tiles: [ESRI_BASE], tileSize: 256,
                attribution: ESRI_ATTR },
        esriRef: { type: "raster", tiles: [ESRI_REF], tileSize: 256 },
      },
      layers: [{ id: "esri", type: "raster", source: "esri" },
               { id: "esriRef", type: "raster", source: "esriRef" }],
    },
    center: manifest.centre,
    zoom: 14.2,
    attributionControl: { compact: true },
  });
  S.map = map;
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  map.addControl(new maplibregl.ScaleControl({ maxWidth: 100, unit: "metric" }), "bottom-left");
  map.scrollZoom.disable();                 // don't hijack the page scroll

  map.on("load", () => {
    map.addSource("boundary", { type: "geojson", data: d.boundary });
    map.addLayer({ id: "boundary", type: "line", source: "boundary",
      paint: { "line-color": "#2f3336", "line-width": 1.3,
               "line-dasharray": [4, 2.5] } });

    map.addSource("network", { type: "geojson", data: d.network });
    map.addLayer({ id: "network", type: "line", source: "network",
      paint: { "line-color": "#b6bcc2",
               "line-width": ["interpolate", ["linear"], ["get", "diameter_mm"],
                              40, 0.6, 300, 3.2] } });

    map.addSource("junctions", { type: "geojson", data: d.junctions });
    map.addSource("sensors", { type: "geojson", data: d.sensors });
    map.addLayer({ id: "sensors", type: "circle", source: "sensors",
      paint: { "circle-radius": 4.5,
               "circle-color": ["match", ["get", "cluster"],
                 1, DMA_COLOURS[0], 2, DMA_COLOURS[1], 3, DMA_COLOURS[2],
                 4, DMA_COLOURS[3], 5, DMA_COLOURS[4], "#666"],
               "circle-opacity": 0.55,
               "circle-stroke-color": "#fff", "circle-stroke-width": 1 } });

    map.addLayer({ id: "hits", type: "circle", source: "junctions",
      filter: ["in", "node_id", ""],
      paint: { "circle-radius": 9, "circle-color": "#1a73e8",
               "circle-opacity": 0.9,
               "circle-stroke-color": "#fff", "circle-stroke-width": 2 } });

    map.addLayer({ id: "hit-pipes", type: "line", source: "network",
      filter: ["in", "pipe_id", ""],
      paint: { "line-color": "#1a73e8", "line-width": 3.4 } });
  });
}

function highlight(nodes, pipes) {
  if (!S.map || !S.map.getLayer("hits")) return;
  S.map.setFilter("hits", ["in", "node_id"].concat(nodes.length ? nodes : [""]));
  S.map.setFilter("hit-pipes", ["in", "pipe_id"].concat(pipes.length ? pipes : [""]));
}

/* ----------------------------------------------------------------- query -- */
function run() {
  const q = QUESTIONS[S.active];
  const out = document.getElementById("hero-results");
  if (!S.ready) { out.innerHTML = '<p class="muted">Store not ready.</p>'; return; }

  const t0 = performance.now();
  let rows;
  try {
    rows = Array.from(S.store.query(q.sparql));
  } catch (e) {
    out.innerHTML = `<pre class="err">${e.message}</pre>`;
    return;
  }
  const dt = (performance.now() - t0).toFixed(1);

  if (!rows.length) {
    out.innerHTML = `<p class="ok"><strong>0 rows</strong> ` +
      `<span class="muted">· ${dt} ms · for this question that is the ` +
      `passing result</span></p>`;
    highlight([], []);
    return;
  }

  const vars = Array.from(rows[0].keys());
  const cell = (r, v) => {
    const t = r.get(v);
    if (t == null) return "";
    const s = short(t.value ?? String(t));
    return s.length > 34 ? s.slice(0, 33) + "…" : s;
  };

  out.innerHTML =
    `<p class="muted"><strong>${rows.length} row${rows.length === 1 ? "" : "s"}</strong>` +
    ` · answered in ${dt} ms</p>` +
    `<div class="tablewrap"><table class="res"><thead><tr>` +
    vars.map((v) => `<th>${v}</th>`).join("") + `</tr></thead><tbody>` +
    rows.slice(0, 40).map((r) => "<tr>" +
      vars.map((v) => `<td>${cell(r, v)}</td>`).join("") + "</tr>").join("") +
    `</tbody></table></div>`;

  const nodes = new Set(), pipes = new Set();
  rows.forEach((r) => vars.forEach((v) => {
    const s = cell(r, v);
    let m = /^wdn:(J\d+)$/.exec(s);
    if (m) nodes.add(m[1]);
    m = /^wdn:(P\d+)$/.exec(s);
    if (m) pipes.add(m[1]);
  }));
  highlight([...nodes], [...pipes]);
}

if (document.readyState !== "loading") boot();
else document.addEventListener("DOMContentLoaded", boot);
document.addEventListener("click", (e) => {
  if (e.target && e.target.id === "hero-run") run();
});
