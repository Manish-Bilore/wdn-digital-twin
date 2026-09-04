"""
wdnkit.georef_auto
==================
Solves the CAD -> world transform automatically, with no clicking.

Idea
----
A campus distribution network fills its campus. So the transform that is
"correct" is the one that makes the network's footprint sit on top of the
service boundary you already digitised. That turns georeferencing into a small
optimisation: search over (dE, dN, theta, scale) for the transform maximising
the intersection-over-union of the network footprint and the boundary polygon.

This is strictly better than the centroid/extent fit in `fit_to_boundary`,
which only matches first moments and is blind to shape. It is also better than
two hand-clicked points, which carry the full weight of two pick errors; here
every metre of pipe contributes to the fit.

Search
------
  1. centroid alignment for a starting guess
  2. coarse grid over translation (and rotation, if enabled)
  3. Nelder-Mead refinement on the free parameters

Report
------
IoU at the optimum, plus the drop-off around it. A sharp, high peak means the
footprint genuinely keys into the boundary. A flat, low one means it does not,
and the function says so rather than returning a confident wrong answer.
"""

from __future__ import annotations

import math

import numpy as np
import shapely
from scipy.optimize import minimize
from shapely.affinity import affine_transform
from shapely.geometry import MultiPoint, Point


def _params_to_matrix(dx, dy, theta=0.0, scale=1.0):
    ct, st = math.cos(theta), math.sin(theta)
    a, b = scale * ct, -scale * st
    d, e = scale * st, scale * ct
    return (a, b, d, e, dx, dy)


def footprint(points_xy, buffer_m=35.0, ratio=0.25):
    """Concave hull of the network nodes, buffered to a service-area polygon."""
    mp = MultiPoint([Point(p) for p in points_xy])
    hull = shapely.concave_hull(mp, ratio=ratio)
    return hull.buffer(buffer_m)


def _iou(poly_a, poly_b):
    if poly_a.is_empty or poly_b.is_empty:
        return 0.0
    inter = poly_a.intersection(poly_b).area
    union = poly_a.union(poly_b).area
    return float(inter / union) if union > 0 else 0.0


def register(points_xy, boundary_geom, allow_rotation=False, allow_scale=False,
             buffer_m=35.0, search_radius_m=600.0, coarse_step_m=40.0,
             rotation_range_deg=8.0, rotation_step_deg=1.0, verbose=True):
    """
    Returns (transform_6tuple, diagnostics_dict).

    points_xy        network node coordinates in CAD units
    boundary_geom    the service boundary, in the project CRS
    allow_rotation   solve a rotation as well as a translation
    allow_scale      solve a scale factor (leave False when CAD units are metres)
    """
    pts = np.asarray(points_xy, dtype=float)
    fp0 = footprint(pts, buffer_m)

    # ---- 1. centroid alignment -------------------------------------------
    c_cad = np.array([fp0.centroid.x, fp0.centroid.y])
    c_world = np.array([boundary_geom.centroid.x, boundary_geom.centroid.y])
    dx0, dy0 = c_world - c_cad

    def score(dx, dy, theta=0.0, scale=1.0):
        tr = _params_to_matrix(dx, dy, theta, scale)
        return _iou(affine_transform(fp0, tr), boundary_geom)

    base = score(dx0, dy0)
    if verbose:
        print(f"    centroid start: IoU {base:.3f}")

    # ---- 2. coarse grid ---------------------------------------------------
    offs = np.arange(-search_radius_m, search_radius_m + 1e-9, coarse_step_m)
    thetas = [0.0]
    if allow_rotation:
        r = math.radians(rotation_range_deg)
        stepr = math.radians(rotation_step_deg)
        thetas = list(np.arange(-r, r + 1e-9, stepr))

    best = (base, dx0, dy0, 0.0, 1.0)
    for th in thetas:
        for ddx in offs:
            for ddy in offs:
                s = score(dx0 + ddx, dy0 + ddy, th)
                if s > best[0]:
                    best = (s, dx0 + ddx, dy0 + ddy, th, 1.0)
    if verbose:
        print(f"    coarse grid   : IoU {best[0]:.3f} "
              f"(shift {best[1]-dx0:+.0f}, {best[2]-dy0:+.0f} m"
              + (f", rot {math.degrees(best[3]):+.2f} deg)" if allow_rotation else ")"))

    # ---- 3. local refinement ---------------------------------------------
    free = ["dx", "dy"] + (["theta"] if allow_rotation else []) \
                        + (["scale"] if allow_scale else [])
    x0 = [best[1], best[2]] + ([best[3]] if allow_rotation else []) \
                            + ([1.0] if allow_scale else [])

    def unpack(x):
        v = dict(dx=x[0], dy=x[1], theta=0.0, scale=1.0)
        i = 2
        if allow_rotation:
            v["theta"] = x[i]; i += 1
        if allow_scale:
            v["scale"] = x[i]; i += 1
        return v

    def neg(x):
        v = unpack(x)
        return -score(v["dx"], v["dy"], v["theta"], v["scale"])

    res = minimize(neg, x0, method="Nelder-Mead",
                   options=dict(xatol=0.25, fatol=1e-5, maxiter=2000))
    v = unpack(res.x)
    iou = -res.fun
    if verbose:
        print(f"    refined       : IoU {iou:.3f}")

    tr = _params_to_matrix(v["dx"], v["dy"], v["theta"], v["scale"])

    # ---- 4. how sharp is the optimum? ------------------------------------
    probe = {}
    for d in (25.0, 50.0, 100.0):
        drops = [score(v["dx"] + sx * d, v["dy"] + sy * d, v["theta"], v["scale"])
                 for sx, sy in ((1, 0), (-1, 0), (0, 1), (0, -1))]
        probe[f"iou_at_{int(d)}m"] = round(float(np.mean(drops)), 4)

    contrast = iou - probe["iou_at_100m"]
    diag = dict(
        iou=round(float(iou), 4),
        centroid_only_iou=round(float(base), 4),
        translation=[round(float(v["dx"]), 2), round(float(v["dy"]), 2)],
        rotation_deg=round(math.degrees(v["theta"]), 4),
        scale=round(float(v["scale"]), 6),
        sharpness=probe,
        contrast_100m=round(float(contrast), 4),
        converged=bool(res.success),
    )

    if verbose:
        print(f"    translation dE {v['dx']:,.2f}  dN {v['dy']:,.2f}")
        if allow_rotation:
            print(f"    rotation      {math.degrees(v['theta']):+.3f} deg")
        if allow_scale:
            print(f"    scale         {v['scale']:.6f}")
        print(f"    sharpness     IoU falls to {probe['iou_at_100m']:.3f} "
              f"100 m off the optimum (contrast {contrast:+.3f})")

        if iou < 0.55:
            print("    ! IoU below 0.55 - the network footprint and the boundary "
                  "do not agree well.\n"
                  "      Check that the boundary really is the campus outline "
                  "and that the\n      correct block of CAD linework was kept.")
        elif contrast < 0.05:
            print("    ! the optimum is flat - translation is poorly constrained "
                  "in at least\n      one direction. Treat the result as "
                  "provisional and verify against imagery.")
        else:
            print("    registration looks well constrained")

    return tr, diag


def transform_to_control_points(tr, cad_points):
    """Express a solved transform as control points, for config.yml."""
    a, b, d, e, dx, dy = tr
    out = []
    for (x, y) in cad_points:
        out.append(dict(cad=[round(float(x), 1), round(float(y), 1)],
                        world=[round(float(a * x + b * y + dx), 2),
                               round(float(d * x + e * y + dy), 2)]))
    return out
