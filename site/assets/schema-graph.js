/* Renders the schema summary graph on the knowledge-graph page.
 * Data comes from data/schema.json, written by 05_export_web.py, rather than
 * being inlined into the page - a <script> tag emitted from a Quarto code
 * chunk is fragile, and a fetch is debuggable when it fails. */
/* Cytoscape loader shared by the explorer and the knowledge-graph page.
 *
 * The <script src=".../cytoscape.min.js"> tag was silently failing, leaving
 * window.cytoscape undefined and both graph panes blank. Loading the ES module
 * directly from inside the script removes the dependency on a global being set
 * by a tag we cannot see the result of, and falls back across CDNs. */
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
      const m = await import(/* webpackIgnore: true */ u);
      const cy = m.default || m.cytoscape || window.cytoscape;
      if (typeof cy === "function") { window.cytoscape = cy; return cy; }
      tried.push(u + " (no callable export)");
    } catch (e) { tried.push(u + " (" + e.message + ")"); }
  }
  throw new Error("cytoscape unavailable — tried:\n" + tried.join("\n"));
}

(function () {
  var EL = "schema-graph";

  function say(msg, isErr) {
    var c = document.getElementById(EL);
    if (!c) return;
    c.innerHTML =
      '<div style="display:flex;height:100%;align-items:center;' +
      'justify-content:center;font-size:13px;color:' +
      (isErr ? "#c5221f" : "#5f6368") + ';padding:20px;text-align:center;">' +
      msg + "</div>";
  }

  function render(data) {
    var c = document.getElementById(EL);
    c.innerHTML = "";
    var maxN = data.maxN || 300;
    cytoscape({
      container: c,
      elements: data.elements,
      style: [
        { selector: "node", style: {
            label: "data(label)", "font-size": 9, color: "#202124",
            "text-valign": "bottom", "text-margin-y": 5,
            "text-background-color": "#fbfbfc",
            "text-background-opacity": 0.9, "text-background-padding": 2,
            width:  "mapData(n, 0, " + maxN + ", 18, 60)",
            height: "mapData(n, 0, " + maxN + ", 18, 60)",
            "background-opacity": 0.9 } },
        { selector: ".network",   style: { "background-color": "#1a73e8" } },
        { selector: ".sensing",   style: { "background-color": "#2a9d8f" } },
        { selector: ".placement", style: { "background-color": "#f4a261" } },
        { selector: "edge", style: {
            label: "data(label)", "font-size": 7.5, color: "#5f6368",
            width: "mapData(n, 1, " + maxN + ", 0.8, 5)",
            "line-color": "#c8ccd0", "target-arrow-color": "#c8ccd0",
            "target-arrow-shape": "triangle", "curve-style": "bezier",
            "text-rotation": "autorotate",
            "text-background-color": "#fbfbfc",
            "text-background-opacity": 0.85,
            "text-background-padding": 2 } }
      ],
      layout: { name: "cose", animate: false, padding: 34,
                nodeRepulsion: 26000, idealEdgeLength: 140,
                nestingFactor: 0.9, randomize: false },
      wheelSensitivity: 0.2
    });
  }

  function boot() {
    if (!document.getElementById(EL)) return;
    say("Loading schema…");
    loadCytoscape()
      .then(function () { return fetch("data/schema.json"); })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(render)
      .catch(function (e) { say(e.message, true); });
  }

  if (document.readyState !== "loading") boot();
  else document.addEventListener("DOMContentLoaded", function () { boot(); });
})();
