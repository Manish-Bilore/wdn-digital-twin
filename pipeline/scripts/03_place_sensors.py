#!/usr/bin/env python3
"""
03_place_sensors.py
===================
K-means DMA zoning, Thiessen tessellation, hierarchical overlay filter, figures.

    python3 scripts/03_place_sensors.py -c config.yml

Outputs:
    optimal_sensor_nodes.csv   selected nodes + why each survived + risk flags
    nodes_clustered.csv        every node with DMA and per-filter pass/fail
    <name>.gpkg                + layers dma_thiessen, optimal_sensor_nodes
    figs/*.png                 network, elevation, pressure, demand, placement
    03_placement.json
"""
import argparse, json, os, sys
import geopandas as gpd
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wdnkit import placement, viz
from wdnkit.config import Config


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-c", "--config", required=True)
    args = ap.parse_args()
    cfg = Config.load(args.config)
    out = cfg.outdir
    name = cfg.get("project.name", "wdn")
    figs = os.path.join(out, "figs"); os.makedirs(figs, exist_ok=True)

    nodes = pd.read_csv(os.path.join(out, "nodes.csv"))
    pipes = pd.read_csv(os.path.join(out, "pipes.csv"))
    gpkg = os.path.join(out, f"{name}.gpkg")
    gp = gpd.read_file(gpkg, layer="pipes")
    crs = gp.crs
    boundary = None
    try:
        boundary = gpd.read_file(gpkg, layer="boundary").geometry.iloc[0]
    except Exception:
        pass

    print("[3/4] sensor placement")
    nodes, sel, cents, cells, attrition = placement.run(nodes, cfg, boundary)

    nodes.to_csv(os.path.join(out, "nodes_clustered.csv"), index=False)
    cols = ["node_id", "cluster", "x", "y", "elevation_m", "pressure_m",
            "demand_lps", "degree", "node_type", "risk_flags", "selection_reason"]
    sel[cols].to_csv(os.path.join(out, "optimal_sensor_nodes.csv"), index=False)

    gpd.GeoDataFrame(sel[cols], geometry=gpd.points_from_xy(sel.x, sel.y),
                     crs=crs).to_file(gpkg, layer="optimal_sensor_nodes", driver="GPKG")
    gdma = gpd.GeoDataFrame({"cluster": range(1, len(cells) + 1)},
                            geometry=cells, crs=crs)
    gdma.to_file(gpkg, layer="dma_thiessen", driver="GPKG")

    print("    figures")
    m2 = json.load(open(os.path.join(out, "02_model.json")))
    m1 = json.load(open(os.path.join(out, "01_ingest.json")))
    meta = dict(crs=m2.get("crs"), pipes=m2.get("pipes"),
                junctions=m2.get("junctions"),
                total_pipe_length_m=m2.get("total_pipe_length_m"),
                peak_factor=m2.get("peak_factor"))
    dem_name = m2.get("elevation_source", "DEM")
    dm = m2.get("demand", {})
    sources = [
        "Network digitised from "
        f"{os.path.basename(m1.get('cad_path', 'CAD drawing'))}"
        f" ({m1.get('n_diameter_labels', 0)} diameter annotations)",
        f"Elevation sampled from {dem_name}",
        f"Demand {dm.get('total_lpd', 0):,.0f} L/day "
        f"({dm.get('population', 'n/a')} persons x {dm.get('lpcd', 'n/a')} lpcd), "
        f"allocated by {dm.get('allocation', 'n/a')} over the "
        f"{dm.get('service_area_source', 'service area')}",
        f"Georeferenced by {m1.get('georeference_mode', 'n/a')}"
        + (f" (footprint IoU {m1['registration']['iou']:.3f})"
           if m1.get("registration") else ""),
    ]

    viz.network_map(
        gp, nodes, os.path.join(figs, "01_network_diameters.png"),
        title=f"{name.upper()} water distribution network",
        subtitle="Pipe diameters from CAD annotation, raised where peak "
                 "velocity would exceed the design limit",
        boundary=boundary, meta=meta, sources=sources)

    viz.scalar_map(
        gp, nodes, "elevation_m", os.path.join(figs, "02_elevation.png"),
        title="Nodal ground elevation",
        subtitle=f"Sampled from {dem_name} at each network node",
        label="Elevation (m)", cmap="terrain",
        boundary=boundary, meta=meta, sources=sources)

    viz.scalar_map(
        gp, nodes, "pressure_m", os.path.join(figs, "03_pressure.png"),
        title="Nodal pressure head at peak demand",
        subtitle="EPANET 2.2 steady-state, demand-driven analysis",
        label="Pressure (m)", cmap="RdYlBu",
        boundary=boundary, meta=meta, sources=sources)

    try:
        gv = gpd.read_file(gpkg, layer="voronoi_demand_polygons")
        viz.demand_map(
            gv, gp, os.path.join(figs, "04_demand.png"),
            title="Area-based demand allocation",
            subtitle="Thiessen polygons clipped to the served area",
            boundary=boundary, meta=meta, sources=sources)
    except Exception:
        pass

    viz.placement_map(
        gp, nodes, sel, gdma,
        os.path.join(figs, "05_optimal_sensor_locations.png"),
        title=f"Optimal monitoring locations - {len(sel)} nodes",
        subtitle="K-means district metered areas with Thiessen boundaries; "
                 "overlay filter on elevation, pressure and demand",
        boundary=boundary, meta=meta, sources=sources)

    with open(os.path.join(out, "03_placement.json"), "w") as f:
        json.dump(dict(n_clusters=int(cfg.get("placement.n_clusters")),
                       attrition=attrition,
                       per_dma={int(k): int(v) for k, v in
                                sel.groupby("cluster").size().to_dict().items()},
                       sensor_nodes=sel.node_id.tolist()), f, indent=2)
    print(f"    -> {os.path.join(out, 'optimal_sensor_nodes.csv')}")


if __name__ == "__main__":
    main()
