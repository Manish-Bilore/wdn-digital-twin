"""
wdnkit.topology
===============
Turns raw CAD centrelines into a clean, connected, EPANET-safe node/link graph.

The steps exist because CAD drawings are drawn to be looked at, not solved:
endpoints miss each other by millimetres, branch lines are drawn as separate
polylines with visible gaps at the tee, service stubs clutter the graph, and
`linemerge` can hand back closed rings that EPANET rejects outright.
"""

from __future__ import annotations

from collections import Counter, defaultdict

import numpy as np
from scipy.spatial import cKDTree
from shapely.geometry import LineString
from shapely.ops import linemerge, substring, unary_union


def quantise(ls: LineString, ndp: int = 3):
    cc = []
    for pt in ls.coords:
        q = (round(float(pt[0]), ndp), round(float(pt[1]), ndp))
        if not cc or q != cc[-1]:
            cc.append(q)
    return LineString(cc) if len(cc) > 1 else None


def endpoints(seg: LineString):
    return (tuple(np.round(seg.coords[0][:2], 3)),
            tuple(np.round(seg.coords[-1][:2], 3)))


def degree_map(segs):
    deg = Counter()
    for s in segs:
        a, b = endpoints(s)
        deg[a] += 1
        deg[b] += 1
    return deg


def weld_endpoints(lines, tol: float):
    """Union-find over endpoints within `tol`, snapping each cluster to its mean."""
    pts = sorted({l.coords[0][:2] for l in lines} | {l.coords[-1][:2] for l in lines})
    if not pts:
        return lines
    tree = cKDTree(np.array(pts))
    parent = list(range(len(pts)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for a, b in tree.query_pairs(tol):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    grp = defaultdict(list)
    for i in range(len(pts)):
        grp[find(i)].append(i)
    rep = {}
    for members in grp.values():
        c = np.array([pts[i] for i in members]).mean(axis=0)
        c = (round(float(c[0]), 3), round(float(c[1]), 3))
        for i in members:
            rep[pts[i]] = c

    out = []
    for l in lines:
        c = [tuple(p[:2]) for p in l.coords]
        c[0], c[-1] = rep.get(c[0], c[0]), rep.get(c[-1], c[-1])
        cc = [c[0]] + [q for i, q in enumerate(c[1:], 1) if q != c[i - 1]]
        if len(cc) > 1:
            out.append(LineString(cc))
    return out


def node_and_merge(lines):
    """Split at every intersection, then collapse degree-2 chains."""
    m = linemerge(unary_union(lines))
    return list(m.geoms) if m.geom_type == "MultiLineString" else [m]


def split_rings(segs, min_len: float = 0.0):
    """
    EPANET error 222: a link may not start and end at the same node. Split any
    closed ring in half.

    This deliberately does NOT delete short links. An earlier version dropped
    everything below `min_len`, which severed the graph wherever a sliver sat
    between two real branches - on the IITB drawing that quietly removed 40 %
    of the network. Short links are contracted instead, by
    `contract_short_edges`, which preserves connectivity.
    """
    out = []
    for s in segs:
        a, b = endpoints(s)
        if a == b:
            if s.length <= 0.0:
                continue                       # genuinely degenerate, no length
            out.append(substring(s, 0.0, 0.5, normalized=True))
            out.append(substring(s, 0.5, 1.0, normalized=True))
        else:
            out.append(s)
    return [s for s in out if endpoints(s)[0] != endpoints(s)[1] and s.length > 0]


def contract_short_edges(segs, min_len: float, max_iter: int = 6, verbose=True):
    """
    Collapse links shorter than `min_len` by welding their two endpoints into
    one node, rather than deleting the link. Slivers appear after noding and
    island bridging; deleting them disconnects the network, contracting them
    does not.
    """
    n0 = len(segs)
    for _ in range(max_iter):
        short = [s for s in segs
                 if s.length < min_len and endpoints(s)[0] != endpoints(s)[1]]
        if not short:
            break
        segs = weld_endpoints(segs, min_len)
        segs = node_and_merge(segs)
    if verbose and len(segs) != n0:
        print(f"    contracted {n0 - len(segs)} sliver link(s) below {min_len} m")
    return segs


def prune_dangles(segs, min_len: float, max_iter: int = 8):
    """Drop short dead-end stubs (building service taps) and re-merge."""
    for _ in range(max_iter):
        deg = degree_map(segs)
        drop = set()
        for i, s in enumerate(segs):
            a, b = endpoints(s)
            if s.length < min_len and (deg[a] == 1 or deg[b] == 1):
                drop.add(i)
        if not drop:
            break
        segs = node_and_merge([s for i, s in enumerate(segs) if i not in drop])
    return segs


def components(segs):
    adj = defaultdict(set)
    for seg in segs:
        a, b = endpoints(seg)
        adj[a].add(b)
        adj[b].add(a)
    seen, comps = set(), []
    for n in adj:
        if n in seen:
            continue
        stack, comp = [n], set()
        while stack:
            u = stack.pop()
            if u in comp:
                continue
            comp.add(u)
            stack.extend(adj[u] - comp)
        seen |= comp
        comps.append(comp)
    comps.sort(key=len, reverse=True)
    return comps


def bridge_islands(segs, max_gap: float, verbose=True):
    """
    Reconnect islands left by the draughtsman. Branch lines are frequently drawn
    as separate polylines that stop short of the main, so raising the snap
    tolerance does not fix it - the gap is metres, not millimetres. Each island
    is joined to the main component by the shortest possible connector.
    """
    added, bridged_len = 0, 0.0
    while True:
        comps = components(segs)
        if len(comps) <= 1:
            break
        main = comps[0]
        mpts = np.array(list(main))
        tree = cKDTree(mpts)
        best = None
        for comp in comps[1:]:
            cpts = np.array(list(comp))
            d, j = tree.query(cpts, k=1)
            k = int(np.argmin(d))
            if best is None or d[k] < best[0]:
                best = (float(d[k]), tuple(cpts[k]), tuple(mpts[j[k]]))
        if best is None or best[0] > max_gap:
            break
        segs = segs + [LineString([best[1], best[2]])]
        added += 1
        bridged_len += best[0]
    if verbose and added:
        print(f"    bridged {added} island(s), {bridged_len:,.0f} m of connectors")
    return segs


def keep_largest_component(segs, verbose=True):
    comps = components(segs)
    if len(comps) <= 1:
        return segs
    keep = comps[0]
    dropped = sum(len(c) for c in comps[1:])
    if verbose:
        print(f"    dropped {len(comps)-1} unreachable island(s), {dropped} node(s)")
    return [s for s in segs if endpoints(s)[0] in keep]


def build(lines, cfg, verbose=True):
    """Full CAD-to-graph pipeline. Returns a list of LineString links."""
    t = cfg.get("topology", {}) if hasattr(cfg, "get") else cfg
    snap = cfg.get("topology.snap_tolerance_m", 0.5)
    prune = cfg.get("topology.prune_dangles_below_m", 7.0)
    minseg = cfg.get("topology.min_segment_length_m", 0.5)
    do_bridge = cfg.get("topology.bridge_islands", True)
    maxgap = cfg.get("topology.max_bridge_gap_m", 150.0)

    segs = [q for q in (quantise(l) for l in lines) if q is not None]
    if verbose:
        print(f"    input: {len(segs)} centrelines, "
              f"{sum(s.length for s in segs):,.0f} m")

    segs = weld_endpoints(segs, snap)
    segs = node_and_merge(segs)
    if prune and prune > 0:
        segs = prune_dangles(segs, prune)
    segs = split_rings(segs)
    if do_bridge:
        segs = bridge_islands(segs, maxgap, verbose)
    # contract slivers (never delete them - that severs the graph), then split
    # any remaining ring, and only then filter for connectivity
    segs = contract_short_edges(segs, minseg, verbose=verbose)
    segs = split_rings(segs)
    len_before = sum(s.length for s in segs)
    segs = keep_largest_component(segs, verbose)
    len_after = sum(s.length for s in segs)
    if verbose and len_before - len_after > 1.0:
        print(f"    ! connectivity filter removed {len_before-len_after:,.0f} m "
              f"({100*(len_before-len_after)/len_before:.1f} %) of pipe - "
              f"raise topology.max_bridge_gap_m if that is too much")

    ncomp = len(components(segs))
    if ncomp != 1:
        raise RuntimeError(
            f"topology still has {ncomp} components after cleanup - "
            "this is a bug; EPANET cannot solve a disconnected network")

    deg = degree_map(segs)
    if verbose:
        dd = dict(sorted(Counter(deg.values()).items()))
        print(f"    graph: {len(segs)} links, {len(deg)} nodes, "
              f"{sum(s.length for s in segs):,.0f} m, degree {dd}")
    return segs


def node_table(segs):
    """Stable node ids plus coordinates, degree and topological class."""
    deg = degree_map(segs)
    coords = sorted(deg.keys())
    nid = {c: f"J{i+1}" for i, c in enumerate(coords)}
    rows = []
    for c in coords:
        d = deg[c]
        rows.append(dict(node_id=nid[c], x=c[0], y=c[1], degree=d,
                         node_type=("dead_end" if d == 1
                                    else "bend" if d == 2 else "junction")))
    return nid, rows
