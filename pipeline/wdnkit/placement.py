"""
wdnkit.placement
================
K-means zoning into district metered areas, Thiessen tessellation of the cluster
centroids, then the hierarchical overlay filter:

  F1 topology  junction (deg >= 3) or dead end (deg == 1)
  F2 terrain   elevation above the DMA mean
  F3 pressure  in the lower or upper tail of the DMA distribution
  F4 demand    above the DMA mean, intersected with the survivors

Every threshold is per-DMA rather than network-wide, so a low-lying zone is not
silently excluded by a criterion calibrated on a hilly one.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import shapely
from shapely.geometry import MultiPoint, Point
from sklearn.cluster import KMeans


def thiessen(centroids, hull):
    pts = MultiPoint([Point(c) for c in centroids])
    cells = shapely.voronoi_polygons(pts, extend_to=hull.envelope)
    cells = list(cells.geoms) if hasattr(cells, "geoms") else [cells]
    out = [None] * len(centroids)
    for c in cells:
        c = c.intersection(hull)
        if c.is_empty:
            continue
        rp = c.representative_point()
        i = int(np.argmin(((np.asarray(centroids) - np.array([rp.x, rp.y])) ** 2)
                          .sum(axis=1)))
        out[i] = c if out[i] is None else out[i].union(c)
    return out


def run(nodes: pd.DataFrame, cfg, boundary_geom=None, verbose=True):
    k = int(cfg.get("placement.n_clusters", 5))
    seed = int(cfg.get("placement.random_state", 42))
    min_per = int(cfg.get("placement.min_per_cluster", 5))
    lo_p = float(cfg.get("placement.pressure_low_percentile", 20)) / 100.0
    hi_p = float(cfg.get("placement.pressure_high_percentile", 80)) / 100.0
    topo_classes = list(cfg.get("placement.topology_classes",
                                ["junction", "dead_end"]))

    nodes = nodes.copy()
    xy = nodes[["x", "y"]].values

    if len(nodes) < k:
        raise ValueError(f"only {len(nodes)} nodes for {k} clusters")

    km = KMeans(n_clusters=k, n_init=25, random_state=seed).fit(xy)
    nodes["cluster"] = km.labels_ + 1
    cents = km.cluster_centers_

    hull = boundary_geom
    if hull is None or hull.is_empty:
        hull = shapely.concave_hull(MultiPoint([Point(p) for p in xy]),
                                    ratio=0.25).buffer(45.0)
    cells = thiessen(cents, hull)

    # ---- filters --------------------------------------------------------
    nodes["f1_topology"] = nodes.node_type.isin(topo_classes)

    nodes["dma_mean_elev"] = nodes.groupby("cluster").elevation_m.transform("mean")
    nodes["f2_elevation"] = nodes.elevation_m > nodes.dma_mean_elev

    p_lo = nodes.groupby("cluster").pressure_m.transform(lambda s: s.quantile(lo_p))
    p_hi = nodes.groupby("cluster").pressure_m.transform(lambda s: s.quantile(hi_p))
    nodes["dma_p_lo"], nodes["dma_p_hi"] = p_lo.round(2), p_hi.round(2)
    nodes["f3_pressure"] = (nodes.pressure_m <= p_lo) | (nodes.pressure_m >= p_hi)

    nodes["dma_mean_demand"] = nodes.groupby("cluster").demand_lps.transform("mean")
    nodes["f4_demand"] = nodes.demand_lps > nodes.dma_mean_demand

    nodes["passed"] = (nodes.f1_topology & nodes.f2_elevation
                       & nodes.f3_pressure & nodes.f4_demand)

    # ---- per-DMA top-up so no zone is left unmonitored -------------------
    sel = []
    topped = 0
    for c, grp in nodes.groupby("cluster"):
        keep = grp[grp.passed]
        if len(keep) < min_per:
            pool = grp[grp.f1_topology & grp.f2_elevation
                       & ~grp.node_id.isin(keep.node_id)] \
                .sort_values("demand_lps", ascending=False)
            need = min_per - len(keep)
            topped += min(need, len(pool))
            keep = pd.concat([keep, pool.head(need)])
        sel.append(keep)
    sel = pd.concat(sel).sort_values(["cluster", "demand_lps"],
                                     ascending=[True, False])
    nodes["selected"] = nodes.node_id.isin(sel.node_id)

    sel = sel.copy()
    sel["selection_reason"] = [_reason(r) for _, r in sel.iterrows()]
    sel["risk_flags"] = [_risks(r) for _, r in sel.iterrows()]

    attrition = {
        "all_nodes": len(nodes),
        "f1_topology": int(nodes.f1_topology.sum()),
        "f1_f2": int((nodes.f1_topology & nodes.f2_elevation).sum()),
        "f1_f2_f3": int((nodes.f1_topology & nodes.f2_elevation
                         & nodes.f3_pressure).sum()),
        "f1_f2_f3_f4": int(nodes.passed.sum()),
        "topped_up": topped,
        "selected": len(sel),
    }

    if verbose:
        print(f"    DMAs: {k} | selected {len(sel)} node(s)"
              + (f" ({topped} added by the per-DMA floor)" if topped else ""))
        print(f"    attrition: {attrition['all_nodes']} -> "
              f"{attrition['f1_topology']} -> {attrition['f1_f2']} -> "
              f"{attrition['f1_f2_f3']} -> {attrition['f1_f2_f3_f4']}")
        per = sel.groupby("cluster").size().to_dict()
        print(f"    per DMA: {{{', '.join(f'{int(a)}: {int(b)}' for a, b in per.items())}}}")

    return nodes, sel, cents, cells, attrition


def _reason(r) -> str:
    bits = ["dead end" if r.node_type == "dead_end" else "junction"]
    if r.f2_elevation:
        bits.append(f"elev {r.elevation_m:.1f} m > DMA mean {r.dma_mean_elev:.1f}")
    if r.pressure_m <= r.dma_p_lo:
        bits.append(f"low pressure {r.pressure_m:.1f} m (<= P{int(r.get('lo',20))} "
                    f"{r.dma_p_lo})")
    elif r.pressure_m >= r.dma_p_hi:
        bits.append(f"high pressure {r.pressure_m:.1f} m (>= {r.dma_p_hi})")
    if r.f4_demand:
        bits.append(f"demand {r.demand_lps:.4f} L/s > DMA mean {r.dma_mean_demand:.4f}")
    return "; ".join(bits)


def _risks(r) -> str:
    out = []
    if r.node_type == "dead_end":
        out.append("StagnationRisk")
    if r.pressure_m <= r.dma_p_lo:
        out.append("IntrusionRisk")
    if r.pressure_m >= r.dma_p_hi:
        out.append("LeakageRisk")
    if r.f4_demand:
        out.append("ServiceLevelRisk")
    return "|".join(out) if out else "None"
