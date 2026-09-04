"""
wdnkit.viz
==========
Map output with one consistent cartographic grammar.

Every figure produced here carries the same furniture, in the same places:

    title / subtitle          top left, outside the frame
    campus boundary           dark dashed outline, on every map
    network                   grey when it is context, coloured when it is the subject
    north arrow               top right, inside the frame
    scale bar                 bottom right, snapped to a round interval
    legend                    lower left, inside the frame
    ancillary block           below the frame: CRS, sources, model counts, date

The point is that any two of these maps can sit side by side in a report and be
read against each other without the reader re-learning the layout.
"""

from __future__ import annotations

import datetime as _dt
import textwrap as _tw

import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle

# ---------------------------------------------------------------- palette ----
DCOL = {40: "#b3cde3", 50: "#8c96c6", 65: "#9e9ac8", 75: "#88419d",
        100: "#f4a261", 125: "#ef8354", 150: "#e76f51", 200: "#e63946",
        250: "#9d0208", 300: "#5c0002", 350: "#3d0000", 400: "#210000"}
CCOL = ["#e63946", "#457b9d", "#f4a261", "#2a9d8f", "#8e44ad",
        "#e07a5f", "#3d5a80", "#81b29a", "#c9ada7", "#6d597a"]

NETWORK_GREY = "#9aa0a6"
BOUNDARY_COL = "#2f3336"
FURNITURE = "#3c4043"

FIGSIZE = (9.2, 11.4)
NICE_STEPS = [25, 50, 100, 200, 250, 500, 1000, 2000, 5000]


# --------------------------------------------------------------- furniture --
def _north_arrow(ax, pad=0.042, size=0.058):
    x = 1.0 - pad
    y = 1.0 - pad
    ax.annotate("", xy=(x, y), xytext=(x, y - size),
                xycoords="axes fraction", textcoords="axes fraction",
                arrowprops=dict(arrowstyle="-|>", color=FURNITURE,
                                linewidth=1.6, mutation_scale=16))
    ax.text(x, y + 0.008, "N", transform=ax.transAxes, ha="center",
            va="bottom", fontsize=10, fontweight="bold", color=FURNITURE)


def _scale_bar(ax, pad=0.04, target_frac=0.24, height=0.008):
    x0, x1 = ax.get_xlim()
    span = x1 - x0
    step = min(NICE_STEPS, key=lambda s: abs(s - span * target_frac))
    fx = step / span
    bx = 1.0 - pad - fx
    by = pad
    ax.add_patch(Rectangle((bx, by), fx / 2.0, height, transform=ax.transAxes,
                           facecolor="white", edgecolor=FURNITURE,
                           linewidth=0.7, zorder=21))
    ax.add_patch(Rectangle((bx + fx / 2.0, by), fx / 2.0, height,
                           transform=ax.transAxes, facecolor=FURNITURE,
                           edgecolor=FURNITURE, linewidth=0.7, zorder=21))
    ax.text(bx, by + height + 0.006, "0", transform=ax.transAxes, ha="center",
            va="bottom", fontsize=8, color=FURNITURE, zorder=22)
    ax.text(bx + fx, by + height + 0.006, f"{step:,} m", transform=ax.transAxes,
            ha="center", va="bottom", fontsize=8, color=FURNITURE, zorder=22)


def _ancillary(fig, meta, sources):
    bits = []
    if meta.get("crs"):
        bits.append(f"CRS {meta['crs']}")
    if meta.get("pipes") and meta.get("junctions"):
        bits.append(f"{meta['pipes']} pipes / {meta['junctions']} junctions / "
                    f"{meta.get('total_pipe_length_m', 0)/1000:.1f} km")
    if meta.get("peak_factor"):
        bits.append(f"peak factor {meta['peak_factor']}")
    line1 = "  \u00b7  ".join(bits)
    line2 = "  \u00b7  ".join(sources) if sources else ""
    line3 = (f"Generated {_dt.date.today().isoformat()} with wdnkit  \u00b7  "
             "hydraulics EPANET 2.2 (Hazen-Williams)")
    wrapped = []
    for t in (line1, line2, line3):
        if t:
            wrapped.extend(_tw.wrap(t, width=138) or [t])
    fig.text(0.035, 0.016, "\n".join(wrapped), ha="left", va="bottom",
             fontsize=7.3, color="#5f6368", linespacing=1.6)


def _frame(ax, boundary=None, title="", subtitle=""):
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    for s in ax.spines.values():
        s.set_edgecolor("#c8ccd0")
        s.set_linewidth(0.9)
    if boundary is not None:
        gpd.GeoSeries([boundary]).plot(ax=ax, facecolor="none",
                                       edgecolor=BOUNDARY_COL, linewidth=1.2,
                                       linestyle=(0, (5, 2.5)), zorder=4)
    # title and subtitle are drawn as explicit text so the two never collide,
    # whatever length the subtitle runs to
    sub_lines = _tw.wrap(subtitle, width=104) if subtitle else []
    if subtitle:
        ax.text(0.0, 1.008, "\n".join(sub_lines), transform=ax.transAxes,
                ha="left", va="bottom", fontsize=9.2, color="#5f6368",
                linespacing=1.45)
    if title:
        y = 1.008 + 0.019 * max(len(sub_lines), 1) + 0.008
        ax.text(0.0, y, title, transform=ax.transAxes, ha="left", va="bottom",
                fontsize=13.5, fontweight="bold", color="#202124")


def _finish(fig, ax, path, meta, sources):
    _north_arrow(ax)
    _scale_bar(ax)
    _ancillary(fig, meta or {}, sources or [])
    fig.subplots_adjust(left=0.035, right=0.965, top=0.905, bottom=0.105)
    fig.savefig(path, dpi=170)
    plt.close(fig)


def _boundary_legend():
    return Line2D([], [], color=BOUNDARY_COL, linestyle=(0, (5, 2.5)),
                  linewidth=1.2, label="Campus boundary")


# ------------------------------------------------------------------- maps ----
def network_map(gp, nodes, path, title="", subtitle="", boundary=None,
                meta=None, sources=None):
    fig, ax = plt.subplots(figsize=FIGSIZE)
    handles = []
    for d, sub in gp.groupby(gp.diameter_mm.astype(int)):
        col = DCOL.get(d, "k")
        sub.plot(ax=ax, color=col, linewidth=0.5 + d / 110.0, zorder=3)
        handles.append(Line2D([], [], color=col, linewidth=1.2 + d / 200.0,
                              label=f"{d} mm"))
    _frame(ax, boundary, title, subtitle)
    handles.append(_boundary_legend())
    ax.legend(handles=handles, title="Pipe diameter", fontsize=8,
              title_fontsize=9, loc="lower left", frameon=False, ncol=2)
    _finish(fig, ax, path, meta, sources)


def scalar_map(gp, nodes, col, path, title="", subtitle="", label="",
               cmap="RdYlBu", boundary=None, meta=None, sources=None):
    fig, ax = plt.subplots(figsize=FIGSIZE)
    gp.plot(ax=ax, color=NETWORK_GREY, linewidth=0.6, zorder=2)
    s = ax.scatter(nodes.x, nodes.y, c=nodes[col], s=18, cmap=cmap,
                   zorder=5, edgecolor="none")
    _frame(ax, boundary, title, subtitle)
    cb = fig.colorbar(s, ax=ax, shrink=0.42, pad=0.015, aspect=26)
    cb.set_label(label or col, fontsize=9)
    cb.ax.tick_params(labelsize=8)
    ax.legend(handles=[_boundary_legend(),
                       Line2D([], [], color=NETWORK_GREY, linewidth=1.2,
                              label="Distribution main")],
              fontsize=8, loc="upper left", frameon=True, framealpha=0.92,
              edgecolor="#d5d8dc", borderpad=0.7).set_zorder(30)
    _finish(fig, ax, path, meta, sources)


def demand_map(gv, gp, path, title="", subtitle="", boundary=None,
               meta=None, sources=None):
    fig, ax = plt.subplots(figsize=FIGSIZE)
    gv.plot(ax=ax, column="demand_lps", cmap="YlOrBr", linewidth=0.18,
            edgecolor="white", zorder=1, legend=True,
            legend_kwds={"shrink": 0.42, "pad": 0.015, "aspect": 26,
                         "label": "Nodal demand (L/s, average day)"})
    gp.plot(ax=ax, color="#3c4043", linewidth=0.5, zorder=3)
    _frame(ax, boundary, title, subtitle)
    ax.legend(handles=[_boundary_legend(),
                       Line2D([], [], color="#3c4043", linewidth=1.2,
                              label="Distribution main")],
              fontsize=8, loc="upper left", frameon=True, framealpha=0.92,
              edgecolor="#d5d8dc", borderpad=0.7).set_zorder(30)
    _finish(fig, ax, path, meta, sources)


def placement_map(gp, nodes, sel, gdma, path, title="", subtitle="",
                  boundary=None, meta=None, sources=None):
    k = int(nodes.cluster.max())
    fig, ax = plt.subplots(figsize=FIGSIZE)
    if gdma is not None and len(gdma):
        gdma.plot(ax=ax, color=[CCOL[i % len(CCOL)] for i in range(len(gdma))],
                  alpha=0.13, edgecolor="#b0b5ba", linewidth=0.7, zorder=0)
    gp.plot(ax=ax, color=NETWORK_GREY, linewidth=0.6, zorder=2)
    for c in range(1, k + 1):
        sub = nodes[nodes.cluster == c]
        ax.scatter(sub.x, sub.y, s=9, color=CCOL[(c - 1) % len(CCOL)],
                   alpha=0.55, zorder=3)
    ax.scatter(sel.x, sel.y, s=110, facecolor="none", edgecolor="black",
               linewidth=1.5, zorder=6)
    ax.scatter(sel.x, sel.y, s=44,
               color=[CCOL[(c - 1) % len(CCOL)] for c in sel.cluster], zorder=7)
    for _, r in sel.iterrows():
        ax.annotate(str(r.node_id).lstrip("J"), (r.x, r.y), fontsize=6.2,
                    xytext=(5, 5), textcoords="offset points", zorder=8)
    _frame(ax, boundary, title, subtitle)
    per = sel.groupby("cluster").size().to_dict()
    handles = [Line2D([], [], marker="o", ls="", color=CCOL[i % len(CCOL)],
                      markersize=7, label=f"DMA {i+1} - {per.get(i+1, 0)} sensors")
               for i in range(k)]
    handles += [Line2D([], [], marker="o", ls="", markerfacecolor="none",
                       markeredgecolor="black", markersize=10,
                       label="Selected monitoring node"),
                _boundary_legend()]
    ax.legend(handles=handles, fontsize=8, loc="upper left", frameon=True,
              framealpha=0.92, edgecolor="#d5d8dc",
              borderpad=0.7).set_zorder(30)
    _finish(fig, ax, path, meta, sources)
