"""Step 0b: fetch the years the yearly archive files don't have yet (2025 -> latest) from the FIRMS API.

Uses the science-quality "SP" products (same processing as the archive), 5 days per request.
Needs a free MAP_KEY: set env var FIRMS_MAP_KEY, or put FIRMS_MAP_KEY=... in a .env file at the project root.
Points are clipped to Bangladesh so the files match the yearly country archives.
"""
import io
import os
import urllib.request
from datetime import date, timedelta

import pandas as pd

from backend import config as C
from backend.pipeline.prepare import tag_admin

API = "https://firms.modaps.eosdis.nasa.gov/api"
BBOX = "88.0,20.6,92.7,26.7"
SOURCES = {"modis": "MODIS_SP", "viirs-snpp": "VIIRS_SNPP_SP"}
DAYS_PER_CALL = 5


def map_key() -> str:
    key = os.environ.get("FIRMS_MAP_KEY")
    env = C.ROOT / ".env"
    if not key and env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("FIRMS_MAP_KEY="):
                key = line.split("=", 1)[1].strip()
    if not key:
        raise SystemExit("No FIRMS MAP_KEY: set FIRMS_MAP_KEY or add it to .env")
    return key


def get(url: str) -> str:
    return urllib.request.urlopen(url, timeout=120).read().decode()


def available_until(key: str) -> dict:
    avail = pd.read_csv(io.StringIO(get(f"{API}/data_availability/csv/{key}/ALL")))
    return {r.data_id: date.fromisoformat(r.max_date) for r in avail.itertuples()}


def fetch(key: str, source: str, start: date, end: date) -> pd.DataFrame:
    frames, d = [], start
    while d <= end:
        n = min(DAYS_PER_CALL, (end - d).days + 1)
        text = get(f"{API}/area/csv/{key}/{source}/{BBOX}/{n}/{d.isoformat()}")
        if not text.startswith("latitude"):
            raise RuntimeError(f"{source} {d}: {text[:200]}")
        chunk = pd.read_csv(io.StringIO(text))
        if len(chunk):  # empty chunks have object dtypes and would poison the concat
            frames.append(chunk)
        d += timedelta(days=n)
    df = pd.concat(frames, ignore_index=True).drop_duplicates()
    df[["latitude", "longitude"]] = df[["latitude", "longitude"]].astype(float)
    df = df[df.acq_date.between(start.isoformat(), end.isoformat())]
    return df[tag_admin(df.latitude.values, df.longitude.values, "adm2") != None]  # noqa: E711


def run(first_year: int = 2025):
    try:
        key = map_key()
    except SystemExit as e:
        print(f"skipping recent-years download: {e}")
        return
    until = available_until(key)
    stamp = C.RAW / "data_end.txt"
    have_until = date.fromisoformat(stamp.read_text().strip()) if stamp.exists() else date.min
    for prefix, source in SOURCES.items():
        last = until[source]
        for y in range(first_year, last.year + 1):
            start, end = date(y, 1, 1), min(date(y, 12, 31), last)
            path = C.RAW / f"{prefix}_{y}_{C.COUNTRY}.csv"
            if path.exists() and (y < have_until.year or end <= have_until):
                continue  # already complete for this year
            df = fetch(key, source, start, end)
            df.to_csv(path, index=False)
            print(f"ok   {path.name}: {len(df):,} points ({start} -> {end})")
    (C.RAW / "data_end.txt").write_text(min(until[s] for s in SOURCES.values()).isoformat())


if __name__ == "__main__":
    run()
