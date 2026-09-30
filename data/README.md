# Data

Everything the demo needs is already here, so the app runs without downloading data (only the
background map tiles come from OpenStreetMap). `python -m backend.run_pipeline` rebuilds `processed/`
from `raw/` and `ref/`, except the Sentinel-2 results (`s2_check.parquet`, `s2/*.png`), which are reused
unless you pass `--with-s2`. The terrain step fetches elevation tiles (no key needed), and FIRMS is
contacted only if `FIRMS_MAP_KEY` is set.

## raw/: NASA FIRMS active fire detections, Bangladesh

| Files | Product | How we got it |
|---|---|---|
| `modis_2000..2024_Bangladesh.csv` | MODIS active fires, MCD14ML (Terra and Aqua), standard processing; the files' version field is Collection 6 up to 2022 and 6.1 from 2023 | FIRMS yearly country archive, `firms.modaps.eosdis.nasa.gov/data/country/` (no key) |
| `viirs-snpp_2012..2024_Bangladesh.csv` | VIIRS S-NPP 375 m active fires (VNP14IMGTML), standard processing | same |
| `modis_2025/2026_…`, `viirs-snpp_2025/2026_…` | same products (`MODIS_SP`, `VIIRS_SNPP_SP`) | FIRMS area API, clipped to Bangladesh (needs a free MAP_KEY) |
| `data_end.txt` | last date covered: 2026-06-30 | |

We acknowledge the use of data from NASA's Fire Information for Resource Management System (FIRMS)
(https://www.earthdata.nasa.gov/firms), part of NASA's Earth Science Data and Information System (ESDIS).
MODIS: MCD14ML, doi:10.5067/FIRMS/MODIS/MCD14ML. VIIRS: VNP14IMGTML (see the FIRMS citation guidance at
https://www.earthdata.nasa.gov/data/tools/firms/faq).

## ref/: reference layers

| File | Source | License |
|---|---|---|
| `bgd_adm2.geojson`, `bgd_adm3.geojson` | geoBoundaries (gbOpen), Bangladesh districts and upazilas. Runfola et al. (2020), *geoBoundaries: A global database of political administrative boundaries*, PLoS ONE 15(4): e0231866 | CC BY 4.0 |
| `bgd_pop_2020_1km.tif` | WorldPop, Bangladesh population 2020, 1 km, UN-adjusted (www.worldpop.org) | CC BY 4.0 |
| `settlements_osm.json` | OpenStreetMap `place` nodes (village, hamlet, town, city, suburb), extracted with the Overpass API | © OpenStreetMap contributors, ODbL 1.0 |

Elevation comes from AWS Terrain Tiles (Terrarium format, SRTM-based; registry.opendata.aws/terrain-tiles).
The tiles are downloaded on first run into `ref/terrarium/` and are not stored in the repository.

## processed/: pipeline outputs used by the app

| File | What it is |
|---|---|
| `detections.parquet` | all fire points, cleaned, with district and upazila |
| `calendar.parquet`, `calendar_annual.parquet` | monthly and yearly Burning Activity Calendar (raw MODIS, naive join, harmonized with 95% interval) |
| `harmonization_report.json` | scaling factors, drift and outage checks, holdout and bootstrap results |
| `grid.parquet` | 4,351 hill cells (0.02°) with slope, elevation and population |
| `cell_history.parquet`, `cell_summary.parquet`, `history_report.json` | harmonized 24-season history per cell |
| `forecast.parquet`, `hill_season_firedays.parquet`, `model_report.json` | forecast method comparison and 2027 forecast |
| `watchlist.parquet`, `watchlist_summary.json` | inspection-priority list, stability test |
| `s2_check.parquet`, `s2_report.json`, `s2/*.png` | Sentinel-2 before/after burn-scar check and image thumbnails |
| `settlements.parquet` | place names tagged with district (derived from OpenStreetMap, © OpenStreetMap contributors, ODbL 1.0) |
| `calibration_months.parquet` | monthly MODIS/VIIRS counts with the documented Aqua outage days removed (calibration and backtest input) |

The Sentinel-2 thumbnails and burn-scar results, and the coded copies in `tools/blind_review/`, contain
modified Copernicus Sentinel data (2025–2026), accessed through Earth Search by Element 84 (AWS Open Data).
The key that links the blind-review codes to groups (`processed/s2_blind_key.csv`) is kept out of the
repository.
