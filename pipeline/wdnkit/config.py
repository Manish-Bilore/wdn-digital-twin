"""
wdnkit.config
=============
Loads the YAML run configuration, resolves defaults, and provides the small
amount of introspection the pipeline needs so you do not have to hand-specify
CRS codes or attribute column names.
"""

from __future__ import annotations

import difflib
import math
import os
from dataclasses import dataclass, field
from typing import Any

import yaml

# ---------------------------------------------------------------- defaults ---
DEFAULTS: dict[str, Any] = {
    "project": {
        "name": "wdn",
        "crs": "auto",             # "auto" -> UTM zone from the data bounds
        "output_dir": "out",
    },
    "cad": {
        "path": None,
        "pipe_layers": "auto",     # "auto" -> layer-name keyword match
        "annotation_layers": "auto",
        "extent_filter": None,     # [minx, miny, maxx, maxy] in CAD units
        "keep": "largest_cluster", # "largest_cluster" | "all" | "in_boundary"
        "units_per_metre": 1.0,
    },
    "georeference": {
        # identity        - CAD coordinates are already in project CRS
        # control_points  - list of {cad: [x,y], world: [E,N]} (>=2)
        # fit_to_boundary - similarity fit of CAD extent onto the boundary
        "mode": "auto_register",
        "control_points": [],
        "allow_rotation": False,
        "allow_scale": False,
        # auto_register tuning
        "search_radius_m": 600.0,
        "coarse_step_m": 40.0,
        "rotation_range_deg": 8.0,
        "footprint_buffer_m": 35.0,
        "min_iou": 0.55,
    },
    "boundary": {
        "path": None,
        "layer": None,
        "buffer_m": 0.0,
        "clip_network": False,
    },
    "dem": {
        "path": None,
        "band": 1,
        "nodata_fill": "idw",      # "idw" | "mean" | float
        "smooth_window_m": 0.0,
        "vertical_offset_m": 0.0,
    },
    "topology": {
        "snap_tolerance_m": 0.5,
        "prune_dangles_below_m": 7.0,
        "bridge_islands": True,
        "max_bridge_gap_m": 150.0,
        "min_segment_length_m": 0.5,
    },
    "demand": {
        "population": None,         # single total for the whole area
        "lpcd": 135.0,              # CPHEEO institutional norm
        "total_lpd": None,          # overrides population * lpcd if given
        "peak_factor": 2.5,
        "allocation": "voronoi",    # "voronoi" | "uniform" | "pipe_length"
        # restrict the tessellation to ground within this distance of a main;
        # None spreads demand over the whole boundary, including unserved land
        "max_service_distance_m": None,
        "unaccounted_for_water": 0.0,   # fraction, e.g. 0.15 adds 15 %
    },
    "hydraulics": {
        "headloss": "H-W",
        "default_diameter_mm": 100.0,
        "diameter_ladder_mm": [40, 50, 75, 100, 150, 200, 250, 300, 350, 400],
        "max_velocity_ms": 1.5,
        "service_diameter_cap_mm": 50.0,
        "roughness_by_material": {
            "CI": 100.0, "GI": 120.0, "DI": 130.0,
            "PVC": 145.0, "HDPE": 145.0, "MS": 120.0, "UNK": 110.0,
        },
        "source": {
            "mode": "auto_largest_north",   # "auto_largest_north" | "coordinates" | "layer"
            "coordinates": None,            # [E, N]
            "path": None,
            "head_mode": "min_residual",    # "min_residual" | "fixed"
            "min_residual_m": 15.0,
            "fixed_head_m": None,
        },
    },
    "placement": {
        "n_clusters": 5,
        "random_state": 42,
        "min_per_cluster": 5,
        "elevation_rule": "above_cluster_mean",
        "pressure_low_percentile": 20,
        "pressure_high_percentile": 80,
        "demand_rule": "above_cluster_mean",
        "topology_classes": ["junction", "dead_end"],
    },
    "ontology": {
        "emit": True,
        "namespace": "https://w3id.org/iitb/wdn#",
        "tbox_path": None,          # optional, merged into the full graph
        "instance_prefix": "",
        "sensor_suite": ["pH", "TDS", "ORP", "DO", "Temperature"],
    },
}

# column-name synonyms used by fuzzy matching
COLUMN_SYNONYMS = {
    "diameter": ["diameter", "dia", "dn", "size", "diam_mm", "diameter_mm"],
    "material": ["material", "mat", "pipe_material", "matl"],
    "population": ["population", "pop", "persons", "people", "occupancy"],
    "name": ["name", "label", "id", "text"],
}


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


@dataclass
class Config:
    raw: dict = field(default_factory=dict)

    def __getitem__(self, k):
        return self.raw[k]

    def get(self, path: str, default=None):
        """Dotted lookup: cfg.get('demand.lpcd')."""
        cur = self.raw
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    @classmethod
    def load(cls, path: str) -> "Config":
        with open(path) as f:
            user = yaml.safe_load(f) or {}
        cfg = cls(_deep_merge(DEFAULTS, user))
        cfg.validate()
        return cfg

    def validate(self):
        errs = []
        if not self.get("cad.path"):
            errs.append("cad.path is required")
        elif not os.path.exists(self.get("cad.path")):
            errs.append(f"cad.path not found: {self.get('cad.path')}")

        for key in ("dem.path", "boundary.path"):
            p = self.get(key)
            if p and not os.path.exists(p):
                errs.append(f"{key} not found: {p}")

        if self.get("demand.total_lpd") is None and self.get("demand.population") is None:
            errs.append("give either demand.population or demand.total_lpd")

        mode = self.get("georeference.mode")
        if mode not in ("identity", "control_points", "fit_to_boundary",
                        "auto_register"):
            errs.append(f"unknown georeference.mode: {mode}")
        if mode == "control_points":
            cps = self.get("georeference.control_points", []) or []
            if len(cps) < 2:
                errs.append("georeference.mode=control_points needs at least 2 points")
            for i, c in enumerate(cps, 1):
                for side in ("cad", "world"):
                    v = (c or {}).get(side)
                    if not isinstance(v, (list, tuple)) or len(v) != 2:
                        errs.append(f"control point {i}: '{side}' must be [x, y]")
                        continue
                    for j, n in enumerate(v):
                        if isinstance(n, str) or not isinstance(n, (int, float)):
                            errs.append(
                                f"control point {i} '{side}' value {j+1} is {n!r}, "
                                "not a number - replace the placeholder with a real "
                                "coordinate read off the drawing / QGIS")
        if mode in ("fit_to_boundary", "auto_register") and not self.get("boundary.path"):
            errs.append(f"georeference.mode={mode} needs boundary.path")

        if errs:
            raise ValueError("configuration problems:\n  - " + "\n  - ".join(errs))

    # ---------------------------------------------------------------- CRS ---
    def resolve_crs(self, lon: float | None = None, lat: float | None = None) -> str:
        """Return the project CRS, deriving a UTM zone when set to 'auto'."""
        crs = self.get("project.crs", "auto")
        if crs and crs != "auto":
            return str(crs)
        if lon is None or lat is None:
            raise ValueError("project.crs is 'auto' but no reference point was given; "
                             "set project.crs explicitly (e.g. EPSG:32643)")
        zone = int(math.floor((lon + 180.0) / 6.0) + 1)
        epsg = (32600 if lat >= 0 else 32700) + zone
        return f"EPSG:{epsg}"

    @property
    def outdir(self) -> str:
        d = self.get("project.output_dir", "out")
        os.makedirs(d, exist_ok=True)
        return d


def match_column(columns, want: str, extra=()) -> str | None:
    """Fuzzy-match an attribute column against known synonyms."""
    cands = list(COLUMN_SYNONYMS.get(want, [])) + list(extra)
    low = {str(c).lower().strip(): c for c in columns}
    for c in cands:
        if c in low:
            return low[c]
    for c in cands:
        hit = difflib.get_close_matches(c, list(low), n=1, cutoff=0.82)
        if hit:
            return low[hit[0]]
    return None
