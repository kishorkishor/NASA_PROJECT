"""Run the whole pipeline end to end:

    python -m backend.run_pipeline            # everything; reuses the saved Sentinel-2 results if present
    python -m backend.run_pipeline --with-s2  # also re-fetch Sentinel-2 imagery (~10-15 min, needs internet)

The FIRMS API step needs FIRMS_MAP_KEY (env var or .env) only to fetch years missing from data/raw.
"""
import sys
import time

from backend import config as C
from backend.pipeline import (download, download_recent, harmonize, history, model, prepare, s2check, terrain,
                              watchlist)

STEPS = [
    ("download FIRMS yearly archives", download.run),
    ("download recent years via FIRMS API", download_recent.run),
    ("prepare + tag districts", prepare.run),
    ("harmonize MODIS/VIIRS (+ drift, outage, holdout, bootstrap)", harmonize.run),
    ("terrain (35 m) + population grid", terrain.run),
    ("harmonized 24-season history per cell", history.run),
    ("forecast comparison + next-season forecast", model.run),
    ("inspection-priority list + stability test", watchlist.run),
]

if __name__ == "__main__":
    fetch_s2 = "--with-s2" in sys.argv or not (C.OUT / "s2_check.parquet").exists()
    STEPS.append(("Sentinel-2 before/after check" + ("" if fetch_s2 else " (saved imagery results)"),
                  lambda: s2check.run(summary_only=not fetch_s2)))
    for name, step in STEPS:
        t = time.time()
        print(f"\n=== {name} ===")
        step()
        print(f"--- {name}: {time.time() - t:.1f}s")
