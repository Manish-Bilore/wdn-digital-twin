"""
wdnkit.hydraulics
=================
Assigns pipe diameters and materials, builds the EPANET 2.2 model with WNTR,
picks and calibrates the source, and runs the steady-state peak-demand solve.

Diameter logic, in order:
  1. nearest CAD annotation within `max_label_distance_m`
  2. otherwise the configured default
  3. cap dead-end service pipes at `service_diameter_cap_mm`
  4. raise anything that would exceed `max_velocity_ms` at its tree flow to the
     next size on the commercial ladder

Step 4 is what makes an annotated-but-incomplete drawing solvable. Real CAD
labels are never reduced by it - only raised - and both values are kept so the
change is auditable.
"""

from __future__ import annotations

from collections import defaultdict

import numpy as np

from .topology import endpoints


def assign_diameters(segs, annotations, cfg, verbose=True):
    """Nearest-label assignment with a distance cutoff."""
    from scipy.spatial import cKDTree

    ladder = list(cfg.get("hydraulics.diameter_ladder_mm"))
    default_d = float(cfg.get("hydraulics.default_diameter_mm"))
    cap = float(cfg.get("hydraulics.service_diameter_cap_mm"))
    max_ld = float(cfg.get("hydraulics.max_label_distance_m", 60.0))

    n = len(segs)
    dia = np.full(n, default_d, dtype=float)
    mat = np.array(["UNK"] * n, dtype=object)
    ldist = np.full(n, np.inf)

    labelled = [a for a in annotations if a.get("diameter_mm")]
    if labelled:
        tree = cKDTree(np.array([[a["x"], a["y"]] for a in labelled]))
        mids = np.array([[s.interpolate(0.5, normalized=True).x,
                          s.interpolate(0.5, normalized=True).y] for s in segs])
        d, idx = tree.query(mids, k=1)
        for i in range(n):
            if d[i] <= max_ld:
                dia[i] = float(labelled[idx[i]]["diameter_mm"])
                m = labelled[idx[i]].get("material")
                mat[i] = m if m else "UNK"
                ldist[i] = d[i]

    from_cad = np.isfinite(ldist)
    if verbose:
        print(f"    {int(from_cad.sum())}/{n} pipes took a diameter from CAD "
              f"annotation (within {max_ld:.0f} m)")

    # dead-end services cannot be larger than the cap
    deg = defaultdict(int)
    for s in segs:
        a, b = endpoints(s)
        deg[a] += 1
        deg[b] += 1
    for i, s in enumerate(segs):
        a, b = endpoints(s)
        if deg[a] == 1 or deg[b] == 1:
            dia[i] = min(dia[i], cap)

    dia = np.array([min(ladder, key=lambda v: abs(v - x)) for x in dia], dtype=float)
    return dia, mat, ldist, from_cad


def tree_flows(segs, nid, demand_by_id, src_key, peak_factor):
    """Cumulative downstream demand on a BFS tree rooted at the source."""
    adj = defaultdict(list)
    for i, s in enumerate(segs):
        a, b = endpoints(s)
        adj[a].append((b, i))
        adj[b].append((a, i))

    order, parent, parent_pipe, seen = [], {}, {}, {src_key}
    q = [src_key]
    while q:
        u = q.pop(0)
        order.append(u)
        for v, i in adj[u]:
            if v not in seen:
                seen.add(v)
                parent[v] = u
                parent_pipe[v] = i
                q.append(v)

    acc = {k: demand_by_id[nid[k]] for k in adj}
    flow = np.zeros(len(segs))
    for u in reversed(order):
        if u in parent:
            flow[parent_pipe[u]] += acc[u]
            acc[parent[u]] = acc.get(parent[u], 0.0) + acc[u]
    return flow * float(peak_factor)


def raise_undersized(dia, flows_lps, cfg, verbose=True):
    """Bump diameters so peak velocity stays under the limit."""
    ladder = np.array(sorted(cfg.get("hydraulics.diameter_ladder_mm")), dtype=float)
    vmax = float(cfg.get("hydraulics.max_velocity_ms"))
    q = np.asarray(flows_lps, dtype=float) / 1000.0
    d_req = np.sqrt(4.0 * np.maximum(q, 0) / (np.pi * vmax)) * 1000.0

    out = np.array(dia, dtype=float)
    for i in range(len(out)):
        if d_req[i] <= ladder[-1]:
            need = ladder[int(np.searchsorted(ladder, d_req[i]))]
        else:
            need = ladder[-1]
        out[i] = max(out[i], need)
    n = int((out > np.asarray(dia)).sum())
    if verbose:
        print(f"    {n} pipe(s) raised by the velocity criterion "
              f"(v_max = {vmax} m/s)")
    return out, n


def pick_source(cfg, segs, dia, nid, nodes_xy, verbose=True):
    """Locate the supply node."""
    mode = cfg.get("hydraulics.source.mode", "auto_largest_north")

    if mode == "coordinates":
        e, n = cfg.get("hydraulics.source.coordinates")
        keys = list(nid.keys())
        arr = np.array(keys, dtype=float)
        i = int(np.argmin((arr[:, 0] - e) ** 2 + (arr[:, 1] - n) ** 2))
        key = keys[i]
        if verbose:
            print(f"    source snapped to {nid[key]} "
                  f"({np.hypot(arr[i,0]-e, arr[i,1]-n):.1f} m from the given point)")
        return key

    if mode == "layer":
        import geopandas as gpd
        g = gpd.read_file(cfg.get("hydraulics.source.path"))
        p = g.geometry.iloc[0]
        keys = list(nid.keys())
        arr = np.array(keys, dtype=float)
        i = int(np.argmin((arr[:, 0] - p.x) ** 2 + (arr[:, 1] - p.y) ** 2))
        return keys[i]

    # default: northernmost endpoint of the largest-diameter mains
    big = float(np.max(dia))
    cand = set()
    for i, s in enumerate(segs):
        if dia[i] >= big:
            a, b = endpoints(s)
            cand |= {a, b}
    key = max(cand, key=lambda c: c[1])
    if verbose:
        print(f"    source auto-picked at {nid[key]} "
              f"(northernmost end of the {big:.0f} mm main)")
    return key


def _diagnose(wn, nodes_df, pipes_df, src_node, exc):
    """
    EPANET error 110 says only "cannot solve". Work out which of the usual
    causes it actually is and say so.
    """
    import numpy as _np
    print("\n    EPANET failed to converge - diagnosing")
    print(f"    {exc}")

    # 1. connectivity from the source
    adj = {}
    for _, r in pipes_df.iterrows():
        adj.setdefault(r.node1, set()).add(r.node2)
        adj.setdefault(r.node2, set()).add(r.node1)
    seen, stack = {src_node}, [src_node]
    while stack:
        u = stack.pop()
        for v in adj.get(u, ()):
            if v not in seen:
                seen.add(v)
                stack.append(v)
    orphan = set(nodes_df.node_id) - seen
    if orphan:
        sample = ", ".join(sorted(orphan)[:8])
        print(f"    CAUSE: {len(orphan)} node(s) unreachable from the source "
              f"{src_node}: {sample}{' ...' if len(orphan) > 8 else ''}")
        print("           a disconnected node makes the system singular. "
              "Raise topology.max_bridge_gap_m,\n"
              "           or lower topology.min_segment_length_m.")
        return
    print(f"    connectivity OK ({len(seen)}/{len(nodes_df)} nodes reachable)")

    # 2. geometry sanity
    bad_d = pipes_df[pipes_df.diameter_mm <= 0]
    bad_l = pipes_df[pipes_df.length_m <= 0]
    if len(bad_d) or len(bad_l):
        print(f"    CAUSE: {len(bad_d)} pipe(s) with non-positive diameter, "
              f"{len(bad_l)} with non-positive length")
        return

    # 3. undersized mains - the usual culprit once connectivity is fine
    q = pipes_df.get("peak_tree_flow_lps")
    if q is not None:
        v = 4.0 * (q / 1000.0) / (_np.pi * (pipes_df.diameter_mm / 1000.0) ** 2)
        hot = pipes_df.assign(v_ms=v).nlargest(8, "v_ms")
        print("    highest implied velocities (undersized mains stall the solver):")
        for _, r in hot.iterrows():
            print(f"      {r.pipe_id:<8} {r.diameter_mm:>5.0f} mm  "
                  f"{r.peak_tree_flow_lps:>8.3f} L/s  {r.v_ms:>7.2f} m/s  "
                  f"{r.length_m:>7.1f} m")
        if float(hot.v_ms.max()) > 6.0:
            print("    CAUSE: implied velocity above 6 m/s. The velocity-sizing "
                  "pass did not fire\n"
                  "           on these pipes - check that "
                  "hydraulics.diameter_ladder_mm reaches a size\n"
                  "           large enough for the peak flow, or lower "
                  "demand.peak_factor.")


def build_and_run(cfg, nodes_df, pipes_df, src_node, crs, verbose=True):
    """Write the .inp, solve, calibrate the source head, return results."""
    import wntr

    peak = float(cfg.get("demand.peak_factor", 1.0))
    rough = cfg.get("hydraulics.roughness_by_material")

    wn = wntr.network.WaterNetworkModel()
    wn.options.hydraulic.headloss = cfg.get("hydraulics.headloss", "H-W")
    wn.options.hydraulic.demand_model = "DDA"
    wn.options.time.duration = 0
    wn.options.time.hydraulic_timestep = 3600

    for _, r in nodes_df.iterrows():
        wn.add_junction(r.node_id,
                        base_demand=float(r.demand_lps) / 1000.0,
                        elevation=float(r.elevation_m),
                        coordinates=(float(r.x), float(r.y)))

    wn.add_pattern("PEAK", [peak])
    for j in wn.junction_name_list:
        wn.get_node(j).demand_timeseries_list[0].pattern_name = "PEAK"

    src = nodes_df.set_index("node_id").loc[src_node]
    head_mode = cfg.get("hydraulics.source.head_mode", "min_residual")
    fixed = cfg.get("hydraulics.source.fixed_head_m")
    res_head = float(fixed) if (head_mode == "fixed" and fixed is not None) \
        else float(nodes_df.elevation_m.max()) + 15.0

    wn.add_reservoir("R1", base_head=res_head,
                     coordinates=(float(src.x) + 25.0, float(src.y) + 25.0))
    big = float(pipes_df.diameter_mm.max()) / 1000.0
    wn.add_pipe("P_SRC", "R1", src_node, length=25.0, diameter=big,
                roughness=130.0, minor_loss=0.0, initial_status="OPEN")

    for _, r in pipes_df.iterrows():
        wn.add_pipe(r.pipe_id, r.node1, r.node2,
                    length=max(float(r.length_m), 0.1),
                    diameter=float(r.diameter_mm) / 1000.0,
                    roughness=float(rough.get(r.material, rough.get("UNK", 110.0))),
                    minor_loss=0.0, initial_status="OPEN")

    # write the model before solving, so a failed run still leaves something
    # to open in EPANET rather than losing the whole build
    import os as _os
    _dbg = _os.environ.get("WDNKIT_DEBUG_INP", "")
    if _dbg:
        wntr.network.write_inpfile(wn, _dbg, units="LPS")

    sim = wntr.sim.EpanetSimulator(wn)
    try:
        res = sim.run_sim()
    except Exception as exc:
        _diagnose(wn, nodes_df, pipes_df, src_node, exc)
        raise

    if head_mode == "min_residual":
        target = float(cfg.get("hydraulics.source.min_residual_m", 15.0))
        for _ in range(8):
            pmin = float(res.node["pressure"].iloc[0][nodes_df.node_id].min())
            if abs(pmin - target) < 0.05:
                break
            res_head += (target - pmin)
            wn.get_node("R1").head_timeseries.base_value = res_head
            res = sim.run_sim()
        if verbose:
            print(f"    source head calibrated to {res_head:.2f} m "
                  f"for a {target:.0f} m minimum residual")

    return wn, res, res_head
