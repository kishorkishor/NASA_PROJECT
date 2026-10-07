"""Live fire watch: VIIRS S-NPP fire detections of the last 7 days over Bangladesh and its hill districts.

    python -m backend.pipeline.live                      # -> data/live/live.json
    python -m backend.pipeline.live --out PATH           # somewhere else
    python -m backend.pipeline.live --csv FILE           # from a saved FIRMS CSV (offline, tests)

No key needed: by default it reads NASA FIRMS' public 7-day file for South Asia (about 0.5 MB, refreshed
several times a day). With FIRMS_MAP_KEY set (env var or .env) it asks the FIRMS area API for Bangladesh only.

Each detection inside the hill grid is placed in its ~2 km square. A square is marked "check" when it is steep
(average slope >= 15 deg) with at least 200 people living in it, or when it is on the 2026 or the 2027
"inspect first" list. "Normal" is the average number of VIIRS S-NPP detections in the hill grid over the same
7 days of the year, 2012-2025, from the science-quality archive. Near-real-time files have no fire-type field,
so detections within ~1 km of a spot the archive flags as a static heat source (FIRMS type 2 or 3: gas flares,
factories, offshore) are set aside and counted separately.
"""
import argparse
import io
import json
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from backend import config as C
from backend.pipeline.download_recent import BBOX, map_key
from backend.pipeline.prepare import tag_admin
from backend.pipeline.terrain import cell_id

PUBLIC_7D = ("https://firms.modaps.eosdis.nasa.gov/data/active_fire/suomi-npp-viirs-c2/csv/"
             "SUOMI_VIIRS_C2_South_Asia_7d.csv")
API_7D = "https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/VIIRS_SNPP_NRT/" + BBOX + "/7"
DAYS = 7
PEOPLE_MIN = 200
NORMAL_YEARS = range(2012, 2026)
OUT = C.DATA / "live" / "live.json"
LON0, LAT0, LON1, LAT1 = (float(v) for v in BBOX.split(","))


def read(text: str) -> tuple[pd.DataFrame, str | None]:
    """FIRMS CSV -> (detections inside the Bangladesh box with acq_time as 'HHMM' UTC, last date in the file)."""
    full = pd.read_csv(io.StringIO(text), dtype={"acq_time": str})
    last = str(full.acq_date.max()) if len(full) else None
    d = full[full.latitude.between(LAT0, LAT1) & full.longitude.between(LON0, LON1)].copy()
    d["acq_time"] = d.acq_time.str.zfill(4)
    return d.reset_index(drop=True), last


def fetch(key: str | None = None) -> tuple[pd.DataFrame, str | None]:
    url = API_7D.format(key=key) if key else PUBLIC_7D
    text = urllib.request.urlopen(url, timeout=120).read().decode()
    if not text.startswith("latitude"):
        raise RuntimeError(f"FIRMS did not return a CSV: {text[:200]}")
    return read(text)


def same_day(d: date, year: int) -> date:
    try:
        return d.replace(year=year)
    except ValueError:  # 29 February in a non-leap year
        return d.replace(year=year, day=28)


def fine_cell(lat, lon):
    return (np.floor(np.asarray(lat, float) / C.HARMONIZE_CELL_DEG).astype(int),
            np.floor(np.asarray(lon, float) / C.HARMONIZE_CELL_DEG).astype(int))


def static_cells() -> set:
    """~1 km cells, plus their neighbours, where the archive flagged a static heat source (FIRMS type 2 or 3)."""
    d = pd.read_parquet(C.OUT / "detections.parquet", columns=["type", "lat", "lon"])
    d = d[d.type.isin([2, 3])]
    base = set(zip(*fine_cell(d.lat.values, d.lon.values)))
    return {(int(a) + i, int(b) + j) for a, b in base for i in (-1, 0, 1) for j in (-1, 0, 1)}


def is_static(lat, lon, static: set) -> np.ndarray:
    return np.array([k in static for k in zip(*fine_cell(lat, lon))], dtype=bool)


def normal(end: date, grid: pd.DataFrame, static: set) -> float:
    """Average hill-grid VIIRS S-NPP detections in the 7 days ending on the same day of the year, 2012-2025."""
    d = pd.read_parquet(C.OUT / "detections.parquet", columns=["sensor", "type", "date", "lat", "lon"])
    d = d[(d.sensor == "VIIRS") & (d.type == 0)]
    d = d[~is_static(d.lat.values, d.lon.values, static)]
    ci, cj = cell_id(d.lat.values, d.lon.values)
    inside = pd.MultiIndex.from_arrays([ci, cj]).isin(pd.MultiIndex.from_frame(grid[["ci", "cj"]]))
    days = d.date[inside].dt.normalize()
    counts = []
    for y in NORMAL_YEARS:
        e = pd.Timestamp(same_day(end, y))
        counts.append(int(days.between(e - pd.Timedelta(days=DAYS - 1), e).sum()))
    return float(np.mean(counts))


def lists() -> dict:
    """(ci, cj) -> seasons whose 'inspect first' list holds the square, e.g. ['2027']."""
    w = pd.read_parquet(C.OUT / "watchlist.parquet", columns=["season", "priority", "ci", "cj"])
    out = {}
    for r in w[w.priority == "inspect first"].itertuples():
        out.setdefault((int(r.ci), int(r.cj)), []).append(str(r.season))
    return {k: sorted(v) for k, v in out.items()}


def nearest_place(lat: np.ndarray, lon: np.ndarray):
    s = pd.read_parquet(C.OUT / "settlements.parquet", columns=["name", "lat", "lon"])
    s = s[s.name != "(unnamed)"]
    kx = 111.32 * np.cos(np.radians(22.5))
    dist = np.hypot((lat[:, None] - s.lat.values[None]) * 110.57, (lon[:, None] - s.lon.values[None]) * kx)
    i = dist.argmin(axis=1)
    return s.name.values[i], dist[np.arange(len(lat)), i]


def build(raw: pd.DataFrame, last_date: str | None, generated: datetime, source: str) -> dict:
    grid = pd.read_parquet(C.OUT / "grid.parquet",
                           columns=["ci", "cj", "lat", "lon", "slope_mean", "population", "district", "upazila"])
    to = date.fromisoformat(last_date) if last_date else generated.date()
    frm = to - timedelta(days=DAYS - 1)
    d = raw[raw.acq_date.between(frm.isoformat(), to.isoformat())].reset_index(drop=True)
    d = d.assign(district=tag_admin(d.latitude.values, d.longitude.values, "adm2") if len(d) else None)
    d = d[d.district.notna()].reset_index(drop=True)
    static = static_cells()
    known = is_static(d.latitude.values, d.longitude.values, static) if len(d) else np.zeros(0, bool)
    n_static = int(known.sum())
    d = d[~known].reset_index(drop=True)
    ci, cj = cell_id(d.latitude.values, d.longitude.values)
    d = d.assign(ci=ci, cj=cj)
    cells = grid.rename(columns={"lat": "cell_lat", "lon": "cell_lon", "district": "cell_district"})
    d = d.merge(cells, on=["ci", "cj"], how="left")
    on_list = lists()
    hills = d.slope_mean.notna().to_numpy()
    listed = [on_list.get((int(a), int(b))) for a, b in zip(d.ci, d.cj)]
    steep_people = ((d.slope_mean >= C.STEEP_SLOPE_DEG) & (d.population >= PEOPLE_MIN)).to_numpy()
    check = hills & (steep_people | np.array([x is not None for x in listed], dtype=bool))
    d = d.assign(hills=hills, check=check, listed=pd.Series(listed, index=d.index, dtype=object))
    d = d.sort_values(["acq_date", "acq_time"])

    alerts = []
    hill = d[d.hills]
    for (a, b), g in hill.groupby(["ci", "cj"], sort=False):
        alerts.append({"ci": int(a), "cj": int(b), "fires": len(g), "row": g.iloc[0], "last": g.iloc[-1]})
    if alerts:
        names, km = nearest_place(np.array([x["row"]["cell_lat"] for x in alerts]),
                                  np.array([x["row"]["cell_lon"] for x in alerts]))
        for x, name, dist in zip(alerts, names, km):
            r, lr = x.pop("row"), x.pop("last")
            x.pop("ci"), x.pop("cj")
            x.update({
                "lat": round(float(r["cell_lat"]), 3), "lon": round(float(r["cell_lon"]), 3),
                "last": f"{lr['acq_date']}T{lr['acq_time'][:2]}:{lr['acq_time'][2:]}Z",
                "place": str(name), "km": round(float(dist), 1),
                "upazila": r["upazila"], "district": r["cell_district"],
                "slope": round(float(r["slope_mean"]), 1), "people": int(round(r["population"])),
                "list": r["listed"], "check": bool(r["check"]),
            })
        alerts.sort(key=lambda x: (not x["list"], not x["check"], -x["slope"]))

    fires = [[round(float(r.latitude), 4), round(float(r.longitude), 4), r.acq_date, r.acq_time,
              round(float(r.frp), 1), 2 if r.check else 1 if r.hills else 0] for r in d.itertuples()]
    return {
        "generated": generated.strftime("%Y-%m-%dT%H:%MZ"),
        "source": source,
        "from": frm.isoformat(), "to": to.isoformat(),
        "counts": {"bangladesh": int(len(d)), "hills": int(d.hills.sum()), "check": int(d.check.sum()),
                   "static": n_static, "normal": round(normal(to, grid, static), 1)},
        "fires": fires,
        "alerts": alerts,
    }


def run(out: Path = OUT, csv: Path | None = None) -> dict:
    if csv:
        (raw, last), source = read(Path(csv).read_text()), "NASA FIRMS, VIIRS S-NPP 375 m (saved file)"
    else:
        try:
            key = map_key()
        except SystemExit:
            key = None
        raw, last = fetch(key)
        source = "NASA FIRMS, VIIRS S-NPP 375 m, near real-time" + (" (area API)" if key else " (public 7-day file)")
    data = build(raw, last, datetime.now(timezone.utc), source)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    print(f"live {data['from']} to {data['to']}: {data['counts']} -> {out}")
    return data


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--csv", type=Path, default=None)
    a = ap.parse_args()
    run(a.out, a.csv)
