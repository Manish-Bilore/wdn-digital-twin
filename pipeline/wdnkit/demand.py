"""
wdnkit.demand
=============
Allocates a single area-wide demand total across the network nodes.

Default is the area-based (Voronoi/Thiessen) method: each node is assigned the
share of the service area that is closer to it than to any other node, and takes
that same share of total demand. The tessellation is clipped to the supplied
boundary so that nodes on the edge of the network do not claim area outside the
campus.
"""

from __future__ import annotations

import numpy as np
import shapely
from scipy.spatial import cKDTree
from shapely.geometry import MultiPoint, Point


def total_demand_lps(cfg) -> tuple[float, dict]:
    """Average-day demand in L/s, plus the provenance of the number."""
    total_lpd = cfg.get("demand.total_lpd")
    pop = cfg.get("demand.population")
    lpcd = cfg.get("demand.lpcd", 135.0)

    if total_lpd is not None:
        lpd = float(total_lpd)
        prov = {"basis": "total_lpd", "total_lpd": lpd}
    else:
        lpd = float(pop) * float(lpcd)
        prov = {"basis": "population x lpcd", "population": pop,
                "lpcd": lpcd, "total_lpd": lpd}

    ufw = float(cfg.get("demand.unaccounted_for_water", 0.0) or 0.0)
    if ufw:
        lpd = lpd / (1.0 - ufw) if ufw < 1 else lpd
        prov["unaccounted_for_water"] = ufw
        prov["total_lpd_with_ufw"] = lpd

    lps = lpd / 86400.0
    prov["avg_day_lps"] = round(lps, 4)
    return lps, prov


def service_area(node_xy, boundary_geom=None, network_geom=None,
                 max_service_distance_m=None, fallback_ratio=0.25,
                 fallback_buffer=45.0, verbose=True):
    """
    The polygon demand is spread over.

    A raw boundary is the wrong answer on its own: parts of a campus lie far
    from any main, and an unclipped Voronoi tessellation hands that unserved
    ground to whichever peripheral node happens to be nearest. On the IITB
    network 16 % of the boundary sits more than 150 m from any pipe, and a
    single dead end ended up carrying 12.5 % of total campus demand.

    Setting `max_service_distance_m` restricts the tessellation to ground
    actually within reach of a main.
    """
    if boundary_geom is not None and not boundary_geom.is_empty:
        area = boundary_geom
        src = "boundary"
        if max_service_distance_m and network_geom is not None:
            reach = network_geom.buffer(float(max_service_distance_m))
            clipped = area.intersection(reach)
            if not clipped.is_empty and clipped.area > 0:
                if verbose:
                    lost = 100.0 * (1.0 - clipped.area / area.area)
                    print(f"    service area clipped to {max_service_distance_m:.0f} m "
                          f"of a main: {clipped.area/1e6:.3f} km2 "
                          f"({lost:.1f} % of the boundary is beyond reach)")
                area, src = clipped, f"boundary within {max_service_distance_m:.0f} m of a main"
        return area, src

    pts = MultiPoint([Point(p) for p in node_xy])
    return shapely.concave_hull(pts, ratio=fallback_ratio).buffer(fallback_buffer), \
        "concave_hull"


def voronoi_allocation(node_xy, total_lps, boundary_geom=None,
                       network_geom=None, max_service_distance_m=None):
    """Area-weighted allocation. Returns (demand_lps, area_m2, polygons, source)."""
    node_xy = np.asarray(node_xy, dtype=float)
    area_poly, src = service_area(node_xy, boundary_geom, network_geom,
                                  max_service_distance_m)

    pts = MultiPoint([Point(p) for p in node_xy])
    cells = shapely.voronoi_polygons(pts, extend_to=area_poly.envelope)
    cells = list(cells.geoms) if hasattr(cells, "geoms") else [cells]

    tree = cKDTree(node_xy)
    area = np.zeros(len(node_xy))
    polys = [None] * len(node_xy)
    for c in cells:
        c = c.intersection(area_poly)
        if c.is_empty:
            continue
        rep = c.representative_point()
        _, i = tree.query([rep.x, rep.y], k=1)
        i = int(i)
        area[i] += c.area
        polys[i] = c if polys[i] is None else polys[i].union(c)

    if area.sum() <= 0:
        raise ValueError("Voronoi allocation produced zero total area - check "
                         "that the boundary and the network overlap")
    share = area / area.sum()
    return share * total_lps, area, polys, src


def uniform_allocation(n, total_lps):
    return np.full(n, total_lps / n), np.zeros(n), [None] * n, "uniform"


def pipe_length_allocation(node_ids, links, total_lps):
    """
    Each node takes half the length of every pipe incident on it, then demand in
    proportion. Useful where the served population tracks main length rather
    than plan area (linear/ribbon development).
    """
    w = {nid: 0.0 for nid in node_ids}
    for l in links:
        w[l["node1"]] = w.get(l["node1"], 0.0) + l["length_m"] / 2.0
        w[l["node2"]] = w.get(l["node2"], 0.0) + l["length_m"] / 2.0
    arr = np.array([w[n] for n in node_ids], dtype=float)
    if arr.sum() <= 0:
        return uniform_allocation(len(node_ids), total_lps)
    return arr / arr.sum() * total_lps, arr, [None] * len(node_ids), "pipe_length"


def allocate(cfg, node_ids, node_xy, links, boundary_geom=None, verbose=True,
             network_geom=None):
    total, prov = total_demand_lps(cfg)
    method = cfg.get("demand.allocation", "voronoi")
    max_sd = cfg.get("demand.max_service_distance_m")

    if method == "voronoi":
        d, a, polys, src = voronoi_allocation(node_xy, total, boundary_geom,
                                              network_geom, max_sd)
    elif method == "pipe_length":
        d, a, polys, src = pipe_length_allocation(node_ids, links, total)
    else:
        d, a, polys, src = uniform_allocation(len(node_ids), total)

    if verbose:
        print(f"    {prov['basis']}: {prov['total_lpd']:,.0f} L/day "
              f"= {total:.3f} L/s average day")
        print(f"    allocation '{method}' over the {src}; "
              f"nodal demand {d.min():.4f} - {d.max():.4f} L/s")
        share = np.sort(d)[::-1]
        top1 = 100.0 * share[0] / d.sum()
        top10 = 100.0 * share[:10].sum() / d.sum()
        print(f"    concentration: largest node {top1:.1f} %, top 10 {top10:.1f} % "
              "of total demand")
        if top1 > 5.0:
            print("    ! one node carries an implausible share - if it is a "
                  "peripheral dead end,\n"
                  "      set demand.max_service_distance_m (150 m is a "
                  "reasonable starting point)")
    prov["allocation"] = method
    prov["service_area_source"] = src
    return d, a, polys, prov
