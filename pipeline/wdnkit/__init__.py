"""wdnkit - configurable WDN modelling, sensor placement and ontology pipeline."""

__version__ = "1.0.1"

# ---------------------------------------------------------------------------
# PROJ / GDAL data-directory guard.
#
# An active conda environment exports PROJ_LIB, PROJ_DATA and GDAL_DATA pointing
# at conda's share directories. When the pipeline runs inside a pip venv,
# rasterio and pyproj each ship their own PROJ database, and conda's is usually
# older. The C library then loads the wrong proj.db and the first coordinate
# transform dies with:
#
#   proj.db contains DATABASE.LAYOUT.VERSION.MINOR = 2 whereas a number >= 6
#   is expected. It comes from another PROJ installation.
#   -> CRSError: The EPSG code is unknown
#
# Pointing the variables at one wheel's data does not help either: rasterio and
# pyproj can bundle different PROJ versions, so forcing pyproj's directory
# breaks rasterio instead. The only reliable fix is to remove the variables and
# let each library use the path it was compiled against.
#
# Set WDNKIT_KEEP_PROJ_ENV=1 to opt out (e.g. when you deliberately point at a
# system PROJ with extra transformation grids installed).
# ---------------------------------------------------------------------------
import os as _os


def _clear_inherited_proj_env(verbose: bool = False) -> list:
    if _os.environ.get("WDNKIT_KEEP_PROJ_ENV"):
        return []
    cleared = []
    for var in ("PROJ_LIB", "PROJ_DATA", "GDAL_DATA"):
        val = _os.environ.pop(var, None)
        if val:
            cleared.append((var, val))
    if verbose and cleared:
        for var, val in cleared:
            print(f"    [wdnkit] unset {var} (was {val})")
    return cleared


CLEARED_PROJ_ENV = _clear_inherited_proj_env(
    verbose=bool(_os.environ.get("WDNKIT_VERBOSE")))
