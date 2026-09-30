"""Shared paths and constants for the Burn Before the Rain pipeline."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"
REF = DATA / "ref"
OUT = DATA / "processed"
OUT.mkdir(parents=True, exist_ok=True)

COUNTRY = "Bangladesh"
LAST_YEAR = 2026
MODIS_YEARS = range(2000, LAST_YEAR + 1)
VIIRS_YEARS = range(2012, LAST_YEAR + 1)
OVERLAP_YEARS = range(2012, LAST_YEAR + 1)


def data_end():
    """Last day with data (written by download_recent); months after it are 'no data', not 'no fire'."""
    import pandas as pd
    f = RAW / "data_end.txt"
    return pd.Timestamp(f.read_text().strip()) if f.exists() else pd.Timestamp(f"{LAST_YEAR}-12-31")

# Harmonization grid: ~1.1 km, close to a MODIS pixel.
HARMONIZE_CELL_DEG = 0.01

# Study area for the inspection list: the Chittagong hill districts.
HILL_DISTRICTS = ["Rangamati", "Bandarban", "Khagrachhari", "Chittagong", "Cox's Bazar"]
# Model / watch-list grid: ~2.2 km cells.
RISK_CELL_DEG = 0.02
# Pre-monsoon burning season (months) and the monsoon it precedes.
BURN_SEASON = [2, 3, 4, 5]
# "Heavy burning": a ~2 km cell with fire on at least this many days in one season (~10% of cells/year).
HEAVY_BURN_DAYS = 3

# Terrain (AWS Terrarium elevation tiles, no auth).
TERRAIN_ZOOM = 12  # ~35 m pixels; zoom 10 (~140 m) flattened hill slopes
TERRAIN_BBOX = (20.7, 91.3, 23.8, 92.7)  # lat_min, lon_min, lat_max, lon_max
STEEP_SLOPE_DEG = 15.0


# Documented MODIS Aqua outages (start, end inclusive, source). Days inside are removed from BOTH sensors
# before calibration/validation, so each month compares the same days. The Aqua-vs-Terra fire-day check in
# harmonize.py is kept as a cross-check for undocumented gaps.
AQUA_OUTAGES = [
    ("2022-03-31", "2022-04-17", "Aqua safe mode, MODIS Characterization Support Team event record"),
]
