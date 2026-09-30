"""Step 6: does a fire detection mean a visibly burned slope? A Sentinel-2 before/after check (2026 season).

NASA cautions that active-fire points are not burned-area maps. So for a sample of cells we look at
Sentinel-2 L2A imagery (Earth Search, no auth) before and after the cell's 2026 fires and compute the
standard burn index dNBR = NBR_before - NBR_after, NBR = (B8A - B12) / (B8A + B12).

Three groups, 20 cells each, all drawn from the three hill-tract districts and all compared over the SAME
before (1 Dec - 31 Jan) and after (1 May - 10 Jun) windows, so no group gets a longer dry-season gap:
  priority   the top of the observed 2026 inspection list
  fire       random other hill-tract cells with >= 1 VIIRS fire day in 2026
  control    random steep hill-tract cells with no VIIRS detection in the 2025 or 2026 seasons
             (no detection is not proof of no fire; controls are not matched on land cover)
Controls matter: the dry season browns vegetation everywhere, so dNBR in unburned hills is not zero.
Per cell: share of commonly clear pixels over several dNBR thresholds (0.27 = moderate-low severity,
Key & Benson 2006). The share of scar on steep ground is descriptive only (the priority group was chosen
for steep fire points). Clouds/shadows are masked with the SCL layer. This is spectral evidence
consistent with burning, not an independent interpretation; tools/blind_review/ holds coded image pairs
for a blind visual check.

Bands are read from the COGs' first overview (40 m): full-resolution windows took 14-50 s each from
here (whole 1-6 MB tiles per read), overviews ~3 s. 40 m is enough to see scars covering >= 5% of a
~2.2 km cell (~24 ha); small single plots (~1 ha) are near the limit.
"""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import numpy as np
import pandas as pd
import rasterio
import requests
from PIL import Image
from rasterio.errors import WindowError
from rasterio.warp import transform, transform_bounds
from rasterio.windows import Window, from_bounds
from scipy import stats

from backend import config as C
from backend.pipeline.terrain import cell_id, footprint_slope

STAC = "https://earth-search.aws.element84.com/v1/search"
N_PER_GROUP = 20
SEED = 0
RES = 40.0                      # analysis grid (m) = first overview of the 20 m bands
CANDIDATES = 8                  # scenes per window whose cloud mask we look at...
MAX_SCENES = 3                  # ...and the clearest ones used in the per-pixel median
MIN_VALID = 0.3                 # a window needs >= 30% clear pixels
SCAR_DNBR = 0.27                # moderate-low severity and above
SCAR_CELL_SHARE = 0.05          # "scar visible" if >= 5% of the cell's clear pixels (~24 ha)
VALID_SCL = [2, 4, 5, 6, 7]     # dark area (burn scars often land here), vegetation, bare, water, unclassified
PRE_WINDOW = ("2025-12-01", "2026-01-31")   # before the Feb-May burn season
POST_WINDOW = ("2026-05-01", "2026-06-10")  # after it, before the monsoon clouds
DNBR_THRESHOLDS = [0.10, 0.20, 0.27, 0.44]
CELL_SHARES = [0.03, 0.05, 0.10]
HILL_TRACTS = ["Rangamati", "Bandarban", "Khagrachhari"]
BLIND = C.ROOT / "tools" / "blind_review"
ENV = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
           GDAL_HTTP_MULTIRANGE="YES", GDAL_HTTP_MERGE_CONSECUTIVE_RANGES="YES", VSI_CACHE="TRUE")
THUMBS = C.OUT / "s2"


def cell_bounds(ci, cj):
    d = C.RISK_CELL_DEG
    return cj * d, ci * d, (cj + 1) * d, (ci + 1) * d  # lon0, lat0, lon1, lat1


def search(bounds, start, end) -> list:
    body = {"collections": ["sentinel-2-l2a"], "bbox": list(bounds), "limit": 40,
            "datetime": f"{start}T00:00:00Z/{end}T23:59:59Z", "query": {"eo:cloud_cover": {"lt": 70}},
            "sortby": [{"field": "properties.eo:cloud_cover", "direction": "asc"}]}
    r = requests.post(STAC, json=body, timeout=120)
    r.raise_for_status()
    return r.json()["features"]


def _read_window(href, utm_bounds, overview_level, bands=1):
    """Window read without GDAL warping; parts outside this tile come back as 0 (= nodata)."""
    with rasterio.Env(**ENV), rasterio.open(href, overview_level=overview_level) as src:
        win = from_bounds(*utm_bounds, transform=src.transform).round_offsets().round_lengths()
        out = np.zeros((bands, int(win.height), int(win.width)), dtype=src.dtypes[0])
        try:
            inside = win.intersection(Window(0, 0, src.width, src.height))
        except WindowError:
            return out
        r0, c0 = int(inside.row_off - win.row_off), int(inside.col_off - win.col_off)
        out[:, r0:r0 + int(inside.height), c0:c0 + int(inside.width)] = src.read(list(range(1, bands + 1)), window=inside)
    return out


def read_band(item, key, utm_bounds, shape):
    """A 20 m band at its 40 m overview, as reflectance (NaN = nodata); SCL stays as class codes."""
    asset = item["assets"][key]
    a = _read_window(asset["href"], utm_bounds, overview_level=0)[0].astype(np.float32)
    fixed = np.zeros(shape, dtype=np.float32)
    h, w = min(shape[0], a.shape[0]), min(shape[1], a.shape[1])
    fixed[:h, :w] = a[:h, :w]
    band = (asset.get("raster:bands") or [{}])[0]
    if "scale" in band:
        nodata = fixed == 0
        # Earth Search: when 'earthsearch:boa_offset_applied' is true the -1000 DN offset is already baked
        # into the pixels, so only the scale applies (applying the listed -0.1 again gives negative SWIR).
        offset = 0.0 if item["properties"].get("earthsearch:boa_offset_applied") else band.get("offset", 0.0)
        fixed = fixed * band["scale"] + offset
        fixed[nodata] = np.nan
    return fixed


def utm_grid(bounds, epsg):
    x0, y0, x1, y1 = transform_bounds("EPSG:4326", f"EPSG:{epsg}", *bounds)
    x0, y0 = np.floor(x0 / RES) * RES, np.floor(y0 / RES) * RES
    x1, y1 = np.ceil(x1 / RES) * RES, np.ceil(y1 / RES) * RES
    return (x0, y0, x1, y1), (int(round((y1 - y0) / RES)), int(round((x1 - x0) / RES)))


def composite(items, bounds):
    """Per-pixel median NBR over the clearest scenes; also returns the clearest scene for a thumbnail."""
    if not items:
        return None
    epsg = items[0]["properties"]["proj:epsg"]
    items = [it for it in items if it["properties"]["proj:epsg"] == epsg]
    utm_bounds, shape = utm_grid(bounds, epsg)
    masks = []
    for it in items[:CANDIDATES]:  # cloud masks are cheap (uint8) - read them first, then pick scenes
        try:
            ok = np.isin(read_band(it, "scl", utm_bounds, shape), VALID_SCL)
        except Exception:  # a flaky tile read should not kill the batch
            continue
        masks.append((float(ok.mean()), it, ok))
        if sum(m[0] > 0.9 for m in masks) >= MAX_SCENES:
            break
    masks = sorted([m for m in masks if m[0] >= 0.05], key=lambda m: -m[0])[:MAX_SCENES]
    scenes = []
    for clear, it, ok in masks:
        try:
            nir, swir = (read_band(it, k, utm_bounds, shape) for k in ("nir08", "swir22"))
        except Exception:
            continue
        with np.errstate(invalid="ignore", divide="ignore"):
            scenes.append((clear, it, np.where(ok, (nir - swir) / (nir + swir), np.nan)))
    if not scenes:
        return None
    with np.errstate(all="ignore"):
        nbr = np.nanmedian(np.stack([s[2] for s in scenes]), axis=0)
    return {"nbr": nbr, "epsg": epsg, "utm_bounds": utm_bounds, "shape": shape,
            "dates": [s[1]["properties"]["datetime"][:10] for s in scenes], "best": scenes[0][1]}


def thumbnail(item, utm_bounds, path):
    try:
        rgb = _read_window(item["assets"]["visual"]["href"], utm_bounds, overview_level=0, bands=3)  # 20 m
        Image.fromarray(np.moveaxis(rgb, 0, -1)).resize((220, 220), Image.BILINEAR).save(path)
        return path.name
    except Exception:
        return None


def check_cell(row) -> dict:
    out = {"ci": row.ci, "cj": row.cj, "group": row.group, "pre_window": f"{row.pre_start}/{row.pre_end}",
           "post_window": f"{row.post_start}/{row.post_end}"}
    try:
        bounds = cell_bounds(row.ci, row.cj)
        pre = composite(search(bounds, row.pre_start, row.pre_end), bounds)
        post = composite(search(bounds, row.post_start, row.post_end), bounds)
    except Exception as e:
        return {**out, "verdict": "no clear imagery", "error": str(e)[:200]}
    if pre is None or post is None or pre["epsg"] != post["epsg"]:
        return {**out, "verdict": "no clear imagery"}
    both = np.isfinite(pre["nbr"]) & np.isfinite(post["nbr"])
    out.update(pre_dates=",".join(pre["dates"]), post_dates=",".join(post["dates"]),
               clear_share=round(float(both.mean()), 3))
    if both.mean() < MIN_VALID:
        return {**out, "verdict": "no clear imagery"}
    diff = pre["nbr"] - post["nbr"]
    scar = both & (diff >= SCAR_DNBR)
    # Slope under each scar pixel (35 m DEM, ~100 m box) - is the burned ground actually steep?
    x0, y0, x1, y1 = pre["utm_bounds"]
    h, w = pre["shape"]
    X, Y = np.meshgrid(x0 + (np.arange(w) + 0.5) * RES, y1 - (np.arange(h) + 0.5) * RES)
    steep_share = None
    if scar.sum() > 0:
        lon, lat = transform(f"EPSG:{pre['epsg']}", "EPSG:4326", X[scar].tolist(), Y[scar].tolist())
        s_mean, _ = footprint_slope(np.array(lat), np.array(lon), footprint_m=RES * 2.5)
        steep_share = round(float(np.nanmean(s_mean >= C.STEEP_SLOPE_DEG)), 3)
    scar_share = float(scar.sum() / both.sum())
    for th in DNBR_THRESHOLDS:
        out[f"scar_share_{int(th * 100):02d}"] = round(float((both & (diff >= th)).sum() / both.sum()), 3)
    out.update(
        dnbr_median=round(float(np.median(diff[both])), 3),
        scar_share=round(scar_share, 3),
        low_burn_share=round(float((diff[both] >= 0.1).mean()), 3),
        scar_on_steep_share=steep_share,
        nbr_pre_median=round(float(np.median(pre["nbr"][both])), 3),
        nbr_post_median=round(float(np.median(post["nbr"][both])), 3),
        verdict="scar visible" if scar_share >= SCAR_CELL_SHARE else "no clear scar",
    )
    THUMBS.mkdir(exist_ok=True)
    out["thumb_pre"] = thumbnail(pre["best"], pre["utm_bounds"], THUMBS / f"{row.ci}_{row.cj}_pre.png")
    out["thumb_post"] = thumbnail(post["best"], post["utm_bounds"], THUMBS / f"{row.ci}_{row.cj}_post.png")
    return out


def sample_cells() -> pd.DataFrame:
    w = pd.read_parquet(C.OUT / "watchlist.parquet")
    obs = w[w.kind == "observed"]
    season = int(obs.season.iloc[0])
    fires = pd.read_parquet(C.OUT / "hill_season_firedays.parquet")
    rng = np.random.default_rng(SEED)
    tracts = obs[obs.district.isin(HILL_TRACTS)]
    priority = tracts[tracts.fire_days > 0].nsmallest(N_PER_GROUP, "rank")[["ci", "cj"]].assign(group="priority")
    others = tracts[(tracts.fire_days > 0) & ~tracts.set_index(["ci", "cj"]).index.isin(priority.set_index(["ci", "cj"]).index)]
    fire = others.iloc[rng.choice(len(others), N_PER_GROUP, replace=False)][["ci", "cj"]].assign(group="fire")
    recent = fires[fires.year >= season - 1].set_index(["ci", "cj"]).index
    calm = tracts[(tracts.slope_p90 >= C.STEEP_SLOPE_DEG) & ~tracts.set_index(["ci", "cj"]).index.isin(recent)]
    control = calm.iloc[rng.choice(len(calm), N_PER_GROUP, replace=False)][["ci", "cj"]].assign(group="control")
    s = pd.concat([priority, fire, control], ignore_index=True)
    s["pre_start"], s["pre_end"] = PRE_WINDOW
    s["post_start"], s["post_end"] = POST_WINDOW
    return s


def blind_kit(res: pd.DataFrame):
    """Coded before/after pairs with no group labels, for a blind visual check by people."""
    BLIND.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(SEED + 1)
    order = rng.permutation(len(res))
    rows = []
    for n, i in enumerate(order):
        r = res.iloc[i]
        code = f"P{n + 1:02d}"
        for part in ("pre", "post"):
            src = r.get(f"thumb_{part}")
            if isinstance(src, str) and (THUMBS / src).exists():
                (BLIND / f"{code}_{'before' if part == 'pre' else 'after'}.png").write_bytes((THUMBS / src).read_bytes())
        rows.append({"code": code, "burned_slope_visible (yes/no/unsure)": "", "notes": ""})
    pd.DataFrame(rows).sort_values("code").to_csv(BLIND / "labels.csv", index=False)
    key = res.iloc[order][["ci", "cj", "group"]].assign(code=[f"P{n + 1:02d}" for n in range(len(res))])
    key.to_csv(C.OUT / "s2_blind_key.csv", index=False)  # open only after labelling


def summarize(res: pd.DataFrame) -> dict:
    ok = res[res.verdict != "no clear imagery"]
    groups = {}
    for gname, g in res.groupby("group"):
        v = g[g.verdict != "no clear imagery"]
        groups[gname] = {
            "cells": int(len(g)), "with_clear_imagery": int(len(v)),
            "scar_visible": int((v.verdict == "scar visible").sum()),
            "scar_share_median": round(float(v.scar_share.median()), 3) if len(v) else None,
            "dnbr_median_of_medians": round(float(v.dnbr_median.median()), 3) if len(v) else None,
            "scar_on_steep_share_median": round(float(v.scar_on_steep_share.dropna().median()), 3)
            if len(v) and v.scar_on_steep_share.notna().any() else None,
            "common_clear_share_median": round(float(v.clear_share.median()), 3) if len(v) else None,
            "common_clear_share_min": round(float(v.clear_share.min()), 3) if len(v) else None,
        }
        if len(v) and "scar_share_27" in v:
            groups[gname]["visible_by_threshold"] = {
                f"dNBR>={th:.2f}, cell>={cs:.0%}": int((v[f"scar_share_{int(th * 100):02d}"] >= cs).sum())
                for th in DNBR_THRESHOLDS for cs in CELL_SHARES}

    def mwu(a, b):
        a, b = ok[ok.group == a].scar_share, ok[ok.group == b].scar_share
        if len(a) < 3 or len(b) < 3:
            return None
        return float(f"{stats.mannwhitneyu(a, b, alternative='greater').pvalue:.2e}")

    return {
        "method": "Sentinel-2 L2A (Earth Search), per-pixel median of up to 3 clearest scenes before / after each "
                  "shared 2026 windows; dNBR = NBR_pre - NBR_post with B8A/B12 at 40 m (COG overview); SCL cloud/"
                  f"shadow mask; scar = dNBR >= {SCAR_DNBR}; 'scar visible' if >= {SCAR_CELL_SHARE:.0%} of clear pixels",
        "sample": {"per_group": N_PER_GROUP, "seed": SEED, "districts": HILL_TRACTS,
                   "pre_window": PRE_WINDOW, "post_window": POST_WINDOW},
        "interpretation": "Spectral evidence consistent with burning (dNBR), not an independent interpretation. "
                          "Controls have no VIIRS detection, which does not prove no fire. Steep-share is descriptive.",
        "groups": groups,
        "mann_whitney_scar_share_priority_gt_control_p": mwu("priority", "control"),
        "mann_whitney_scar_share_fire_gt_control_p": mwu("fire", "control"),
        "mann_whitney_scar_share_priority_gt_fire_p": mwu("priority", "fire"),
    }


def run(summary_only: bool = False):
    """Imagery is slow to fetch (~1-2 min per cell from here); summary_only re-scores the saved results."""
    if summary_only and (C.OUT / "s2_check.parquet").exists():
        res = pd.read_parquet(C.OUT / "s2_check.parquet")
    else:
        s = sample_cells()
        footprint_slope(np.array([22.0]), np.array([92.0]))  # load the slope raster once, before the threads
        with ThreadPoolExecutor(12) as ex:
            res = pd.DataFrame(list(ex.map(check_cell, s.itertuples(index=False))))
        res.to_parquet(C.OUT / "s2_check.parquet", index=False)
        blind_kit(res)
    report = summarize(res)
    # Saved imagery results are only valid if the list's top places are still the ones that were checked.
    current = set(map(tuple, sample_cells().query("group == 'priority'")[["ci", "cj"]].values.tolist()))
    checked = set(map(tuple, res.query("group == 'priority'")[["ci", "cj"]].values.tolist()))
    report["sample_matches_current_top_list"] = current == checked
    if current != checked:
        print("WARNING: the inspection list changed since the Sentinel-2 check - rerun with --with-s2")
    (C.OUT / "s2_report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    import sys
    run(summary_only="--summary-only" in sys.argv)
