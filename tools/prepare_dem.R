library(sf)
library(terra)

# ------------------------------------------------------------
# Paths
# ------------------------------------------------------------
setwd("~/Desktop/wdn_sensor_ontology")
project_dir <- path.expand("~/Desktop/wdn_sensor_ontology")
inputs_dir  <- file.path(project_dir, "inputs")

campus_path <- file.path(inputs_dir, "campus_boundary.gpkg")
dem_path    <- file.path(inputs_dir, "FABDEM_DTM_30m.tif")
out_path    <- file.path(inputs_dir, "dem.tif")

# ------------------------------------------------------------
# 1. Read campus boundary
# ------------------------------------------------------------

campus <- st_read(campus_path, quiet = TRUE)

print(campus)

# If the GPKG contains multiple layers, check with:
# st_layers(campus_path)

# ------------------------------------------------------------
# 2. Reproject campus to UTM 43N
#    IIT Bombay is approximately in UTM Zone 43N
# ------------------------------------------------------------

campus_utm <- st_transform(campus, 32643)

# ------------------------------------------------------------
# 3. Create 300 m buffer
# ------------------------------------------------------------

campus_buffer <- st_buffer(campus_utm, dist = 300)

# Dissolve into a single polygon
campus_buffer <- st_union(campus_buffer)

# ------------------------------------------------------------
# 4. Read DEM
# ------------------------------------------------------------

dem <- rast(dem_path)

print(dem)

cat("\nDEM CRS:\n")
print(crs(dem))

# ------------------------------------------------------------
# 5. Transform buffer to DEM CRS
# ------------------------------------------------------------

campus_buffer_dem <- st_transform(
  st_as_sf(campus_buffer),
  crs(dem)
)

# Convert sf → terra vector
buffer_vect <- vect(campus_buffer_dem)

# ------------------------------------------------------------
# 6. Crop DEM to buffered campus extent
# ------------------------------------------------------------

dem_crop <- crop(dem, buffer_vect)

# ------------------------------------------------------------
# 7. Mask DEM to exact buffered boundary
# ------------------------------------------------------------

dem_clip <- mask(
  dem_crop,
  buffer_vect
)

# ------------------------------------------------------------
# 8. Save as inputs/dem.tif
# ------------------------------------------------------------

writeRaster(
  dem_clip,
  out_path,
  overwrite = TRUE,
  wopt = list(
    gdal = c("COMPRESS=DEFLATE", "TILED=YES"),
    datatype = "FLT4S",
    NAflag = -9999
  )
)

# ------------------------------------------------------------
# 9. Verification
# ------------------------------------------------------------

cat("\nSaved DEM:\n")
cat(out_path, "\n")

print(dem_clip)

cat("\nDEM extent:\n")
print(ext(dem_clip))

cat("\nDEM CRS:\n")
print(crs(dem_clip))

cat("\nElevation range:\n")
print(global(dem_clip, c("min", "max"), na.rm = TRUE))