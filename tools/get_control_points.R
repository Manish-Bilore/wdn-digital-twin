#!/usr/bin/env Rscript
# =============================================================================
# get_control_points.R
#
# Produces the `georeference.control_points` block for code/config.yml.
#
# Because the pipeline runs with allow_scale = FALSE and allow_rotation = FALSE,
# only a translation is being solved. That has a useful consequence: the
# straight-line distance and bearing between your two CAD landmarks must survive
# unchanged into the world pair. This script checks exactly that, so a mis-click
# is caught here rather than showing up 20 minutes later as a 106 m elevation.
#
# Two ways to supply the world coordinates:
#   MODE = "interactive"  click the two points on satellite imagery
#   MODE = "manual"       paste lon/lat (or easting/northing) you read elsewhere
#
# Run from the PROJECT ROOT:
#   Rscript code/get_control_points.R
# =============================================================================
setwd("~/Desktop/wdn_sensor_ontology")
# ------------------------------------------------------------------ config --
MODE        <- "interactive"          # "interactive" | "manual"
TARGET_CRS  <- 32643                  # UTM 43N
CAD_GPKG    <- "inputs/Digital Water_cad.gpkg"
CAD_LAYER   <- "Water Supply Line"
CONFIG_PATH <- "code/config.yml"      # only read, never overwritten
OUT_DIR     <- "outputs"

# The two landmarks, in CAD units. Defaults are the north pump house (on the
# 12" C.I. trunk) and the southernmost terminal node of the network.
CAD_POINTS <- data.frame(
  name = c("north_pump_house", "south_terminal"),
  x    = c(2435.6,             1753.8),
  y    = c(1041.0,            -389.5),
  stringsAsFactors = FALSE
)

# MANUAL mode only. Longitude/latitude in WGS84, in the SAME ORDER as
# CAD_POINTS. Leave as NA to be prompted on the console.
WORLD_LONLAT <- data.frame(
  lon = c(NA_real_, NA_real_),
  lat = c(NA_real_, NA_real_)
)
# ...or give projected coordinates directly and set USE_LONLAT <- FALSE
USE_LONLAT <- TRUE
WORLD_EN <- data.frame(
  easting  = c(NA_real_, NA_real_),
  northing = c(NA_real_, NA_real_)
)

TOL_FRACTION <- 0.02                  # 2 % length tolerance
TOL_BEARING  <- 2.0                   # degrees

# ------------------------------------------------------------------- setup --
need <- function(pkgs) {
  miss <- pkgs[!vapply(pkgs, requireNamespace, logical(1), quietly = TRUE)]
  if (length(miss)) {
    stop("missing packages: ", paste(miss, collapse = ", "),
         "\n  install.packages(c(",
         paste(sprintf('"%s"', miss), collapse = ", "), "))", call. = FALSE)
  }
}
need(c("sf"))
suppressPackageStartupMessages(library(sf))

dir.create(OUT_DIR, showWarnings = FALSE, recursive = TRUE)

say  <- function(...) cat(sprintf(...), "\n")
rule <- function() cat(strrep("-", 78), "\n")

if (nrow(CAD_POINTS) < 2) stop("need at least two CAD landmarks", call. = FALSE)

# ------------------------------------------------- CAD geometry invariants --
cad_dx  <- CAD_POINTS$x[2] - CAD_POINTS$x[1]
cad_dy  <- CAD_POINTS$y[2] - CAD_POINTS$y[1]
cad_len <- sqrt(cad_dx^2 + cad_dy^2)
cad_brg <- (atan2(cad_dx, cad_dy) * 180 / pi) %% 360

rule()
say("CAD landmarks")
for (i in seq_len(nrow(CAD_POINTS)))
  say("  %-20s [%9.1f, %9.1f]", CAD_POINTS$name[i], CAD_POINTS$x[i], CAD_POINTS$y[i])
say("  separation  %.1f m", cad_len)
say("  bearing     %.2f deg from grid north", cad_brg)
say("  tolerance   %.1f m / %.1f deg", TOL_FRACTION * cad_len, TOL_BEARING)
rule()

# ------------------------------------------------------- collect the world --
get_world <- function() {
  
  if (identical(MODE, "manual")) {
    if (!USE_LONLAT) {
      en <- WORLD_EN
      if (any(is.na(en))) {
        for (i in seq_len(nrow(CAD_POINTS))) {
          cat(sprintf("\n%s (CAD %.1f, %.1f)\n", CAD_POINTS$name[i],
                      CAD_POINTS$x[i], CAD_POINTS$y[i]))
          en$easting[i]  <- as.numeric(readline("  easting  : "))
          en$northing[i] <- as.numeric(readline("  northing : "))
        }
      }
      return(st_as_sf(en, coords = c("easting", "northing"), crs = TARGET_CRS))
    }
    ll <- WORLD_LONLAT
    if (any(is.na(ll))) {
      for (i in seq_len(nrow(CAD_POINTS))) {
        cat(sprintf("\n%s (CAD %.1f, %.1f)\n", CAD_POINTS$name[i],
                    CAD_POINTS$x[i], CAD_POINTS$y[i]))
        ll$lon[i] <- as.numeric(readline("  longitude : "))
        ll$lat[i] <- as.numeric(readline("  latitude  : "))
      }
    }
    pts <- st_as_sf(ll, coords = c("lon", "lat"), crs = 4326)
    return(st_transform(pts, TARGET_CRS))
  }
  
  # ---- interactive ---------------------------------------------------------
  need(c("mapview", "mapedit", "leaflet"))
  suppressPackageStartupMessages({
    library(mapview); library(mapedit); library(leaflet)
  })
  
  centre <- c(72.9155, 19.1335)   # IITB, only used to open the map somewhere
  if (file.exists("inputs/campus_boundary.gpkg")) {
    b <- st_read("inputs/campus_boundary.gpkg", quiet = TRUE)
    b <- st_transform(b, 4326)
    bb <- st_bbox(b)
    centre <- c(mean(c(bb["xmin"], bb["xmax"])), mean(c(bb["ymin"], bb["ymax"])))
  }
  
  m <- leaflet() |>
    addProviderTiles("Esri.WorldImagery", group = "Esri imagery") |>
    addProviderTiles("OpenStreetMap", group = "OSM") |>
    addLayersControl(baseGroups = c("Esri imagery", "OSM")) |>
    setView(lng = centre[1], lat = centre[2], zoom = 16)
  
  if (exists("b")) m <- m |> addPolygons(data = b, fill = FALSE,
                                         color = "#ffcc00", weight = 2)
  
  cat("\nClick the landmarks IN THIS ORDER, then press Done:\n")
  for (i in seq_len(nrow(CAD_POINTS)))
    cat(sprintf("   %d. %s\n", i, CAD_POINTS$name[i]))
  cat("\n")
  
  drawn <- editMap(m, targetLayerId = NULL)
  pts <- drawn$finished
  if (is.null(pts) || nrow(pts) < nrow(CAD_POINTS))
    stop("expected ", nrow(CAD_POINTS), " points, got ",
         if (is.null(pts)) 0 else nrow(pts), call. = FALSE)
  pts <- pts[seq_len(nrow(CAD_POINTS)), ]
  pts <- st_sf(geometry = st_geometry(pts))
  st_transform(pts, TARGET_CRS)
}

world <- get_world()
wc <- st_coordinates(world)

# ------------------------------------------------------------------- check --
w_dx  <- wc[2, 1] - wc[1, 1]
w_dy  <- wc[2, 2] - wc[1, 2]
w_len <- sqrt(w_dx^2 + w_dy^2)
w_brg <- (atan2(w_dx, w_dy) * 180 / pi) %% 360

len_err <- abs(w_len - cad_len)
brg_err <- min(abs(w_brg - cad_brg), 360 - abs(w_brg - cad_brg))

rule()
say("World pair")
for (i in seq_len(nrow(wc)))
  say("  %-20s E %11.2f  N %12.2f", CAD_POINTS$name[i], wc[i, 1], wc[i, 2])
say("  separation  %.1f m   (CAD %.1f m, error %.1f m = %.2f %%)",
    w_len, cad_len, len_err, 100 * len_err / cad_len)
say("  bearing     %.2f deg (CAD %.2f, error %.2f deg)", w_brg, cad_brg, brg_err)

ok_len <- len_err <= TOL_FRACTION * cad_len
ok_brg <- brg_err <= TOL_BEARING

if (!ok_len)
  say("  FAIL length. One of the picks is on the wrong feature, or the drawing\n        is not at 1:1 scale. Do not use these points.")
if (!ok_brg)
  say("  FAIL bearing. The drawing is rotated relative to grid north; rerun the\n        pipeline with allow_rotation: true, or re-pick.")
if (ok_len && ok_brg) say("  PASS - geometry is consistent with a pure translation")

# per-point implied offset; with rotation and scale locked these must agree
off_e <- wc[, 1] - CAD_POINTS$x
off_n <- wc[, 2] - CAD_POINTS$y
say("")
say("Implied translation from each point:")
for (i in seq_along(off_e))
  say("  %-20s dE %11.2f   dN %12.2f", CAD_POINTS$name[i], off_e[i], off_n[i])
say("  spread      dE %.2f m   dN %.2f m", diff(range(off_e)), diff(range(off_n)))
say("  mean        dE %11.2f   dN %12.2f", mean(off_e), mean(off_n))
rule()

# ------------------------------------------------------------------ output --
yml <- c(
  "georeference:",
  "  mode: control_points",
  "  allow_rotation: false",
  "  allow_scale: false",
  "  control_points:",
  unlist(lapply(seq_len(nrow(wc)), function(i) c(
    sprintf("    - cad:   [%.1f, %.1f]", CAD_POINTS$x[i], CAD_POINTS$y[i]),
    sprintf("      world: [%.2f, %.2f]", wc[i, 1], wc[i, 2])
  )))
)
snippet <- file.path(OUT_DIR, "control_points.yml")
writeLines(yml, snippet)

cat("Paste this into", CONFIG_PATH, "replacing the existing georeference block:\n\n")
cat(paste(yml, collapse = "\n"), "\n\n")
say("also written to %s", snippet)

# ---------------------------------------- preview: translated network layer --
if (file.exists(CAD_GPKG)) {
  net <- st_read(CAD_GPKG, layer = CAD_LAYER, quiet = TRUE)
  net <- net[!st_is_empty(net), ]
  net <- suppressWarnings(st_cast(net, "LINESTRING"))
  bb  <- do.call(rbind, lapply(st_geometry(net), function(g) st_bbox(g)[c(1, 2)]))
  net <- net[bb[, 1] > 1400, ]          # the clean standalone copy
  shifted <- st_geometry(net) + c(mean(off_e), mean(off_n))
  out <- st_sf(geometry = shifted, crs = TARGET_CRS)
  prev <- file.path(OUT_DIR, "georeference_preview.gpkg")
  st_write(out, prev, layer = "network_translated",
           delete_dsn = TRUE, quiet = TRUE)
  say("preview -> %s", prev)
  say("Open it in QGIS over imagery. If the mains do not follow the roads,")
  say("re-pick before running the pipeline.")
} else {
  say("note: %s not found - run stage 1 once to generate it, then rerun", CAD_GPKG)
}
rule()

