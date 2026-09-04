/* =============================================================================
 * explorer.js
 * Three coupled panes over one selection state:
 *
 *   map    MapLibre GL, Esri light-grey basemap, the network as GeoJSON
 *   query  a SPARQL editor running against an in-browser Oxigraph store
 *   graph  Cytoscape, the ABox subgraph around whatever is selected
 *
 * Selecting a node on the map rewrites the query and expands the subgraph.
 * Running a query highlights its result set back on the map. Nothing is
 * pre-computed: the triples are parsed in the browser and every query is
 * evaluated live.
 * ========================================================================= */

const DATA = "data/";
const NS = "https://w3id.org/iitb/wdn#";

const ESRI_BASE =
  "https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/" +
  "World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}";
const ESRI_REF =
  "https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/" +
  "World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}";
const ESRI_ATTR =
  "Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ, and the GIS user community";

const DMA_COLOURS = ["#e63946", "#457b9d", "#f4a261", "#2a9d8f", "#8e44ad"];

const PRESETS = {
  "Sensors on dead ends": `PREFIX wdn: <${NS}>
PREFIX sosa: <http://www.w3.org/ns/sosa/>

SELECT ?node ?pressure ?elevation WHERE {
  ?station a wdn:MonitoringStation ;
           wdn:deployedAt ?node .
  ?node a wdn:DeadEnd ;
        wdn:modelledPressure ?pressure ;
        wdn:elevation ?elevation .
}
ORDER BY ?pressure`,

  "All four criteria satisfied": `PREFIX wdn: <${NS}>

SELECT ?node (COUNT(DISTINCT ?c) AS ?criteria) WHERE {
  ?node wdn:satisfiesCriterion ?c .
}
GROUP BY ?node
HAVING (COUNT(DISTINCT ?c) = 4)`,

  "Coverage per DMA": `PREFIX wdn: <${NS}>

SELECT ?dma (COUNT(DISTINCT ?station) AS ?stations)
            (COUNT(DISTINCT ?node) AS ?nodes) WHERE {
  ?node wdn:inDMA ?dma .
  OPTIONAL { ?station wdn:deployedAt ?node }
}
GROUP BY ?dma
ORDER BY ?dma`,

  "Unmonitored large mains": `PREFIX wdn: <${NS}>

SELECT ?pipe ?diameter ?node WHERE {
  ?pipe a wdn:Pipe ;
        wdn:diameter ?diameter ;
        wdn:startNode ?node .
  FILTER (?diameter >= 200)
  FILTER NOT EXISTS { ?s wdn:deployedAt ?node }
}
ORDER BY DESC(?diameter)`,

  "Provisioning check (expect 0 rows)": `PREFIX wdn: <${NS}>
PREFIX sosa: <http://www.w3.org/ns/sosa/>

SELECT ?station WHERE {
  ?station sosa:hosts ?dependent .
  ?dependent wdn:compensatedBy ?t .
  FILTER NOT EXISTS {
    ?station sosa:hosts ?temp . ?temp a wdn:TemperatureSensor
  }
}`,

  "Intrusion-risk stations": `PREFIX wdn: <${NS}>

SELECT ?node ?pressure ?risk WHERE {
  ?station wdn:deployedAt ?node ;
           wdn:addressesRisk ?risk .
  ?node wdn:modelledPressure ?pressure .
  FILTER (?risk = wdn:IntrusionRisk)
}
ORDER BY ?pressure`,
};

const short = (t) => (t || "").replace(NS, "wdn:")
  .replace("http://www.w3.org/ns/sosa/", "sosa:")
  .replace("http://www.w3.org/1999/02/22-rdf-syntax-ns#", "rdf:")
  .replace("http://www.w3.org/2000/01/rdf-schema#", "rdfs:")
  .replace("http://www.w3.org/2001/XMLSchema#", "xsd:");

const state = { store: null, map: null, cy: null, selected: null, layers: {} };

/* ------------------------------------------------------------------ boot -- */
async function boot() {
  status("Loading network geometry…");
  const [network, junctions, sensors, dma, boundary, manifest] =
    await Promise.all([
      fetch(DATA + "network.geojson").then((r) => r.json()),
      fetch(DATA + "junctions.geojson").then((r) => r.json()),
      fetch(DATA + "sensors.geojson").then((r) => r.json()),
      fetch(DATA + "dma.geojson").then((r) => r.json()),
      fetch(DATA + "boundary.geojson").then((r) => r.json()),
      fetch(DATA + "manifest.json").then((r) => r.json()),
    ]);

  initMap({ network, junctions, sensors, dma, boundary }, manifest);
  initQueryUI();

  status("Parsing the knowledge graph…");
  try {
    await initStore();
    const n = state.store.size ?? manifest.counts.triples;
    status(`Ready — ${n.toLocaleString()} triples in memory.`, "ok");
    document.getElementById("run-query").disabled = false;
  } catch (e) {
    console.error(e);
    status("Could not start the triplestore: " + e.message, "err");
  }
}

function status(msg, kind = "") {
  const el = document.getElementById("wdn-status");
  if (!el) return;
  el.textContent = msg;
  el.className = "wdn-status " + kind;
}

/* ----------------------------------------------------------------- store -- */
async function initStore() {
  const mod = await import(
    "https://cdn.jsdelivr.net/npm/oxigraph@0.5.11/web.js"
  );
  if (mod.default) await mod.default();
  const ttl = await fetch(DATA + "graph.ttl").then((r) => r.text());
  state.store = new mod.Store();
  state.store.load(ttl, { format: "text/turtle", base_iri: NS });
}

/* ------------------------------------------------------------------- map -- */
function initMap(d, manifest) {
  const map = new maplibregl.Map({
    container: "wdn-map",
    style: {
      version: 8,
      sources: {
        esri: { type: "raster", tiles: [ESRI_BASE], tileSize: 256,
                attribution: ESRI_ATTR },
        esriRef: { type: "raster", tiles: [ESRI_REF], tileSize: 256 },
      },
      layers: [
        { id: "esri", type: "raster", source: "esri" },
        { id: "esriRef", type: "raster", source: "esriRef" },
      ],
    },
    center: manifest.centre,
    zoom: 14.4,
    attributionControl: { compact: true },
  });
  state.map = map;
  map.addControl(new maplibregl.NavigationControl({ showCompass: true }), "top-right");
  map.addControl(new maplibregl.ScaleControl({ maxWidth: 120, unit: "metric" }), "bottom-left");

  map.on("load", () => {
    map.addSource("boundary", { type: "geojson", data: d.boundary });
    map.addLayer({
      id: "boundary", type: "line", source: "boundary",
      paint: { "line-color": "#2f3336", "line-width": 1.4, "line-dasharray": [4, 2.5] },
    });

    map.addSource("dma", { type: "geojson", data: d.dma });
    map.addLayer({
      id: "dma-fill", type: "fill", source: "dma",
      paint: {
        "fill-color": ["match", ["get", "cluster"],
          1, DMA_COLOURS[0], 2, DMA_COLOURS[1], 3, DMA_COLOURS[2],
          4, DMA_COLOURS[3], 5, DMA_COLOURS[4], "#cccccc"],
        "fill-opacity": 0.1,
      },
      layout: { visibility: "none" },
    });

    map.addSource("network", { type: "geojson", data: d.network });
    map.addLayer({
      id: "network", type: "line", source: "network",
      paint: {
        "line-color": ["interpolate", ["linear"], ["get", "diameter_mm"],
          40, "#b3cde3", 100, "#f4a261", 200, "#e63946", 300, "#5c0002"],
        "line-width": ["interpolate", ["linear"], ["zoom"],
          13, ["/", ["get", "diameter_mm"], 160],
          17, ["/", ["get", "diameter_mm"], 34]],
      },
    });

    map.addSource("junctions", { type: "geojson", data: d.junctions });
    map.addLayer({
      id: "junctions", type: "circle", source: "junctions",
      paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 13, 1.8, 17, 4.5],
        "circle-color": "#5f6368", "circle-opacity": 0.6,
      },
    });

    map.addSource("sensors", { type: "geojson", data: d.sensors });
    map.addLayer({
      id: "sensors", type: "circle", source: "sensors",
      paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 13, 4.5, 17, 9],
        "circle-color": ["match", ["get", "cluster"],
          1, DMA_COLOURS[0], 2, DMA_COLOURS[1], 3, DMA_COLOURS[2],
          4, DMA_COLOURS[3], 5, DMA_COLOURS[4], "#333"],
        "circle-stroke-color": "#ffffff", "circle-stroke-width": 1.6,
      },
    });

    map.addLayer({
      id: "hits", type: "circle", source: "junctions",
      filter: ["in", "node_id", ""],
      paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 13, 7, 17, 14],
        "circle-color": "rgba(0,0,0,0)",
        "circle-stroke-color": "#1a73e8", "circle-stroke-width": 3,
      },
    });

    ["sensors", "junctions"].forEach((lyr) => {
      map.on("click", lyr, (e) => selectNode(e.features[0].properties));
      map.on("mouseenter", lyr, () => (map.getCanvas().style.cursor = "pointer"));
      map.on("mouseleave", lyr, () => (map.getCanvas().style.cursor = ""));
    });

    map.on("click", "network", (e) => {
      if (map.queryRenderedFeatures(e.point, { layers: ["sensors", "junctions"] }).length)
        return;
      const p = e.features[0].properties;
      popup(e.lngLat, `<strong>${p.pipe_id}</strong><br>
        ${p.diameter_mm} mm ${p.material || ""}<br>
        ${Number(p.length_m).toFixed(1)} m ·
        ${Number(p.velocity_ms).toFixed(3)} m/s<br>
        <span class="muted">diameter ${p.diameter_from_cad ? "from CAD" : "inferred"}</span>`);
    });

    document.getElementById("toggle-dma")?.addEventListener("change", (ev) => {
      map.setLayoutProperty("dma-fill", "visibility",
        ev.target.checked ? "visible" : "none");
    });
  });
}

function popup(lngLat, html) {
  new maplibregl.Popup({ closeButton: true, maxWidth: "280px" })
    .setLngLat(lngLat).setHTML(html).addTo(state.map);
}

/* -------------------------------------------------------------- selection -- */
function selectNode(props) {
  state.selected = props.node_id;
  document.getElementById("sel-node").textContent = props.node_id;
  const rows = [
    ["Type", props.node_type],
    ["DMA", props.cluster ?? "—"],
    ["Elevation", `${Number(props.elevation_m).toFixed(2)} m`],
    ["Pressure", `${Number(props.pressure_m).toFixed(2)} m`],
    ["Demand", `${Number(props.demand_lps).toFixed(4)} L/s`],
    ["Degree", props.degree],
  ];
  if (props.risk_flags) rows.push(["Risk", props.risk_flags]);
  document.getElementById("sel-table").innerHTML = rows
    .map(([k, v]) => `<div class="kv"><span>${k}</span><b>${v}</b></div>`).join("");
  if (props.selection_reason) {
    document.getElementById("sel-reason").textContent = props.selection_reason;
  }
  setQuery(`PREFIX wdn: <${NS}>

# everything the graph knows about ${props.node_id}
SELECT ?predicate ?object WHERE {
  wdn:${props.node_id} ?predicate ?object .
}`);
  expandSubgraph(props.node_id);
}

/* ------------------------------------------------------------- query pane -- */
function initQueryUI() {
  const sel = document.getElementById("preset");
  Object.keys(PRESETS).forEach((k) => {
    const o = document.createElement("option");
    o.value = k; o.textContent = k; sel.appendChild(o);
  });
  sel.addEventListener("change", () => setQuery(PRESETS[sel.value]));
  setQuery(PRESETS["Sensors on dead ends"]);
  document.getElementById("run-query").addEventListener("click", runQuery);
  document.getElementById("query").addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") runQuery();
  });
}

function setQuery(q) {
  document.getElementById("query").value = q;
}

function runQuery() {
  const q = document.getElementById("query").value;
  const out = document.getElementById("results");
  if (!state.store) { out.innerHTML = "<p class='err'>Store not ready.</p>"; return; }
  const t0 = performance.now();
  let rows;
  try {
    rows = state.store.query(q);
  } catch (e) {
    out.innerHTML = `<pre class="err">${e.message}</pre>`;
    return;
  }
  const dt = (performance.now() - t0).toFixed(1);

  if (typeof rows === "boolean") {
    out.innerHTML = `<p class="ok">${rows} <span class="muted">(${dt} ms)</span></p>`;
    return;
  }
  const arr = Array.from(rows);
  if (!arr.length) {
    out.innerHTML = `<p class="muted">0 rows — ${dt} ms.
      For the provisioning check, zero rows is the passing result.</p>`;
    highlight([]);
    return;
  }
  const vars = Array.from(arr[0].keys ? arr[0].keys() : Object.keys(arr[0]));
  const cell = (r, v) => {
    const t = r.get ? r.get(v) : r[v];
    return t == null ? "" : short(t.value ?? String(t));
  };
  out.innerHTML =
    `<p class="muted">${arr.length} row${arr.length === 1 ? "" : "s"} · ${dt} ms</p>
     <div class="tablewrap"><table class="res"><thead><tr>` +
    vars.map((v) => `<th>${v}</th>`).join("") +
    `</tr></thead><tbody>` +
    arr.slice(0, 300).map((r) =>
      `<tr>` + vars.map((v) => `<td>${cell(r, v)}</td>`).join("") + `</tr>`
    ).join("") +
    `</tbody></table></div>`;

  // couple results back to the map: any wdn:J* term becomes a highlight
  const ids = new Set();
  arr.forEach((r) => vars.forEach((v) => {
    const s = cell(r, v);
    const m = /^wdn:(J\d+)$/.exec(s);
    if (m) ids.add(m[1]);
  }));
  highlight(Array.from(ids));
}

function highlight(ids) {
  if (!state.map || !state.map.getLayer("hits")) return;
  state.map.setFilter("hits", ["in", "node_id"].concat(ids.length ? ids : [""]));
  document.getElementById("hit-count").textContent =
    ids.length ? `${ids.length} node${ids.length === 1 ? "" : "s"} on the map` : "";
}

/* ------------------------------------------------------------ graph pane -- */
function expandSubgraph(nodeId, hops = 2) {
  if (!state.store) return;
  const q = `PREFIX wdn: <${NS}>
SELECT ?s ?p ?o WHERE {
  { wdn:${nodeId} ?p ?o . BIND(wdn:${nodeId} AS ?s) }
  UNION
  { ?s ?p wdn:${nodeId} . BIND(wdn:${nodeId} AS ?o) }
}`;
  let rows;
  try { rows = Array.from(state.store.query(q)); } catch (e) { return; }

  const els = [];
  const seen = new Set();
  const add = (id, label, cls) => {
    if (seen.has(id)) return;
    seen.add(id);
    els.push({ data: { id, label }, classes: cls });
  };
  add(`wdn:${nodeId}`, nodeId, "focus");

  rows.forEach((r, i) => {
    const s = short((r.get("s") ?? {}).value ?? "");
    const p = short((r.get("p") ?? {}).value ?? "");
    const o = r.get("o");
    const ov = o ? (o.value ?? String(o)) : "";
    const isIri = ov.startsWith("http");
    const oid = isIri ? short(ov) : `lit${i}`;
    add(oid, isIri ? short(ov) : ov, isIri ? "iri" : "literal");
    els.push({ data: { id: `e${i}`, source: s || `wdn:${nodeId}`,
                       target: oid, label: p } });
  });

  const container = document.getElementById("wdn-graph");
  if (!container) return;
  if (state.cy) state.cy.destroy();
  state.cy = cytoscape({
    container,
    elements: els,
    style: [
      { selector: "node", style: {
          label: "data(label)", "font-size": 8, color: "#3c4043",
          "text-valign": "center", "text-halign": "right",
          "text-margin-x": 4, "background-color": "#9aa0a6", width: 10, height: 10 } },
      { selector: ".focus", style: {
          "background-color": "#1a73e8", width: 18, height: 18,
          "font-size": 10, "font-weight": "bold" } },
      { selector: ".iri", style: { "background-color": "#2a9d8f" } },
      { selector: ".literal", style: {
          "background-color": "#f4a261", shape: "round-rectangle" } },
      { selector: "edge", style: {
          label: "data(label)", "font-size": 7, color: "#5f6368",
          width: 1, "line-color": "#dadce0", "target-arrow-color": "#dadce0",
          "target-arrow-shape": "triangle", "curve-style": "bezier",
          "text-rotation": "autorotate" } },
    ],
    layout: { name: "cose", animate: false, padding: 18, nodeRepulsion: 9000 },
  });
}

if (document.readyState !== "loading") boot();
else document.addEventListener("DOMContentLoaded", boot);
