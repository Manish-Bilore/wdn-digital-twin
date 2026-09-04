"""
wdnkit.cadingest
================
Reads a DWG or DXF, pulls out the pipe centrelines and the diameter/material
annotations, and places the drawing in a projected CRS.

DWG is read through GDAL/OGR's `CAD` driver (libopencad), which handles
AC1015 (AutoCAD 2000) and later without an ODA converter. DXF is read through
the OGR `DXF` driver. Neither carries a CRS, so georeferencing is explicit -
see `georeference()`.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
import warnings
from collections import Counter

import geopandas as gpd
import numpy as np
from scipy.spatial import cKDTree
from shapely.affinity import affine_transform
from shapely.geometry import LineString, Point

warnings.filterwarnings("ignore")

PIPE_LAYER_HINTS = ["water", "supply", "pipe", "main", "wdn", "distribution", "line"]
# names that look like pipes but are not centrelines
PIPE_LAYER_VETO = ["text", "label", "annot", "dia text", "meter", "hydrant",
                   "fh", "valve", "symbol", "block", "legend", "note"]
ANNO_LAYER_HINTS = ["text", "dia", "label", "annot", "size"]
EXCLUDE_LAYER_HINTS = ["building", "road", "contour", "tree", "boundary", "grid",
                       "hatch", "dim", "titleblock", "border"]

# inch -> mm, the commercial ladder found in Indian campus drawings
INCH_TO_MM = {1.0: 25, 1.25: 32, 1.5: 40, 2.0: 50, 2.5: 65, 3.0: 75, 4.0: 100,
              5.0: 125, 6.0: 150, 8.0: 200, 10.0: 250, 12.0: 300, 14.0: 350,
              16.0: 400}

MATERIAL_PATTERNS = [
    (r"\bC\.?\s*I\.?\b", "CI"), (r"CAST\s*IRON", "CI"),
    (r"\bG\.?\s*I\.?\b", "GI"), (r"GALV", "GI"),
    (r"\bD\.?\s*I\.?\b", "DI"), (r"DUCTILE", "DI"),
    (r"\bPVC\b|\bUPVC\b|\bCPVC\b", "PVC"),
    (r"\bHDPE\b|\bMDPE\b|\bPE\b", "HDPE"),
    (r"\bM\.?\s*S\.?\b|\bMILD\s*STEEL\b", "MS"),
]

# annotations that are asset notes rather than diameters
ASSET_PATTERNS = [
    (r"\bA\.?\s*V\.?\b|AIR\s*VALVE", "AirValve"),
    (r"NON\s*RETURN|\bNRV\b", "NonReturnValve"),
    (r"\bW\.?\s*O\.?\b|WASH\s*OUT", "WashOut"),
    (r"\bF\.?\s*H\.?\b|FIRE\s*HYDRANT|HYDRANT", "FireHydrant"),
    (r"\bP\s*/?\s*H\b|PUMP\s*HOUSE|PUMPHOUSE", "PumpHouse"),
    (r"DEAD\s*END", "DeadEnd"),
    (r"\bSLUICE\b|\bS\.?\s*V\.?\b", "SluiceValve"),
    (r"\bTANK\b|\bESR\b|\bGSR\b|\bSUMP\b", "Tank"),
]


# --------------------------------------------------------------- reading ----
def normalise_cad(path: str, workdir: str = None, force: bool = False) -> str:
    """
    geopandas' default pyogrio engine has no CAD driver, so a DWG/DXF is first
    converted to a GeoPackage with `ogr2ogr` (GDAL's CAD driver reads AC1015+
    via libopencad; DXF via the DXF driver). The converted file is cached next
    to the source so repeat runs are cheap.

    Any other vector format is passed straight through.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".dwg", ".dxf"):
        return path

    workdir = workdir or os.path.dirname(os.path.abspath(path))
    os.makedirs(workdir, exist_ok=True)
    gpkg = os.path.join(workdir,
                        os.path.splitext(os.path.basename(path))[0] + "_cad.gpkg")

    if os.path.exists(gpkg) and not force:
        if os.path.getmtime(gpkg) >= os.path.getmtime(path):
            return gpkg
        os.remove(gpkg)

    if shutil.which("ogr2ogr") is None:
        raise RuntimeError(
            "ogr2ogr not found. Install GDAL (apt install gdal-bin, or "
            "conda install -c conda-forge gdal) to read DWG/DXF.")

    print(f"    converting {os.path.basename(path)} -> {os.path.basename(gpkg)}")
    r = subprocess.run(
        ["ogr2ogr", "-f", "GPKG", gpkg, path, "-skipfailures", "-nlt", "GEOMETRY"],
        capture_output=True, text=True)
    if not os.path.exists(gpkg):
        raise RuntimeError(f"ogr2ogr could not read {path}:\n{r.stderr[:800]}")
    return gpkg


def list_layers(path: str) -> list[str]:
    import pyogrio
    try:
        return [l[0] if isinstance(l, (list, tuple, np.ndarray)) else l
                for l in pyogrio.list_layers(path)]
    except Exception:
        from osgeo import ogr
        ds = ogr.Open(path)
        return [ds.GetLayer(i).GetName() for i in range(ds.GetLayerCount())]


def _score_layer(name: str, hints, exclude) -> int:
    n = name.lower()
    if any(h in n for h in exclude):
        return -1
    return sum(1 for h in hints if h in n)


def pick_layers(path: str, want: str, configured) -> list[str]:
    """Choose CAD layers by name, or honour an explicit list from the config."""
    if configured and configured != "auto":
        return list(configured) if isinstance(configured, (list, tuple)) else [configured]
    hints = PIPE_LAYER_HINTS if want == "pipe" else ANNO_LAYER_HINTS
    veto = EXCLUDE_LAYER_HINTS + (PIPE_LAYER_VETO if want == "pipe" else [])
    layers = list_layers(path)
    scored = [(l, _score_layer(l, hints, veto)) for l in layers]
    picked = [l for l, s in scored if s > 0]
    if not picked and want == "pipe":
        # fall back to whichever layer holds the most linear geometry
        best, bestn = None, -1
        for l in layers:
            try:
                g = gpd.read_file(path, layer=l)
                n = int((g.geometry.geom_type.isin(["LineString", "MultiLineString"])).sum())
            except Exception:
                n = 0
            if n > bestn:
                best, bestn = l, n
        picked = [best] if best else []
    return picked


def read_cad_lines(path: str, layers, extent_filter=None) -> gpd.GeoDataFrame:
    """All linear geometry from the chosen layers, exploded to LineStrings."""
    frames = []
    for l in layers:
        try:
            g = gpd.read_file(path, layer=l)
        except Exception as e:
            print(f"    ! could not read layer {l}: {e}")
            continue
        g = g[~g.geometry.is_empty & g.geometry.notna()]
        if len(g) == 0:
            continue
        g = g.explode(index_parts=False)
        g = g[g.geometry.geom_type == "LineString"].copy()
        g["cad_layer"] = l
        frames.append(g)
    if not frames:
        raise RuntimeError(f"no linear geometry found in layers {layers}")
    out = gpd.pd.concat(frames, ignore_index=True)
    out = gpd.GeoDataFrame(out, geometry="geometry").set_crs(None, allow_override=True)

    if extent_filter:
        minx, miny, maxx, maxy = extent_filter
        b = out.geometry.bounds
        out = out[(b.minx >= minx) & (b.miny >= miny)
                  & (b.maxx <= maxx) & (b.maxy <= maxy)]
    return out.reset_index(drop=True)


def read_cad_text(path: str, layers) -> gpd.GeoDataFrame:
    """Annotation entities with their text and insertion point."""
    frames = []
    for l in layers:
        try:
            g = gpd.read_file(path, layer=l)
        except Exception:
            continue
        g = g[~g.geometry.is_empty & g.geometry.notna()]
        tcol = None
        for c in ("text", "Text", "TEXT", "label", "Contents"):
            if c in g.columns:
                tcol = c
                break
        if tcol is None:
            continue
        g = g[g[tcol].notna()].copy()
        if len(g) == 0:
            continue
        g["text"] = g[tcol].astype(str)
        g["cad_layer"] = l
        frames.append(g[["text", "cad_layer", "geometry"]])
    if not frames:
        return gpd.GeoDataFrame({"text": [], "cad_layer": [], "geometry": []},
                                geometry="geometry")
    out = gpd.pd.concat(frames, ignore_index=True)
    out = gpd.GeoDataFrame(out, geometry="geometry").set_crs(None, allow_override=True)
    c = out.geometry.centroid
    out["x"], out["y"] = c.x, c.y
    return out.reset_index(drop=True)


# ------------------------------------------------------------- annotation ---
def parse_annotation(txt: str, ladder=None) -> dict:
    """
    '3"G.I.' -> {'diameter_mm': 75, 'material': 'GI'}
    'NEW 250MM' -> {'diameter_mm': 250}
    'A.V.'  -> {'asset': 'AirValve'}
    """
    t = str(txt).upper().strip()
    out: dict = {"raw": txt, "diameter_mm": None, "material": None, "asset": None}

    for pat, mat in MATERIAL_PATTERNS:
        if re.search(pat, t):
            out["material"] = mat
            break
    for pat, asset in ASSET_PATTERNS:
        if re.search(pat, t):
            out["asset"] = asset
            break

    # explicit millimetres
    m = re.search(r"(\d{2,4})\s*(?:MM|M\.M\.)", t)
    if m:
        out["diameter_mm"] = float(m.group(1))
        return out

    # fractional inches: 1 1/2", 11/2", 3/4"
    m = re.search(r"(\d+)?\s*(\d)\s*/\s*(\d)\s*[\"”]", t)
    if m:
        whole = float(m.group(1)) if m.group(1) else 0.0
        frac = float(m.group(2)) / float(m.group(3))
        inch = whole + frac if whole else (1.0 + frac if frac < 1 and not m.group(1) else frac)
        out["diameter_mm"] = _inch_to_mm(inch, ladder)
        return out

    # decimal inches
    m = re.search(r"(\d+(?:\.\d+)?)\s*[\"”]", t)
    if m:
        out["diameter_mm"] = _inch_to_mm(float(m.group(1)), ladder)
        return out

    # bare number that looks like a nominal bore
    m = re.fullmatch(r"(\d{2,4})", t)
    if m:
        v = float(m.group(1))
        if 15 <= v <= 2000:
            out["diameter_mm"] = v
    return out


def _inch_to_mm(inch: float, ladder=None) -> float:
    if inch in INCH_TO_MM:
        mm = INCH_TO_MM[inch]
    else:
        k = min(INCH_TO_MM, key=lambda v: abs(v - inch))
        mm = INCH_TO_MM[k]
    if ladder:
        mm = min(ladder, key=lambda v: abs(v - mm))
    return float(mm)


# ---------------------------------------------------------- georeferencing --
def transform_as_control_points(tr, cad_points):
    """Freeze a solved transform as explicit control points for config.yml."""
    a, b, d, e, dx, dy = tr
    return [dict(cad=[round(float(x), 1), round(float(y), 1)],
                 world=[round(float(a * x + b * y + dx), 2),
                        round(float(d * x + e * y + dy), 2)])
            for (x, y) in cad_points]


def similarity_from_points(cad_pts, world_pts, allow_rotation=True, allow_scale=True):
    """
    Least-squares similarity (Helmert) transform CAD -> world.
    Returns the 6-tuple shapely's affine_transform wants: (a, b, d, e, xoff, yoff).
    """
    P = np.asarray(cad_pts, dtype=float)
    Q = np.asarray(world_pts, dtype=float)
    if len(P) < 2:
        raise ValueError("need at least two control points")

    cp, cq = P.mean(axis=0), Q.mean(axis=0)
    P0, Q0 = P - cp, Q - cq

    s = 1.0
    if allow_scale:
        denom = (P0 ** 2).sum()
        s = math.sqrt((Q0 ** 2).sum() / denom) if denom > 0 else 1.0

    theta = 0.0
    if allow_rotation:
        num = (P0[:, 0] * Q0[:, 1] - P0[:, 1] * Q0[:, 0]).sum()
        den = (P0[:, 0] * Q0[:, 0] + P0[:, 1] * Q0[:, 1]).sum()
        theta = math.atan2(num, den)

    ct, st = math.cos(theta), math.sin(theta)
    a, b = s * ct, -s * st
    d, e = s * st, s * ct
    xoff = cq[0] - (a * cp[0] + b * cp[1])
    yoff = cq[1] - (d * cp[0] + e * cp[1])
    return (a, b, d, e, xoff, yoff)


def fit_to_boundary(lines_gdf, boundary_geom, allow_rotation=False, allow_scale=True):
    """
    Convenience georeferencing when no control points exist: match the CAD
    network's centroid and extent to the supplied boundary polygon.

    This is a coarse fit. It gets the network onto the right patch of ground so
    a DEM can be sampled, but it will not survive survey-grade scrutiny. Use
    control points whenever you have them.
    """
    cad_pts = np.array([[p.x, p.y] for p in lines_gdf.geometry.representative_point()])
    cad_c = cad_pts.mean(axis=0)
    bx, by = boundary_geom.centroid.x, boundary_geom.centroid.y

    cminx, cminy, cmaxx, cmaxy = lines_gdf.total_bounds
    bminx, bminy, bmaxx, bmaxy = boundary_geom.bounds
    cad_span = max(cmaxx - cminx, cmaxy - cminy)
    b_span = max(bmaxx - bminx, bmaxy - bminy)
    s = (b_span / cad_span) if (allow_scale and cad_span > 0) else 1.0

    a, b, d, e = s, 0.0, 0.0, s
    xoff = bx - a * cad_c[0]
    yoff = by - e * cad_c[1]
    return (a, b, d, e, xoff, yoff)


def georeference(lines_gdf, text_gdf, cfg, boundary_geom=None, crs=None):
    """Apply the configured transform and stamp the project CRS on both frames."""
    mode = cfg.get("georeference.mode")
    if mode == "identity":
        tr = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    elif mode == "control_points":
        cps = cfg.get("georeference.control_points")
        tr = similarity_from_points(
            [c["cad"] for c in cps], [c["world"] for c in cps],
            cfg.get("georeference.allow_rotation", True),
            cfg.get("georeference.allow_scale", True))
    elif mode == "auto_register":
        if boundary_geom is None:
            raise ValueError("auto_register needs a boundary geometry")
        from . import georef_auto
        from .topology import degree_map, node_and_merge, quantise, weld_endpoints
        segs = [q for q in (quantise(l) for l in lines_gdf.geometry) if q is not None]
        segs = node_and_merge(weld_endpoints(segs, 0.5))
        pts = np.array(list(degree_map(segs).keys()))
        tr, diag = georef_auto.register(
            pts, boundary_geom,
            allow_rotation=cfg.get("georeference.allow_rotation", False),
            allow_scale=cfg.get("georeference.allow_scale", False),
            buffer_m=cfg.get("georeference.footprint_buffer_m", 35.0),
            search_radius_m=cfg.get("georeference.search_radius_m", 600.0),
            coarse_step_m=cfg.get("georeference.coarse_step_m", 40.0),
            rotation_range_deg=cfg.get("georeference.rotation_range_deg", 8.0))
        globals()["_LAST_REGISTRATION"] = diag
        if diag["iou"] < float(cfg.get("georeference.min_iou", 0.55)):
            print("    ! registration quality is below georeference.min_iou - "
                  "verify before trusting the model")
    elif mode == "fit_to_boundary":
        if boundary_geom is None:
            raise ValueError("fit_to_boundary needs a boundary geometry")
        tr = fit_to_boundary(lines_gdf, boundary_geom,
                             cfg.get("georeference.allow_rotation", False),
                             cfg.get("georeference.allow_scale", True))
        print("    ! coarse centroid/extent fit - supply control points for a "
              "survey-grade transform")
    else:
        raise ValueError(f"unknown georeference.mode {mode}")

    scale = math.hypot(tr[0], tr[2])
    print(f"    transform: scale={scale:.5f} "
          f"offset=({tr[4]:,.1f}, {tr[5]:,.1f})")

    lines = lines_gdf.copy()
    lines["geometry"] = [affine_transform(g, tr) for g in lines.geometry]
    lines = lines.set_crs(crs, allow_override=True)

    text = text_gdf.copy()
    if len(text):
        text["geometry"] = [affine_transform(g, tr) for g in text.geometry]
        text = text.set_crs(crs, allow_override=True)
        c = text.geometry.centroid
        text["x"], text["y"] = c.x, c.y
    return lines, text, tr


# ------------------------------------------------------------- clustering ---
def spatial_clusters(lines_gdf, eps_m=250.0):
    """Label each feature with a spatial cluster id (single-link, radius eps_m)."""
    reps = np.array([[p.x, p.y] for p in lines_gdf.geometry.representative_point()])
    if len(reps) < 3:
        return np.zeros(len(lines_gdf), dtype=int)
    tree = cKDTree(reps)
    parent = list(range(len(reps)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for a, b in tree.query_pairs(eps_m):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    return np.array([find(i) for i in range(len(reps))])


def select_cluster(lines_gdf, boundary_geom=None, eps_m=250.0, verbose=True):
    """
    CAD sheets often carry the same network twice - once draped over the campus
    base map, once as a clean standalone copy - and the base-map version usually
    wins on total length because the building and road linework sits on the same
    layer. When a service boundary is available, pick the cluster that actually
    falls inside it; only fall back to "longest" when there is nothing to test
    against.
    """
    labels = spatial_clusters(lines_gdf, eps_m)
    uniq = list(dict.fromkeys(labels.tolist()))
    if len(uniq) <= 1:
        return lines_gdf.reset_index(drop=True)

    stats = []
    for lab in uniq:
        m = labels == lab
        sub = lines_gdf[m]
        L = float(sub.geometry.length.sum())
        frac = 0.0
        if boundary_geom is not None:
            reps = sub.geometry.representative_point()
            frac = float(np.mean([boundary_geom.contains(p) for p in reps]))
        stats.append((lab, int(m.sum()), L, frac))

    if boundary_geom is not None and max(s[3] for s in stats) > 0.05:
        best = max(stats, key=lambda s: (s[3], s[2]))
        why = f"{best[3]*100:.0f}% inside the boundary"
    else:
        best = max(stats, key=lambda s: s[2])
        why = "longest cluster (no boundary test available)"
        if len(stats) > 1 and verbose:
            print("    ! several disjoint blocks of linework and no boundary "
                  "overlap to arbitrate - set cad.extent_filter if the wrong "
                  "one is chosen")

    keep = labels == best[0]
    if verbose:
        print(f"    kept 1 of {len(uniq)} block(s): {int(keep.sum())} features, "
              f"{best[2]:,.0f} m - {why}")
    return lines_gdf[keep].reset_index(drop=True)


def largest_spatial_cluster(lines_gdf, eps_m=250.0):
    """Backward-compatible wrapper: longest cluster, no boundary test."""
    reps = np.array([[p.x, p.y] for p in lines_gdf.geometry.representative_point()])
    if len(reps) < 3:
        return lines_gdf
    tree = cKDTree(reps)
    pairs = tree.query_pairs(eps_m)
    parent = list(range(len(reps)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for a, b in pairs:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    labels = np.array([find(i) for i in range(len(reps))])
    counts = Counter(labels)

    # rank by total pipe length, not feature count
    best, best_len = None, -1.0
    for lab in counts:
        m = labels == lab
        L = float(lines_gdf.geometry[m].length.sum())
        if L > best_len:
            best, best_len = lab, L
    keep = labels == best
    if keep.sum() < len(lines_gdf):
        print(f"    kept densest cluster: {int(keep.sum())}/{len(lines_gdf)} features, "
              f"{best_len:,.0f} m")
    return lines_gdf[keep].reset_index(drop=True)
