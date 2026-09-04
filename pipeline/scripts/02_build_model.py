#!/usr/bin/env python3
"""
02_build_model.py
=================
Builds the solved EPANET 2.2 model: topology cleanup, DEM elevations, demand
allocation, diameter assignment, source calibration, steady-state solve.

    python3 scripts/02_build_model.py -c config.yml

Outputs (in project.output_dir):
    <name>.inp          the EPANET model
    <name>.gpkg         junctions / pipes / voronoi_demand_polygons / boundary
    nodes.csv pipes.csv full attribute tables including solved state
    02_model.json       parameter and result summary
"""

import argparse
import json
import os
import sys

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString, Point

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wdnkit import demand as dmd
from wdnkit import hydraulics as hyd
from wdnkit import terrain, topology
from wdnkit.config import Config


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-c", "--config", required=True)
    ap.add_argument("--dem", help="override dem.path")
    args = ap.parse_args()
    cfg = Config.load(args.config)
    out = cfg.outdir
    name = cfg.get("project.name", "wdn")

    gpkg_in = os.path.join(out, "01_cad_lines.gpkg")
    if not os.path.exists(gpkg_in):
        raise SystemExit("run 01_ingest_cad.py first")
    meta_in = json.load(open(os.path.join(out, "01_ingest.json")))
    crs = meta_in["crs"]

    lines = gpd.read_file(gpkg_in, layer="centrelines")
    try:
        ann = gpd.read_file(gpkg_in, layer="annotations")
        annotations = ann.drop(columns="geometry").to_dict("records")
    except Exception:
        annotations = []
    boundary = None
    try:
        boundary = gpd.read_file(gpkg_in, layer="boundary").geometry.iloc[0]
    except Exception:
        pass

    # ---- topology --------------------------------------------------------
    print("[2/4] building topology")
    segs = topology.build(list(lines.geometry), cfg)
    nid, node_rows = topology.node_table(segs)
    nodes = pd.DataFrame(node_rows)

    if cfg.get("boundary.clip_network") and boundary is not None:
        keep = nodes.apply(lambda r: boundary.contains(Point(r.x, r.y)), axis=1)
        print(f"    boundary clip would drop {int((~keep).sum())} node(s) "
              "(not applied to preserve connectivity)")

    # ---- elevation -------------------------------------------------------
    print("    sampling elevation")
    dem_path = args.dem or cfg.get("dem.path")
    if dem_path:
        z, n_fill = terrain.sample_dem(
            dem_path, nodes.x.values, nodes.y.values, crs,
            band=int(cfg.get("dem.band", 1)),
            nodata_fill=cfg.get("dem.nodata_fill", "idw"),
            vertical_offset=float(cfg.get("dem.vertical_offset_m", 0.0)))
        z = terrain.smooth_elevations(nodes.x.values, nodes.y.values, z,
                                      float(cfg.get("dem.smooth_window_m", 0.0)))
        elev_src = os.path.basename(dem_path)
    else:
        raise SystemExit("dem.path is required - supply a DEM covering the network")
    nodes["elevation_m"] = np.round(z, 2)

    # ---- demand ----------------------------------------------------------
    print("    allocating demand")
    links_pre = [dict(node1=nid[topology.endpoints(s)[0]],
                      node2=nid[topology.endpoints(s)[1]],
                      length_m=s.length) for s in segs]
    from shapely.ops import unary_union as _uu
    d_lps, area, cells, prov = dmd.allocate(
        cfg, nodes.node_id.tolist(), nodes[["x", "y"]].values, links_pre, boundary,
        network_geom=_uu(segs))
    nodes["demand_lps"] = np.round(d_lps, 5)
    nodes["service_area_m2"] = np.round(area, 1)

    # ---- diameters -------------------------------------------------------
    print("    assigning diameters")
    dia, mat, ldist, from_cad = hyd.assign_diameters(segs, annotations, cfg)
    src_key = hyd.pick_source(cfg, segs, dia, nid, nodes[["x", "y"]].values)
    src_node = nid[src_key]

    flows = hyd.tree_flows(segs, nid, dict(zip(nodes.node_id, nodes.demand_lps)),
                           src_key, cfg.get("demand.peak_factor", 1.0))
    dia_final, n_raised = hyd.raise_undersized(dia, flows, cfg)

    pipes = pd.DataFrame({
        "pipe_id": [f"P{i+1}" for i in range(len(segs))],
        "node1": [nid[topology.endpoints(s)[0]] for s in segs],
        "node2": [nid[topology.endpoints(s)[1]] for s in segs],
        "length_m": [round(s.length, 2) for s in segs],
        "diameter_cad_mm": dia,
        "diameter_mm": dia_final,
        "material": mat,
        "diameter_from_cad": from_cad,
        "label_distance_m": np.round(ldist, 1),
        "peak_tree_flow_lps": np.round(flows, 4),
    })

    # ---- solve -----------------------------------------------------------
    print("    running EPANET")
    import wntr
    wn, res, head = hyd.build_and_run(cfg, nodes, pipes, src_node, crs)

    p = res.node["pressure"].iloc[0]
    nodes["pressure_m"] = nodes.node_id.map(p).round(2)
    nodes["head_m"] = (nodes.pressure_m + nodes.elevation_m).round(2)
    pipes["flow_lps"] = pipes.pipe_id.map(res.link["flowrate"].iloc[0] * 1000).round(4)
    pipes["velocity_ms"] = pipes.pipe_id.map(res.link["velocity"].iloc[0]).round(4)
    pipes["headloss_m_per_km"] = pipes.pipe_id.map(
        res.link["headloss"].iloc[0]).round(4)

    inp = os.path.join(out, f"{name}.inp")
    wntr.network.write_inpfile(wn, inp, units="LPS")
    print(f"    pressure  min {nodes.pressure_m.min():.1f} | "
          f"median {nodes.pressure_m.median():.1f} | "
          f"max {nodes.pressure_m.max():.1f} m")
    print(f"    velocity  median {pipes.velocity_ms.median():.3f} | "
          f"max {pipes.velocity_ms.max():.3f} m/s")

    nodes.to_csv(os.path.join(out, "nodes.csv"), index=False)
    pipes.to_csv(os.path.join(out, "pipes.csv"), index=False)

    # ---- GIS -------------------------------------------------------------
    gpkg = os.path.join(out, f"{name}.gpkg")
    if os.path.exists(gpkg):
        os.remove(gpkg)
    gpd.GeoDataFrame(nodes, geometry=gpd.points_from_xy(nodes.x, nodes.y),
                     crs=crs).to_file(gpkg, layer="junctions", driver="GPKG")
    gpd.GeoDataFrame(pipes, geometry=list(segs), crs=crs).to_file(
        gpkg, layer="pipes", driver="GPKG")
    if any(c is not None for c in cells):
        gv = gpd.GeoDataFrame(
            {"node_id": nodes.node_id, "demand_lps": nodes.demand_lps},
            geometry=[c if c is not None else Point(0, 0) for c in cells], crs=crs)
        gv.to_file(gpkg, layer="voronoi_demand_polygons", driver="GPKG")
    if boundary is not None:
        gpd.GeoDataFrame(geometry=[boundary], crs=crs).to_file(
            gpkg, layer="boundary", driver="GPKG")

    summary = dict(
        name=name, crs=crs, elevation_source=elev_src,
        junctions=len(nodes), pipes=len(pipes),
        total_pipe_length_m=round(float(pipes.length_m.sum()), 1),
        dead_ends=int((nodes.degree == 1).sum()),
        junction_nodes=int((nodes.degree >= 3).sum()),
        demand=prov,
        peak_factor=cfg.get("demand.peak_factor"),
        source_node=src_node, source_head_m=round(head, 2),
        diameters_from_cad=int(from_cad.sum()), diameters_raised=n_raised,
        elevation_m=dict(min=float(nodes.elevation_m.min()),
                         median=float(nodes.elevation_m.median()),
                         max=float(nodes.elevation_m.max())),
        pressure_m=dict(min=float(nodes.pressure_m.min()),
                        p20=float(nodes.pressure_m.quantile(.2)),
                        median=float(nodes.pressure_m.median()),
                        p80=float(nodes.pressure_m.quantile(.8)),
                        max=float(nodes.pressure_m.max())),
        velocity_ms=dict(median=float(pipes.velocity_ms.median()),
                         max=float(pipes.velocity_ms.max())),
    )
    with open(os.path.join(out, "02_model.json"), "w") as f:
        json.dump(summary, f, indent=2)
    print(f"    -> {inp}\n    -> {gpkg}")


if __name__ == "__main__":
    main()
