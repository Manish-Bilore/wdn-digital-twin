#!/usr/bin/env python3
"""
05_export_web.py
================
Packages a completed pipeline run for the static site: GeoJSON in WGS84, the
knowledge graph, a compact search index, and the static analysis figures.

    python3 scripts/05_export_web.py -c config.yml --site site

Everything written here is small enough to load in the browser without tiling
or an API - the whole payload is well under 100 kB gzipped, which is what makes
live client-side SPARQL practical rather than a mock-up.

Outputs (into <site>/data):
    network.geojson        pipes, with diameter / material / flow / velocity
    junctions.geojson      nodes, with elevation / demand / pressure / DMA
    sensors.geojson        the selected monitoring nodes + rationale
    dma.geojson            Thiessen district metered areas
    demand.geojson         Voronoi allocation polygons
    boundary.geojson       campus outline
    graph.ttl              TBox + ABox, for the in-browser triplestore
    manifest.json          counts, extents, provenance, model summary
    figures/*.svg          static analysis charts
"""

import argparse
import json
import os
import shutil
import sys

import geopandas as gpd
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WGS84 = "EPSG:4326"


def _round_geom(gdf, ndp=6):
    """Trim coordinate precision - 6 dp is ~0.1 m, well past what we can claim."""
    import shapely
    gdf = gdf.copy()
    gdf["geometry"] = shapely.set_precision(gdf.geometry.values, 10 ** (-ndp))
    return gdf


def _write(gdf, path, keep=None, ndp=6):
    g = gdf.to_crs(WGS84)
    if keep:
        keep = [c for c in keep if c in g.columns]
        g = g[keep + ["geometry"]]
    for c in g.columns:
        if c == "geometry":
            continue
        if pd.api.types.is_float_dtype(g[c]):
            g[c] = g[c].round(4)
        elif pd.api.types.is_bool_dtype(g[c]):
            g[c] = g[c].astype(int)
    if os.path.exists(path):
        os.remove(path)
    g.to_file(path, driver="GeoJSON", coordinate_precision=ndp)
    return os.path.getsize(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--outdir", default="outputs",
                    help="pipeline output directory")
    ap.add_argument("-s", "--site", default="site", help="Quarto site root")
    ap.add_argument("-n", "--name", default="iitb")
    args = ap.parse_args()

    out, site = args.outdir, args.site
    data = os.path.join(site, "data")
    figs = os.path.join(data, "figures")
    os.makedirs(figs, exist_ok=True)

    gpkg = os.path.join(out, f"{args.name}.gpkg")
    m1 = json.load(open(os.path.join(out, "01_ingest.json")))
    m2 = json.load(open(os.path.join(out, "02_model.json")))
    m3 = json.load(open(os.path.join(out, "03_placement.json")))

    sizes = {}
    print("[5/5] exporting web payload")

    # ---- vector layers ---------------------------------------------------
    pipes = gpd.read_file(gpkg, layer="pipes")
    sizes["network.geojson"] = _write(
        pipes, os.path.join(data, "network.geojson"),
        ["pipe_id", "node1", "node2", "length_m", "diameter_mm",
         "diameter_cad_mm", "material", "diameter_from_cad", "roughness_C",
         "flow_lps", "velocity_ms"])

    nodes = gpd.read_file(gpkg, layer="junctions")
    ncl = pd.read_csv(os.path.join(out, "nodes_clustered.csv"))
    fcols = [c for c in ("cluster", "f1_topology", "f2_elevation",
                         "f3_pressure", "f4_demand", "passed", "selected")
             if c in ncl.columns]
    nodes = nodes.merge(ncl[["node_id"] + fcols], on="node_id", how="left")
    sizes["junctions.geojson"] = _write(
        nodes, os.path.join(data, "junctions.geojson"),
        ["node_id", "elevation_m", "demand_lps", "pressure_m", "degree",
         "node_type", "service_area_m2"] + fcols)

    for layer, fname, keep in [
        ("optimal_sensor_nodes", "sensors.geojson",
         ["node_id", "cluster", "elevation_m", "pressure_m", "demand_lps",
          "degree", "node_type", "risk_flags", "selection_reason"]),
        ("dma_thiessen", "dma.geojson", ["cluster"]),
        ("voronoi_demand_polygons", "demand.geojson", ["node_id", "demand_lps"]),
        ("boundary", "boundary.geojson", None),
    ]:
        try:
            g = gpd.read_file(gpkg, layer=layer)
            sizes[fname] = _write(g, os.path.join(data, fname), keep)
        except Exception as e:
            print(f"    ! layer {layer} skipped: {e}")

    # ---- knowledge graph -------------------------------------------------
    src_ttl = os.path.join(out, f"{args.name}_full.ttl")
    if not os.path.exists(src_ttl):
        src_ttl = os.path.join(out, f"{args.name}_instances.ttl")
    shutil.copy(src_ttl, os.path.join(data, "graph.ttl"))
    sizes["graph.ttl"] = os.path.getsize(os.path.join(data, "graph.ttl"))

    triples = None
    try:
        from rdflib import Graph
        g = Graph()
        g.parse(os.path.join(data, "graph.ttl"), format="turtle")
        triples = len(g)
        # RDF/XML alongside Turtle, so the w3id content-negotiation branch that
        # serves application/rdf+xml has something to redirect to
        owl_path = os.path.join(data, "ontology.owl")
        g.serialize(owl_path, format="xml")
        sizes["ontology.owl"] = os.path.getsize(owl_path)
    except Exception as e:
        print(f"    ! RDF/XML not written: {e}")

    # ---- figures ---------------------------------------------------------
    srcfig = os.path.join(out, "figs")
    if os.path.isdir(srcfig):
        for f in sorted(os.listdir(srcfig)):
            shutil.copy(os.path.join(srcfig, f), os.path.join(figs, f))
        print(f"    copied {len(os.listdir(figs))} map figure(s)")

    make_charts(figs, nodes, pipes, m1, m2, m3)

    # ---- manifest --------------------------------------------------------
    b = nodes.to_crs(WGS84).total_bounds
    manifest = dict(
        name=args.name,
        crs_source=m2.get("crs"),
        bounds=[round(float(v), 6) for v in b],
        centre=[round(float((b[0] + b[2]) / 2), 6),
                round(float((b[1] + b[3]) / 2), 6)],
        counts=dict(pipes=int(m2.get("pipes", 0)),
                    junctions=int(m2.get("junctions", 0)),
                    sensors=int(m3.get("attrition", {}).get("selected", 0)),
                    dmas=int(m3.get("n_clusters", 0)),
                    triples=triples,
                    pipe_length_km=round(m2.get("total_pipe_length_m", 0) / 1000, 2),
                    dead_ends=int(m2.get("dead_ends", 0)),
                    diameters_from_cad=int(m2.get("diameters_from_cad", 0))),
        model=dict(peak_factor=m2.get("peak_factor"),
                   source_node=m2.get("source_node"),
                   source_head_m=m2.get("source_head_m"),
                   elevation_m=m2.get("elevation_m"),
                   pressure_m=m2.get("pressure_m"),
                   velocity_ms=m2.get("velocity_ms"),
                   demand=m2.get("demand")),
        registration=m1.get("registration"),
        georeference_mode=m1.get("georeference_mode"),
        cad=dict(source=os.path.basename(m1.get("cad_path", "")),
                 pipe_layers=m1.get("pipe_layers"),
                 n_lines=m1.get("n_lines"),
                 n_diameter_labels=m1.get("n_diameter_labels"),
                 n_asset_notes=m1.get("n_asset_notes")),
        placement=m3.get("attrition"),
        per_dma=m3.get("per_dma"),
        payload_bytes=sizes,
    )
    with open(os.path.join(data, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)

    total = sum(sizes.values())
    print(f"    payload {total/1024:.0f} kB raw across {len(sizes)} file(s)")
    for k, v in sorted(sizes.items(), key=lambda kv: -kv[1]):
        print(f"      {k:<24} {v/1024:>8.1f} kB")
    print(f"    -> {data}")


# --------------------------------------------------------------- charts -----
def make_charts(figs, nodes, pipes, m1, m2, m3):
    """Static SVGs for the narrative sections. No JS charting library needed."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.size": 9, "axes.edgecolor": "#c8ccd0",
                         "axes.labelcolor": "#3c4043",
                         "xtick.color": "#5f6368", "ytick.color": "#5f6368"})

    # 1. placement attrition funnel
    a = m3.get("attrition", {})
    stages = [("All junctions", a.get("all_nodes", 0)),
              ("F1 topology", a.get("f1_topology", 0)),
              ("F2 elevation", a.get("f1_f2", 0)),
              ("F3 pressure", a.get("f1_f2_f3", 0)),
              ("F4 demand", a.get("f1_f2_f3_f4", 0)),
              ("+ per-DMA floor", a.get("selected", 0))]
    fig, ax = plt.subplots(figsize=(7.2, 3.4))
    labels = [s[0] for s in stages]
    vals = [s[1] for s in stages]
    cols = ["#c8ccd0", "#c8ccd0", "#8ab4f8", "#669df6", "#4285f4", "#1a73e8"]
    bars = ax.barh(range(len(vals))[::-1], vals, color=cols, height=0.62)
    ax.set_yticks(range(len(vals))[::-1])
    ax.set_yticklabels(labels)
    for b, v in zip(bars, vals):
        ax.text(v + max(vals) * 0.012, b.get_y() + b.get_height() / 2, str(v),
                va="center", fontsize=8.5, color="#3c4043")
    ax.set_xlabel("Nodes surviving")
    ax.set_title("Hierarchical overlay filter", loc="left", fontweight="bold")
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    fig.savefig(os.path.join(figs, "chart_attrition.svg"))
    plt.close(fig)

    # 2. diameter provenance
    fig, ax = plt.subplots(figsize=(7.2, 3.2))
    d = pipes.copy()
    d["from_cad"] = d.get("diameter_from_cad", False).astype(bool) \
        if "diameter_from_cad" in d.columns else False
    order = sorted(d.diameter_mm.astype(int).unique())
    cad = [int(((d.diameter_mm.astype(int) == v) & d.from_cad).sum()) for v in order]
    inf = [int(((d.diameter_mm.astype(int) == v) & ~d.from_cad).sum()) for v in order]
    x = np.arange(len(order))
    ax.bar(x, cad, 0.62, label="From CAD annotation", color="#1a73e8")
    ax.bar(x, inf, 0.62, bottom=cad, label="Default / velocity-sized",
           color="#c8ccd0")
    ax.set_xticks(x)
    ax.set_xticklabels([f"{v}" for v in order])
    ax.set_xlabel("Diameter (mm)")
    ax.set_ylabel("Pipes")
    ax.set_title("Where each pipe diameter came from", loc="left",
                 fontweight="bold")
    ax.legend(frameon=False, fontsize=8)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    fig.savefig(os.path.join(figs, "chart_diameters.svg"))
    plt.close(fig)

    # 3. pressure / elevation distribution
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(7.6, 3.0))
    a1.hist(nodes.elevation_m, bins=26, color="#8ab4f8", edgecolor="white",
            linewidth=0.5)
    a1.set_xlabel("Elevation (m)")
    a1.set_ylabel("Nodes")
    a1.set_title("Ground elevation", loc="left", fontweight="bold", fontsize=10)
    a2.hist(nodes.pressure_m, bins=26, color="#f4a261", edgecolor="white",
            linewidth=0.5)
    a2.axvline(15, color="#e63946", linestyle="--", linewidth=1)
    a2.text(15.6, a2.get_ylim()[1] * 0.92, "15 m minimum residual",
            fontsize=7.5, color="#e63946")
    a2.set_xlabel("Pressure head (m)")
    a2.set_title("Modelled pressure", loc="left", fontweight="bold", fontsize=10)
    for ax in (a1, a2):
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
    fig.tight_layout()
    fig.savefig(os.path.join(figs, "chart_distributions.svg"))
    plt.close(fig)

    # 4. georeferencing sharpness
    reg = m1.get("registration")
    if reg and reg.get("sharpness"):
        fig, ax = plt.subplots(figsize=(7.2, 2.9))
        sh = reg["sharpness"]
        xs = [0] + [int(k.split("_")[2].replace("m", "")) for k in sh]
        ys = [reg["iou"]] + list(sh.values())
        ax.plot(xs, ys, "o-", color="#1a73e8", linewidth=1.8, markersize=5)
        ax.axhline(reg.get("centroid_only_iou", 0), color="#9aa0a6",
                   linestyle="--", linewidth=1)
        ax.text(xs[-1], reg.get("centroid_only_iou", 0) + 0.004,
                "centroid-only fit", ha="right", fontsize=7.5, color="#5f6368")
        ax.set_xlabel("Displacement from the solved optimum (m)")
        ax.set_ylabel("Footprint IoU")
        ax.set_title("How sharply the registration is constrained", loc="left",
                     fontweight="bold")
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        fig.tight_layout()
        fig.savefig(os.path.join(figs, "chart_registration.svg"))
        plt.close(fig)

    print("    wrote 4 analysis chart(s)")


if __name__ == "__main__":
    main()
