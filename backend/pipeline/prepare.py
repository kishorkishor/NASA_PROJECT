"""Step 1: clean raw FIRMS CSVs into one table and tag every point with its district/upazila."""
import json

import numpy as np
import pandas as pd
import shapely
from shapely.geometry import shape

from backend import config as C

VIIRS_CONF = {"l": 20, "n": 60, "h": 90}


def load_raw() -> pd.DataFrame:
    frames = []
    for sensor, prefix, years in [("MODIS", "modis", C.MODIS_YEARS), ("VIIRS", "viirs-snpp", C.VIIRS_YEARS)]:
        for y in years:
            f = C.RAW / f"{prefix}_{y}_{C.COUNTRY}.csv"
            if not f.exists() or f.stat().st_size == 0:
                continue
            d = pd.read_csv(f)
            if d.empty:
                continue
            d["sensor"] = sensor
            frames.append(d)
    df = pd.concat(frames, ignore_index=True)
    conf = df["confidence"].astype(str)
    df["conf"] = np.where(conf.isin(VIIRS_CONF), conf.map(VIIRS_CONF), pd.to_numeric(conf, errors="coerce"))
    df["date"] = pd.to_datetime(df["acq_date"])
    out = pd.DataFrame({
        "sensor": df["sensor"],
        "satellite": df["satellite"].replace({"N": "S-NPP"}),
        "date": df["date"],
        # Overpass time (UTC, HHMM). Kept to check whether MODIS Aqua's orbit drift changed its sampling time.
        "utc_hhmm": pd.to_numeric(df["acq_time"], errors="coerce").astype("Int64"),
        "year": df["date"].dt.year,
        "month": df["date"].dt.month,
        "lat": df["latitude"],
        "lon": df["longitude"],
        "frp": df["frp"],
        "conf": df["conf"],
        "daynight": df["daynight"],
        "type": df["type"],
    })
    return out


def load_polygons(level: str):
    g = json.loads((C.REF / f"bgd_{level}.geojson").read_text())
    names = [f["properties"]["shapeName"] for f in g["features"]]
    geoms = [shape(f["geometry"]) for f in g["features"]]
    return names, geoms


def tag_admin(lat: np.ndarray, lon: np.ndarray, level: str) -> np.ndarray:
    """Return the admin-area name each point falls in (None if outside all polygons)."""
    names, geoms = load_polygons(level)
    tree = shapely.STRtree(geoms)
    pts = shapely.points(lon, lat)
    pt_idx, poly_idx = tree.query(pts, predicate="intersects")
    result = np.full(len(pts), None, dtype=object)
    result[pt_idx] = np.asarray(names, dtype=object)[poly_idx]
    return result


def load_settlements() -> pd.DataFrame:
    d = json.loads((C.REF / "settlements_osm.json").read_text(encoding="utf-8"))
    rows = [{
        "name": e["tags"].get("name:en") or e["tags"].get("name") or "(unnamed)",
        "place": e["tags"].get("place"),
        "lat": e["lat"],
        "lon": e["lon"],
    } for e in d["elements"]]
    s = pd.DataFrame(rows)
    s["district"] = tag_admin(s.lat.values, s.lon.values, "adm2")
    return s[s.district.notna()].reset_index(drop=True)


def run():
    det = load_raw()
    det["district"] = tag_admin(det.lat.values, det.lon.values, "adm2")
    det["upazila"] = tag_admin(det.lat.values, det.lon.values, "adm3")
    det.to_parquet(C.OUT / "detections.parquet", index=False)
    s = load_settlements()
    s.to_parquet(C.OUT / "settlements.parquet", index=False)
    print(f"detections: {len(det):,} ({det.sensor.value_counts().to_dict()}), "
          f"outside BGD polygons: {det.district.isna().sum():,}")
    print(f"settlements: {len(s):,} ({s.place.value_counts().to_dict()})")


if __name__ == "__main__":
    run()
