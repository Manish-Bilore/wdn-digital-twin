"""
wdnkit.terrain
==============
Samples nodal elevation from a user-supplied DEM, and loads the campus/service
boundary used to clip demand allocation.

The DEM may be in any CRS; it is reprojected on the fly to the project CRS.
Nodes that fall on nodata (common at raster edges or over water bodies) are
filled by inverse-distance weighting from valid neighbours rather than silently
inheriting a nodata sentinel, which would wreck the hydraulics.
"""

from __future__ import annotations

import warnings

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.warp import transform as warp_transform
from scipy.spatial import cKDTree
from shapely.geometry import shape

warnings.filterwarnings("ignore")


def load_boundary(path, layer=None, crs=None, buffer_m=0.0):
    """Read the boundary vector, reproject, dissolve to a single geometry."""
    if not path:
        return None, None
    g = gpd.read_file(path, layer=layer) if layer else gpd.read_file(path)
    g = g[~g.geometry.is_empty & g.geometry.notna()]
    if g.crs is None:
        if crs is None:
            raise ValueError("boundary has no CRS and project.crs is unset")
        g = g.set_crs(crs)
    if crs is not None:
        g = g.to_crs(crs)
    geom = g.geometry.union_all() if hasattr(g.geometry, "union_all") \
        else g.geometry.unary_union
    if buffer_m:
        geom = geom.buffer(buffer_m)
    return geom, g


def sample_dem(dem_path, xs, ys, crs, band=1, nodata_fill="idw",
               vertical_offset=0.0, verbose=True):
    """
    Sample a raster at projected coordinates. Handles CRS mismatch and nodata.
    Returns (elevations, n_filled).
    """
    xs = np.asarray(xs, dtype=float)
    ys = np.asarray(ys, dtype=float)

    with rasterio.open(dem_path) as ds:
        dem_crs = ds.crs
        if dem_crs is not None and crs is not None and str(dem_crs) != str(crs):
            if verbose:
                print(f"    reprojecting sample points {crs} -> {dem_crs}")
            sx, sy = warp_transform(crs, dem_crs, xs.tolist(), ys.tolist())
            sx, sy = np.asarray(sx), np.asarray(sy)
        else:
            sx, sy = xs, ys

        left, bottom, right, top = ds.bounds
        inside = (sx >= left) & (sx <= right) & (sy >= bottom) & (sy <= top)
        if inside.sum() == 0:
            raise ValueError(
                "no network node falls inside the DEM extent - check the "
                "georeferencing transform and the DEM coverage")
        if verbose and inside.sum() < len(sx):
            print(f"    ! {int((~inside).sum())} node(s) outside the DEM extent")

        vals = np.full(len(sx), np.nan)
        pts = [(float(a), float(b)) for a, b in zip(sx[inside], sy[inside])]
        got = np.array([v[band - 1] for v in ds.sample(pts, indexes=[band])],
                       dtype=float)
        nod = ds.nodatavals[band - 1] if ds.nodatavals else None
        if nod is not None:
            got[np.isclose(got, nod)] = np.nan
        vals[inside] = got

    bad = ~np.isfinite(vals)
    n_bad = int(bad.sum())
    if n_bad:
        if isinstance(nodata_fill, (int, float)):
            vals[bad] = float(nodata_fill)
        elif nodata_fill == "mean":
            vals[bad] = np.nanmean(vals)
        else:   # idw from the nearest valid nodes
            good = ~bad
            if good.sum() == 0:
                raise ValueError("DEM returned nodata at every node")
            tree = cKDTree(np.c_[xs[good], ys[good]])
            k = min(6, int(good.sum()))
            d, idx = tree.query(np.c_[xs[bad], ys[bad]], k=k)
            d = np.atleast_2d(d)
            idx = np.atleast_2d(idx)
            w = 1.0 / np.maximum(d, 1e-6)
            vals[bad] = (vals[good][idx] * w).sum(axis=1) / w.sum(axis=1)
        if verbose:
            print(f"    filled {n_bad} nodata node(s) using '{nodata_fill}'")

    vals = vals + float(vertical_offset)
    if verbose:
        print(f"    elevation: min {np.min(vals):.1f}  "
              f"median {np.median(vals):.1f}  max {np.max(vals):.1f} m")
    return vals, n_bad


def smooth_elevations(xs, ys, z, window_m):
    """
    Optional neighbourhood mean. DEM noise at 30 m posting can put a spurious
    2-3 m step between adjacent junctions 10 m apart, which shows up as
    phantom pressure structure.
    """
    if not window_m or window_m <= 0:
        return z
    tree = cKDTree(np.c_[xs, ys])
    out = np.array(z, dtype=float)
    for i, (x, y) in enumerate(zip(xs, ys)):
        idx = tree.query_ball_point([x, y], window_m)
        if idx:
            out[i] = float(np.mean(np.asarray(z)[idx]))
    return out
