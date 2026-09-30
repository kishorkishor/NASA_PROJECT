"""Step 3: build the inspection grid (~2.2 km cells over the hill districts) with slope, elevation and population.

Elevation: AWS Terrarium tiles (SRTM-based, no auth) at zoom 12 (~35 m pixels). Zoom 10 (~140 m) was
tried first but smooths hill slopes badly (median slope at fire points 11 deg vs 16 deg at zoom 12).
Population: WorldPop 2020 1 km (UN-adjusted).
"""
import io
import math
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

import numpy as np
import pandas as pd
from PIL import Image

from backend import config as C
from backend.pipeline.prepare import tag_admin

TILE_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
VIIRS_FOOTPRINT_M = 375  # slope around a fire point is summarised over one VIIRS pixel


def lonlat_to_tile(lon, lat, z):
    n = 2 ** z
    x = (lon + 180) / 360 * n
    y = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n
    return x, y


def _tile(z, tx, ty) -> np.ndarray:
    cache = C.REF / "terrarium"
    cache.mkdir(exist_ok=True)
    f = cache / f"{z}_{tx}_{ty}.png"
    if not f.exists():
        f.write_bytes(urllib.request.urlopen(TILE_URL.format(z=z, x=tx, y=ty), timeout=60).read())
    a = np.asarray(Image.open(io.BytesIO(f.read_bytes())).convert("RGB"), dtype=np.float32)
    return a[..., 0] * 256 + a[..., 1] + a[..., 2] / 256 - 32768


def fetch_dem():
    """Mosaic Terrarium tiles over the bbox; returns elevation (m, float32) and the tile origin."""
    z = C.TERRAIN_ZOOM
    lat0, lon0, lat1, lon1 = C.TERRAIN_BBOX
    x0, y0 = (int(v) for v in lonlat_to_tile(lon0, lat1, z))
    x1, y1 = (int(v) for v in lonlat_to_tile(lon1, lat0, z))
    xs, ys = range(x0, x1 + 1), range(y0, y1 + 1)
    with ThreadPoolExecutor(16) as ex:
        tiles = list(ex.map(lambda xy: _tile(z, *xy), [(tx, ty) for ty in ys for tx in xs]))
    elev = np.empty((len(ys) * 256, len(xs) * 256), dtype=np.float32)
    for i, t in enumerate(tiles):
        r, c = divmod(i, len(xs))
        elev[r * 256:(r + 1) * 256, c * 256:(c + 1) * 256] = t
    return elev, x0, y0


def row_lat(nrows, y0, z):
    py = np.arange(nrows) + 0.5
    return np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * (y0 + py / 256) / 2 ** z))))


def col_lon(ncols, x0, z):
    return (x0 + (np.arange(ncols) + 0.5) / 256) / 2 ** z * 360 - 180


def slope_grid(elev, y0):
    """Slope in degrees (float32). Pixel size shrinks with cos(lat) in Web Mercator."""
    z = C.TERRAIN_ZOOM
    lat = row_lat(elev.shape[0], y0, z)
    px_m = (156543.03392 * np.cos(np.radians(lat)) / 2 ** z).astype(np.float32)[:, None]
    dzdy, dzdx = np.gradient(elev)
    return np.degrees(np.arctan(np.hypot(dzdx / px_m, dzdy / px_m))).astype(np.float32)


def load_population():
    im = Image.open(C.REF / "bgd_pop_2020_1km.tif")
    pop = np.asarray(im, dtype=np.float64)
    pop = np.where(pop < 0, 0, pop)
    _, _, _, lon0, lat0, _ = im.tag_v2[33922]
    res = im.tag_v2[33550][0]
    lats = lat0 - (np.arange(pop.shape[0]) + 0.5) * res
    lons = lon0 + (np.arange(pop.shape[1]) + 0.5) * res
    return pop, lats, lons


@lru_cache(maxsize=1)
def _slope_raster():
    r = np.load(C.OUT / "slope_raster.npz")
    return r["slope"].astype(np.float32), int(r["x0"]), int(r["y0"]), int(r["zoom"])


def footprint_slope(lat, lon, steep_deg: float = C.STEEP_SLOPE_DEG, footprint_m: float = VIIRS_FOOTPRINT_M):
    """Mean slope and share of steep ground inside a ~375 m (one VIIRS pixel, default) box around each point.

    A VIIRS detection only says "fire somewhere in this ~375 m pixel", so a single DEM pixel under the
    point centre would be false precision. Returns (mean_slope_deg, steep_share); NaN outside the mosaic.
    """
    slope, x0, y0, z = _slope_raster()
    n = 2 ** z
    lat, lon = np.asarray(lat, float), np.asarray(lon, float)
    px = np.floor(((lon + 180) / 360 * n - x0) * 256).astype(int)
    py = np.floor(((1 - np.arcsinh(np.tan(np.radians(lat))) / np.pi) / 2 * n - y0) * 256).astype(int)
    px_m = 156543.03392 * np.cos(np.radians(np.nanmean(lat) if len(lat) else 22.0)) / n
    h = max(1, int(round(footprint_m / px_m / 2)))
    mean_s, steep = np.full(len(lat), np.nan), np.full(len(lat), np.nan)
    ok = (px - h >= 0) & (py - h >= 0) & (px + h < slope.shape[1]) & (py + h < slope.shape[0])
    offs = np.arange(-h, h + 1)
    idx = np.flatnonzero(ok)
    for start in range(0, len(idx), 20000):  # chunked to bound memory
        i = idx[start:start + 20000]
        win = slope[py[i][:, None, None] + offs[None, :, None], px[i][:, None, None] + offs[None, None, :]]
        mean_s[i] = win.mean(axis=(1, 2))
        steep[i] = (win >= steep_deg).mean(axis=(1, 2))
    return mean_s, steep


def sample_population(lat, lon, radius_px: int = 1) -> np.ndarray:
    """People in the (2r+1)^2 block of 1 km WorldPop pixels around each point (r=1: ~3x3 km, i.e. within ~1.5 km)."""
    pop, lats, lons = load_population()
    res = abs(lats[1] - lats[0])
    rows = np.floor((lats[0] + res / 2 - np.asarray(lat, float)) / res).astype(int)
    cols = np.floor((np.asarray(lon, float) - (lons[0] - res / 2)) / res).astype(int)
    padded = np.pad(pop, radius_px)
    total = np.zeros(len(rows))
    for dr in range(-radius_px, radius_px + 1):
        for dc in range(-radius_px, radius_px + 1):
            r, c = rows + dr + radius_px, cols + dc + radius_px
            ok = (r >= 0) & (c >= 0) & (r < padded.shape[0]) & (c < padded.shape[1])
            total[ok] += padded[r[ok], c[ok]]
    return total


def cell_id(lat, lon):
    return np.floor(np.asarray(lat) / C.RISK_CELL_DEG).astype(int), np.floor(np.asarray(lon) / C.RISK_CELL_DEG).astype(int)


def _runs(ids: np.ndarray):
    """Contiguous runs of equal values -> (value, start, stop)."""
    cut = np.flatnonzero(np.diff(ids)) + 1
    starts, stops = np.r_[0, cut], np.r_[cut, len(ids)]
    return [(int(ids[s]), int(s), int(e)) for s, e in zip(starts, stops)]


def cell_stats(elev, slope, x0, y0) -> pd.DataFrame:
    """Per 0.02 deg cell: mean elevation, mean / p90 slope and steep share. Rows and columns map to cells
    independently, so each cell is a rectangular block - no full lat/lon meshgrid needed."""
    z = C.TERRAIN_ZOOM
    ci_rows = np.floor(row_lat(elev.shape[0], y0, z) / C.RISK_CELL_DEG).astype(int)
    cj_cols = np.floor(col_lon(elev.shape[1], x0, z) / C.RISK_CELL_DEG).astype(int)
    rows = []
    for ci, r0, r1 in _runs(ci_rows):
        for cj, c0, c1 in _runs(cj_cols):
            s = slope[r0:r1, c0:c1]
            rows.append((ci, cj, float(elev[r0:r1, c0:c1].mean()), float(s.mean()),
                         float(np.percentile(s, 90)), float((s >= C.STEEP_SLOPE_DEG).mean())))
    return pd.DataFrame(rows, columns=["ci", "cj", "elev_mean", "slope_mean", "slope_p90", "steep_frac"])


def run():
    elev, x0, y0 = fetch_dem()
    slope = slope_grid(elev, y0)
    g = cell_stats(elev, slope, x0, y0)
    g["lat"] = (g.ci + 0.5) * C.RISK_CELL_DEG
    g["lon"] = (g.cj + 0.5) * C.RISK_CELL_DEG
    g["district"] = tag_admin(g.lat.values, g.lon.values, "adm2")
    g["upazila"] = tag_admin(g.lat.values, g.lon.values, "adm3")
    g = g[g.district.isin(C.HILL_DISTRICTS)].copy()

    pop, plats, plons = load_population()
    PLON, PLAT = np.meshgrid(plons, plats)
    pci, pcj = cell_id(PLAT.ravel(), PLON.ravel())
    p = pd.DataFrame({"ci": pci, "cj": pcj, "pop": pop.ravel()}).groupby(["ci", "cj"]).pop.sum()
    g = g.merge(p.rename("population").reset_index(), on=["ci", "cj"], how="left").fillna({"population": 0})

    g.to_parquet(C.OUT / "grid.parquet", index=False)
    # Keep the pixel-level slope so fire points can be checked against the slope *where they are*.
    np.savez_compressed(C.OUT / "slope_raster.npz", slope=slope.astype(np.float16), x0=x0, y0=y0, zoom=C.TERRAIN_ZOOM)
    _slope_raster.cache_clear()
    print(f"DEM {elev.shape[0]}x{elev.shape[1]} px at zoom {C.TERRAIN_ZOOM}; grid cells: {len(g):,} "
          f"by district: {g.district.value_counts().to_dict()}")
    print(f"population in hill districts grid: {g.population.sum() / 1e6:.2f} M; "
          f"cells with p90 slope >= {C.STEEP_SLOPE_DEG} deg: {(g.slope_p90 >= C.STEEP_SLOPE_DEG).sum():,}")


if __name__ == "__main__":
    run()
