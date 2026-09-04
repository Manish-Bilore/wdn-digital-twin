#!/usr/bin/env python3
"""
01_ingest_cad.py
================
Reads the DWG/DXF, selects the pipe and annotation layers, georeferences the
drawing into the project CRS, and writes an intermediate GeoPackage.

    python3 scripts/01_ingest_cad.py -c config.yml

Outputs (in project.output_dir):
    01_cad_lines.gpkg   layer 'centrelines'  - georeferenced pipe geometry
                        layer 'annotations'  - parsed diameter/material/asset text
                        layer 'boundary'     - the service boundary, if supplied
    01_ingest.json      what was chosen and why
"""

import argparse
import json
import os
import sys

import geopandas as gpd
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wdnkit import cadingest
from wdnkit.config import Config
from wdnkit.terrain import load_boundary


def _cp_anchors(lines_gdf):
    """Two well-separated CAD anchors, used to express the solved transform."""
    import numpy as _np
    b = lines_gdf.total_bounds
    return [(round(float(b[0]), 1), round(float(b[3]), 1)),
            (round(float(b[2]), 1), round(float(b[1]), 1))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-c", "--config", required=True)
    args = ap.parse_args()
    cfg = Config.load(args.config)
    out = cfg.outdir

    cad_src = cfg.get("cad.path")
    print(f"[1/4] ingesting {cad_src}")
    cad_path = cadingest.normalise_cad(cad_src, out)

    layers = cadingest.list_layers(cad_path)
    print(f"    layers present: {layers}")

    pipe_layers = cadingest.pick_layers(cad_path, "pipe", cfg.get("cad.pipe_layers"))
    anno_layers = cadingest.pick_layers(cad_path, "anno",
                                        cfg.get("cad.annotation_layers"))
    print(f"    pipe layers      -> {pipe_layers}")
    print(f"    annotation layers-> {anno_layers}")
    if not pipe_layers:
        raise SystemExit("no pipe layer identified; set cad.pipe_layers explicitly")

    lines = cadingest.read_cad_lines(cad_path, pipe_layers,
                                     cfg.get("cad.extent_filter"))
    text = cadingest.read_cad_text(cad_path, anno_layers)
    print(f"    {len(lines)} centreline features, {len(text)} annotation(s)")

    # boundary is needed before georeferencing when fitting to it
    crs = cfg.get("project.crs")
    bgeom, bgdf = (None, None)
    if cfg.get("boundary.path"):
        bgeom, bgdf = load_boundary(cfg.get("boundary.path"),
                                    cfg.get("boundary.layer"),
                                    crs if crs != "auto" else None,
                                    cfg.get("boundary.buffer_m", 0.0))
        if crs == "auto" and bgdf is not None and bgdf.crs is not None:
            crs = str(bgdf.crs)
            print(f"    project CRS taken from the boundary: {crs}")

    if crs == "auto":
        raise SystemExit("set project.crs explicitly, or supply a boundary "
                         "carrying a CRS")

    mode = cfg.get("georeference.mode")
    if mode in ("fit_to_boundary", "auto_register"):
        # the fit depends on which block of linework we keep, so choose first
        if cfg.get("cad.keep") == "largest_cluster":
            lines = cadingest.select_cluster(lines, None)
        lines_g, text_g, tr = cadingest.georeference(lines, text, cfg, bgeom, crs)
    else:
        # georeference first, then let the boundary arbitrate between blocks
        lines_g, text_g, tr = cadingest.georeference(lines, text, cfg, bgeom, crs)
        if cfg.get("cad.keep") == "largest_cluster":
            lines_g = cadingest.select_cluster(lines_g, bgeom)
            if bgeom is not None and len(text_g):
                near = text_g.geometry.representative_point().apply(
                    lambda p: bgeom.buffer(150).contains(p))
                text_g = text_g[near].reset_index(drop=True)

    # parse annotations after georeferencing so x/y are in project coordinates
    ladder = cfg.get("hydraulics.diameter_ladder_mm")
    recs = []
    if len(text_g):
        for _, r in text_g.iterrows():
            p = cadingest.parse_annotation(r["text"], ladder)
            p.update(x=float(r["x"]), y=float(r["y"]), cad_layer=r["cad_layer"])
            recs.append(p)
    ann = pd.DataFrame(recs) if recs else pd.DataFrame(
        columns=["raw", "diameter_mm", "material", "asset", "x", "y", "cad_layer"])

    n_dia = int(ann.diameter_mm.notna().sum()) if len(ann) else 0
    n_asset = int(ann.asset.notna().sum()) if len(ann) else 0
    print(f"    parsed {n_dia} diameter label(s), {n_asset} asset note(s)")
    if n_dia:
        print("    diameters found: "
              f"{sorted(ann.diameter_mm.dropna().unique().tolist())}")

    gpkg = os.path.join(out, "01_cad_lines.gpkg")
    if os.path.exists(gpkg):
        os.remove(gpkg)
    lines_g[["cad_layer", "geometry"]].to_file(gpkg, layer="centrelines",
                                               driver="GPKG")
    if len(ann):
        gann = gpd.GeoDataFrame(
            ann, geometry=gpd.points_from_xy(ann.x, ann.y), crs=crs)
        gann.to_file(gpkg, layer="annotations", driver="GPKG")
    if bgdf is not None:
        gpd.GeoDataFrame(geometry=[bgeom], crs=crs).to_file(
            gpkg, layer="boundary", driver="GPKG")

    meta = dict(cad_path=cad_src, cad_readable=cad_path, crs=crs, pipe_layers=pipe_layers,
                annotation_layers=anno_layers, n_lines=len(lines_g),
                n_annotations=len(ann), n_diameter_labels=n_dia,
                n_asset_notes=n_asset, transform=list(tr),
                georeference_mode=cfg.get("georeference.mode"),
                registration=getattr(cadingest, "_LAST_REGISTRATION", None),
                control_points_equivalent=cadingest.transform_as_control_points(
                    tr, [(lines_g.total_bounds[0], lines_g.total_bounds[1])]
                    if False else _cp_anchors(lines)))
    with open(os.path.join(out, "01_ingest.json"), "w") as f:
        json.dump(meta, f, indent=2)
    print(f"    -> {gpkg}")


if __name__ == "__main__":
    main()
