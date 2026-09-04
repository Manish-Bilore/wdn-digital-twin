/* =============================================================================
 * schema-graph.js
 * Draws the schema that was derived from the instance data at build time.
 *
 * The page's Python chunk walks graph.ttl, extracts every distinct
 * (class -> predicate -> class) pattern actually asserted, and writes
 * data/schema.json. This renders it.
 *
 * Layout is fixed rather than force-directed. At 15 nodes a physics simulation
 * produces a different tangle on every load and reads as noise; a hand-placed
 * arrangement puts the layers in reading order (network -> monitoring ->
 * sensing -> placement) and stays the same every time, so the diagram can be
 * referred to in text.
 *
 * Edge labels are hidden until you hover or select, because 33 predicate names
 * drawn at once is what made the first version illegible.
 * ========================================================================= */

async function loadCytoscape() {
  if (window.cytoscape) return window.cytoscape;
  const urls = [
    "https://cdn.jsdelivr.net/npm/cytoscape@3.30.2/dist/cytoscape.esm.min.mjs",
    "https://cdn.jsdelivr.net/npm/cytoscape@3.30.2/dist/cytoscape.esm.mjs",
    "https://unpkg.com/cytoscape@3.30.2/dist/cytoscape.esm.mjs",
    "https://esm.sh/cytoscape@3.30.2",
  ];
  const tried = [];
  for (const u of urls) {
    try {
      const m = await import(u);
      const cy = m.default || m.cytoscape || window.cytoscape;
      if (typeof cy === "function") { window.cytoscape = cy; return cy; }
      tried.push(u + " (no callable export)");
    } catch (e) { tried.push(u + " (" + e.message + ")"); }
  }
  throw new Error("cytoscape unavailable — tried:\n" + tried.join("\n"));
}

(function () {
  const EL = "schema-graph";

  /* Columns run left to right in the order the pipeline builds them.
     Anything not listed falls back to a spare column, so the diagram survives
     the ontology growing. */
  const POS = {
    "wdn:Pipe":                     { x:  70, y: 150 },
    "wdn:Junction":                 { x:  70, y: 330 },
    "wdn:DeadEnd":                  { x:  70, y: 500 },

    "wdn:PipeMaterial":             { x: 330, y:  70 },
    "wdn:WaterDistributionNetwork": { x: 330, y: 240 },
    "wdn:DistrictMeteredArea":      { x: 330, y: 410 },
    "wdn:TopologyCriterion":        { x: 330, y: 570 },

    "wdn:MonitoringStation":        { x: 610, y: 330 },
    "wdn:PlacementRationale":       { x: 610, y: 570 },

    "wdn:PHSensor":                 { x: 880, y:  90 },
    "wdn:TDSSensor":                { x: 880, y: 200 },
    "wdn:ORPSensor":                { x: 880, y: 310 },
    "wdn:DOSensor":                 { x: 880, y: 420 },
    "wdn:TemperatureSensor":        { x: 880, y: 530 },

    "sosa:ObservableProperty":      { x: 1140, y: 310 },
  };
  const SPARE = { x: 1140, y: 560, dy: 90 };

  const COLOUR = { network: "#1a73e8", sensing: "#2a9d8f", placement: "#e8710a" };

  function say(msg, isErr) {
    const c = document.getElementById(EL);
    if (!c) return;
    c.innerHTML =
      '<div style="display:flex;height:100%;align-items:center;' +
      'justify-content:center;font-size:13px;color:' +
      (isErr ? "#c5221f" : "#5f6368") +
      ';padding:24px;text-align:center;white-space:pre-wrap">' + msg + "</div>";
  }

  function detail(html) {
    const d = document.getElementById("schema-detail");
    if (d) d.innerHTML = html;
  }

  function render(cytoscape, data) {
    const c = document.getElementById(EL);
    c.innerHTML = "";
    const maxN = Math.max(data.maxN || 1, 1);

    let spare = 0;
    const els = data.elements.map((e) => {
      if (e.data.source) return e;
      const p = POS[e.data.id] ||
        { x: SPARE.x, y: SPARE.y + SPARE.dy * spare++ };
      return Object.assign({}, e, { position: { x: p.x, y: p.y } });
    });

    const cy = cytoscape({
      container: c,
      elements: els,
      minZoom: 0.35,
      maxZoom: 2.5,
      wheelSensitivity: 0.2,
      style: [
        { selector: "node", style: {
            label: "data(label)",
            "font-size": 11, "font-weight": 600, color: "#202124",
            "font-family": "system-ui, -apple-system, sans-serif",
            "text-valign": "bottom", "text-margin-y": 6,
            "text-background-color": "#fbfbfc",
            "text-background-opacity": 0.94,
            "text-background-padding": 3,
            "text-background-shape": "roundrectangle",
            width:  "mapData(n, 0, " + maxN + ", 22, 66)",
            height: "mapData(n, 0, " + maxN + ", 22, 66)",
            "border-width": 2, "border-color": "#ffffff",
            "background-opacity": 0.92,
            "transition-property": "opacity, border-color, border-width",
            "transition-duration": "120ms" } },
        { selector: ".network",   style: { "background-color": COLOUR.network } },
        { selector: ".sensing",   style: { "background-color": COLOUR.sensing } },
        { selector: ".placement", style: { "background-color": COLOUR.placement } },
        { selector: "edge", style: {
            width: "mapData(n, 1, " + maxN + ", 1, 6)",
            "line-color": "#cdd2d7",
            "target-arrow-color": "#cdd2d7",
            "target-arrow-shape": "triangle",
            "arrow-scale": 0.85,
            "curve-style": "bezier",
            "control-point-step-size": 46,
            opacity: 0.75,
            label: "",                       // hidden until hover or selection
            "font-size": 9.5, color: "#3c4043",
            "font-family": "ui-monospace, Menlo, monospace",
            "text-rotation": "autorotate",
            "text-background-color": "#ffffff",
            "text-background-opacity": 0.95,
            "text-background-padding": 3,
            "text-background-shape": "roundrectangle",
            "transition-property": "opacity, line-color, width",
            "transition-duration": "120ms" } },
        { selector: "edge.show", style: {
            label: "data(label)", "line-color": "#1a73e8",
            "target-arrow-color": "#1a73e8", opacity: 1, "z-index": 20 } },
        { selector: ".dim",  style: { opacity: 0.12 } },
        { selector: ".focus", style: {
            "border-color": "#202124", "border-width": 3 } },
      ],
      layout: { name: "preset", fit: true, padding: 60 },
    });

    const clear = () => {
      cy.elements().removeClass("dim focus");
      cy.edges().removeClass("show");
    };

    cy.on("mouseover", "node", (e) => {
      const n = e.target;
      const nb = n.closedNeighborhood();
      cy.elements().addClass("dim");
      nb.removeClass("dim");
      n.addClass("focus");
      n.connectedEdges().addClass("show").removeClass("dim");
      const d = n.data();
      detail(
        '<strong style="color:' + (COLOUR[n.classes()[0]] || "#202124") + '">' +
        d.label + "</strong> — " + d.n.toLocaleString() + " instance" +
        (d.n === 1 ? "" : "s") + " · " + n.connectedEdges().length +
        " relationship type" + (n.connectedEdges().length === 1 ? "" : "s"));
    });
    cy.on("mouseout", "node", () => { clear(); detail(HINT); });

    cy.on("mouseover", "edge", (e) => {
      const d = e.target.data();
      e.target.addClass("show");
      detail("<code>" + d.source + "</code> &nbsp;<strong>" + d.label +
             "</strong>&nbsp; <code>" + d.target + "</code> — " +
             d.n.toLocaleString() + " assertion" + (d.n === 1 ? "" : "s"));
    });
    cy.on("mouseout", "edge", () => { clear(); detail(HINT); });

    cy.on("tap", (e) => { if (e.target === cy) { clear(); detail(HINT); } });

    const fit = document.getElementById("schema-fit");
    if (fit) fit.onclick = () => { clear(); cy.fit(undefined, 60); detail(HINT); };

    detail(HINT);
  }

  const HINT = '<span style="color:#5f6368">Hover a class to isolate what it ' +
               'connects to; hover an edge to see how many times that pattern ' +
               'is asserted.</span>';

  function boot() {
    if (!document.getElementById(EL)) return;
    say("Loading schema…");
    let cyto;
    loadCytoscape()
      .then((c) => { cyto = c; return fetch("data/schema.json"); })
      .then((r) => {
        if (!r.ok) throw new Error("data/schema.json returned HTTP " + r.status);
        return r.json();
      })
      .then((d) => {
        if (!d.elements || !d.elements.length)
          throw new Error("no schema edges were derived from the graph");
        render(cyto, d);
      })
      .catch((e) => say(e.message, true));
  }

  if (document.readyState !== "loading") boot();
  else document.addEventListener("DOMContentLoaded", boot);
})();
