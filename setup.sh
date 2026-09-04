#!/usr/bin/env bash
# =============================================================================
# setup.sh - lay out the WDN project, build a virtualenv, install dependencies
#
# Run it from the project root (the folder that contains `files/`):
#     bash setup.sh
#
# Produces:
#     code/        the pipeline (wdnkit package + numbered scripts + config)
#     inputs/      your DWG, DEM and boundary
#     outputs/     everything the pipeline writes
#     .venv/       the virtual environment
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33m  ! %s\033[0m\n' "$*"; }
ok()   { printf '\033[32m  ✓ %s\033[0m\n' "$*"; }

# ---------------------------------------------------------------- 1. GDAL ---
say "Checking system GDAL"
if command -v ogr2ogr >/dev/null 2>&1; then
    ok "ogr2ogr $(ogr2ogr --version 2>/dev/null | head -1)"
else
    warn "ogr2ogr not found - the DWG cannot be read without it."
    if command -v apt-get >/dev/null 2>&1; then
        echo "      sudo apt-get update && sudo apt-get install -y gdal-bin"
    elif command -v brew >/dev/null 2>&1; then
        echo "      brew install gdal"
    fi
    read -r -p "  Install now with apt? [y/N] " a
    if [[ "${a:-N}" =~ ^[Yy]$ ]]; then
        sudo apt-get update && sudo apt-get install -y gdal-bin
        ok "installed"
    else
        warn "continuing - stage 1 will fail until GDAL is available"
    fi
fi

# ------------------------------------------------------------ 2. layout -----
say "Creating project layout"
mkdir -p code/wdnkit code/scripts inputs outputs
ok "code/ inputs/ outputs/"

SRC=""
# extractors mangle the folder name (files_, files-1, files (1)/), so match loosely
shopt -s nullglob
for cand in files files_ files* code_src src; do
    if [[ -d "$cand" ]] && compgen -G "$cand/*.py" >/dev/null; then SRC="$cand"; break; fi
done
if [[ -z "$SRC" ]]; then
    # last resort: any directory one level down that holds the stage scripts
    for cand in */; do
        if compgen -G "${cand}0[1-9]_*.py" >/dev/null; then SRC="${cand%/}"; break; fi
    done
fi
shopt -u nullglob

if [[ -z "$SRC" ]]; then
    warn "no files/ directory found - assuming the code is already in place"
else
    say "Sorting $SRC into code/"
    for f in "$SRC"/*.py; do
        b="$(basename "$f")"
        case "$b" in
            0[1-9]_*.py)  cp "$f" code/scripts/ ;;
            run_all.py)   cp "$f" code/ ;;
            *)            cp "$f" code/wdnkit/ ;;
        esac
    done
    [[ -f "$SRC/config.example.yml" ]] && cp "$SRC/config.example.yml" code/
    [[ -f "$SRC/README.md" ]]          && cp "$SRC/README.md" code/
    for t in "$SRC"/*.ttl; do [[ -e "$t" ]] && cp "$t" code/; done
    ok "$(ls code/wdnkit/*.py | wc -l) modules, $(ls code/scripts/*.py | wc -l) stage scripts"
fi

# a TBox left beside setup.sh rather than inside the code folder
shopt -s nullglob
for t in *.ttl; do
    [[ -f "code/$(basename "$t")" ]] || { cp "$t" code/; ok "collected $(basename "$t")"; }
done
shopt -u nullglob

# the zip flattens the package, so the marker file has to be recreated
if [[ ! -f code/wdnkit/__init__.py ]]; then
    cat > code/wdnkit/__init__.py <<'PY'
"""wdnkit - configurable WDN modelling, sensor placement and ontology pipeline."""
__version__ = "1.0.0"
PY
    ok "created code/wdnkit/__init__.py"
fi

# ------------------------------------------------------- 3. move the DWG ----
say "Collecting input data"
shopt -s nullglob nocaseglob
for d in *.dwg *.dxf; do
    [[ -e "inputs/$d" ]] || { mv "$d" inputs/; ok "moved $d -> inputs/"; }
done
shopt -u nocaseglob

CAD=""
for c in inputs/*.dwg inputs/*.dxf; do CAD="$c"; break; done
shopt -u nullglob
[[ -n "$CAD" ]] && ok "CAD: $CAD" || warn "no .dwg/.dxf in inputs/"

# ------------------------------------------------------------ 4. venv -------
say "Building the virtual environment"
if ! python3 -c "import venv" >/dev/null 2>&1; then
    warn "python3-venv missing"
    echo "      sudo apt-get install -y python3-venv"
    exit 1
fi
[[ -d .venv ]] || python3 -m venv .venv
ok "$(.venv/bin/python --version)"

cat > requirements.txt <<'REQ'
# pinned to a combination verified end-to-end on this pipeline
geopandas==1.1.4
shapely==2.1.2
pyogrio==0.13.0
pyproj==3.7.2
rasterio==1.5.1
scipy==1.18.1
scikit-learn==1.9.0
pandas==3.0.5
numpy==2.5.2
matplotlib==3.11.1
PyYAML==6.0.3
wntr==1.5.0
rdflib==7.6.0
REQ
ok "requirements.txt"

say "Installing dependencies (a few minutes)"
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt
ok "installed"

say "Verifying"
.venv/bin/python - <<'PY'
import importlib.util, sys
mods = ["geopandas", "shapely", "rasterio", "scipy", "sklearn",
        "yaml", "wntr", "rdflib", "matplotlib"]
bad = [m for m in mods if not importlib.util.find_spec(m)]
print("  missing:", bad) if bad else print("  ✓ all imports resolve")
sys.path.insert(0, "code")
import wdnkit
from wdnkit import cadingest, topology, hydraulics, demand, terrain, placement, ontology, viz
print("  ✓ wdnkit", wdnkit.__version__, "imports cleanly")
import wntr
wn = wntr.network.WaterNetworkModel()
wn.add_reservoir("R", base_head=50.0)
wn.add_junction("J", base_demand=0.001, elevation=10.0)
wn.add_pipe("P", "R", "J", length=100.0, diameter=0.1, roughness=100.0)
r = wntr.sim.EpanetSimulator(wn).run_sim()
print(f"  ✓ EPANET engine solves (test pressure {float(r.node['pressure'].iloc[0]['J']):.1f} m)")
PY

# ------------------------------------------------------------ 5. config -----
say "Writing code/config.yml"
if [[ -f code/config.yml ]]; then
    warn "code/config.yml exists - leaving it alone"
else
    CADREL="${CAD:-inputs/YOUR_DRAWING.dwg}"
    TBOX="null"
    [[ -f code/wdn_sensor_ontology.ttl ]] && TBOX="code/wdn_sensor_ontology.ttl"
    cat > code/config.yml <<YML
# Paths are relative to the PROJECT ROOT - run the pipeline from there:
#   .venv/bin/python code/run_all.py -c code/config.yml

project:
  name: iitb
  crs: EPSG:32643            # UTM 43N covers Mumbai
  output_dir: outputs

cad:
  path: "${CADREL}"
  pipe_layers: auto
  annotation_layers: auto
  extent_filter: null
  keep: largest_cluster

georeference:
  # REPLACE THESE. Pick two points you can identify in both the drawing and
  # QGIS (a road junction, a building corner), read the CAD x/y and the UTM
  # easting/northing, and put them here.
  mode: control_points
  allow_rotation: true
  allow_scale: true
  control_points:
    - cad:   [0.0, 0.0]
      world: [0.0, 0.0]
    - cad:   [0.0, 0.0]
      world: [0.0, 0.0]

boundary:
  path: inputs/campus_boundary.gpkg
  layer: null
  buffer_m: 0
  clip_network: false

dem:
  path: inputs/dem.tif
  band: 1
  nodata_fill: idw
  smooth_window_m: 25        # damps 30 m DEM noise between close junctions
  vertical_offset_m: 0

topology:
  snap_tolerance_m: 0.5
  prune_dangles_below_m: 7.0
  bridge_islands: true
  max_bridge_gap_m: 150.0
  min_segment_length_m: 0.5

demand:
  population: 13429
  lpcd: 135
  peak_factor: 2.5
  allocation: voronoi
  unaccounted_for_water: 0.0

hydraulics:
  headloss: H-W
  default_diameter_mm: 100
  diameter_ladder_mm: [40, 50, 75, 100, 150, 200, 250, 300, 350, 400]
  max_velocity_ms: 1.5
  service_diameter_cap_mm: 50
  max_label_distance_m: 60
  roughness_by_material:
    CI: 100
    GI: 120
    DI: 130
    PVC: 145
    HDPE: 145
    MS: 120
    UNK: 110
  source:
    mode: auto_largest_north
    head_mode: min_residual
    min_residual_m: 15

placement:
  n_clusters: 5
  random_state: 42
  min_per_cluster: 5
  pressure_low_percentile: 20
  pressure_high_percentile: 80
  topology_classes: [junction, dead_end]

ontology:
  emit: true
  namespace: "https://w3id.org/iitb/wdn#"
  tbox_path: ${TBOX}
  sensor_suite: [pH, TDS, ORP, DO, Temperature]
YML
    ok "code/config.yml"
fi

# ------------------------------------------------------------ 6. status ----
say "Input check"
MISSING=0
[[ -n "$CAD" ]]                        && ok "CAD drawing"        || { warn "CAD drawing"; MISSING=1; }
[[ -f inputs/dem.tif ]]                && ok "DEM"                || { warn "DEM  -> inputs/dem.tif";                MISSING=1; }
[[ -f inputs/campus_boundary.gpkg ]]   && ok "campus boundary"    || { warn "boundary -> inputs/campus_boundary.gpkg"; MISSING=1; }

cat <<'EOF'

------------------------------------------------------------------------------
Next
------------------------------------------------------------------------------
  source .venv/bin/activate
  python code/run_all.py -c code/config.yml

Run from the project root, not from code/.
EOF

if [[ "$MISSING" -eq 1 ]]; then
cat <<'EOF'

Still needed before stage 2 will run:

  DEM      Copernicus GLO-30 (dataspace.copernicus.eu) or Bhuvan CartoDEM.
           Any CRS - it is reprojected on the fly. Clip to the campus + 300 m.
           Save as inputs/dem.tif

  Boundary Digitise the campus edge in QGIS over satellite imagery
           (~5 minutes). Save as inputs/campus_boundary.gpkg in EPSG:32643.
           Do this first: the same session gives you the control points.

Control points: with the boundary open in QGIS, open the DWG as a separate
layer, find two features visible in both, and read off the coordinate pairs.
Two is the minimum; four spread across the site is better.
EOF
fi
echo
