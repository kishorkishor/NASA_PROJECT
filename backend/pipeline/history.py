"""Step 3b: a harmonized 24-season burn history for every ~2 km hill cell (2003 -> latest season).

This is where the MODIS/VIIRS harmonization reaches individual places: every place on the inspection
list carries its long-term record and pattern (the ranking itself uses VIIRS fire-days, slope and people). VIIRS alone only goes back to
2012, and naively stitching MODIS (2003-2011) to VIIRS (2012+) at cell level inflates recent burning:
VIIRS catches small fires MODIS misses, so almost any cell "burns" in the VIIRS era.

Cell-level harmonization = probability calibration, in MODIS terms:
  - MODIS era (2003-2011): active = 1 if >= 1 MODIS Aqua detection in the cell in Feb-May, else 0.
  - VIIRS era (2012+):     active = P(MODIS would detect >= 1 | k VIIRS ~1 km cell-days in the cell),
    a calibration curve learned on clean overlap seasons (where both sensors flew and MODIS is trusted).
Summed over seasons this gives the expected number of MODIS-detectable active seasons - comparable
across the 2012 sensor change. (A plain integer threshold on VIIRS cell-days was tried first; it cannot
match MODIS's rate: k>=2 over-flags, k>=3 under-flags.) The curve is fitted on 2012-2019 and checked
on later clean seasons it never saw; the final curve uses all clean seasons.
"""
import json

import numpy as np
import pandas as pd
from scipy import stats

from backend import config as C
from backend.pipeline.harmonize import CHRONO_TRAIN_END, FIRST_CLEAN_YEAR
from backend.pipeline.terrain import cell_id

K_BINS = [0, 1, 2, 3, 4, 5, 6, 8, 10, 15]   # VIIRS cell-day bins: [0], [1], ..., [8-9], [10-14], [15+]
CHRONIC_SHARE, RECURRING_SHARE, OCCASIONAL_SHARE = 0.5, 0.25, 0.1
NEW_SINCE = 2020            # "new burning": essentially no activity 2003..2019, clearly active since
NEW_BEFORE_MAX, NEW_SINCE_MIN = 1.0, 1.5  # expected active seasons before / since NEW_SINCE
TREND_JUMP = 0.25           # "burning more": active share after 2012 exceeds before by >= 25 points


def season_table(grid: pd.DataFrame) -> pd.DataFrame:
    """Per (cell, season): MODIS Aqua detections, VIIRS raw detections and VIIRS ~1 km cell-days."""
    d = pd.read_parquet(C.OUT / "detections.parquet")
    d = d[(d["type"] == 0) & d.district.isin(C.HILL_DISTRICTS) & d.month.isin(C.BURN_SEASON)
          & (d.year >= FIRST_CLEAN_YEAR) & ((d.sensor == "VIIRS") | (d.satellite == "Aqua"))].copy()
    d["ci"], d["cj"] = cell_id(d.lat, d.lon)
    d["k1"] = np.floor(d.lat / C.HARMONIZE_CELL_DEG).astype(int) * 100000 + np.floor(d.lon / C.HARMONIZE_CELL_DEG).astype(int)
    keys = ["ci", "cj", "year"]
    modis = d[d.sensor == "MODIS"].groupby(keys).size().rename("modis")
    v = d[d.sensor == "VIIRS"]
    viirs_raw = v.groupby(keys).size().rename("viirs_raw")
    viirs_cd = v.drop_duplicates(["date", "k1"]).groupby(keys).size().rename("viirs_celldays")
    cells = grid[["ci", "cj"]].drop_duplicates()
    years = pd.DataFrame({"year": range(FIRST_CLEAN_YEAR, int(d.year.max()) + 1)})
    idx = pd.MultiIndex.from_frame(cells.merge(years, how="cross"))
    t = pd.concat([modis, viirs_raw, viirs_cd], axis=1).reindex(idx).fillna(0).reset_index()
    t.columns = ["ci", "cj", "year", "modis", "viirs_raw", "viirs_celldays"]
    t.loc[t.year < min(C.VIIRS_YEARS), ["viirs_raw", "viirs_celldays"]] = np.nan
    return t


def k_bin(k: pd.Series) -> np.ndarray:
    return np.searchsorted(K_BINS, k.fillna(0).values, side="right") - 1


def fit_curve(t: pd.DataFrame, years: list) -> pd.Series:
    """P(MODIS >= 1 | VIIRS cell-day bin), with a monotone fix (more VIIRS evidence never lowers it)."""
    x = t[t.year.isin(years)]
    p = (x.modis >= 1).groupby(k_bin(x.viirs_celldays)).mean().reindex(range(len(K_BINS)))
    return p.ffill().cummax()


def apply_curve(t: pd.DataFrame, curve: pd.Series) -> np.ndarray:
    return curve.values[k_bin(t.viirs_celldays)]


def check(t: pd.DataFrame, years: list, curve: pd.Series) -> dict:
    """How well the curve reproduces MODIS on `years`: Brier skill, yearly shares, per-cell counts."""
    x = t[t.year.isin(years)].copy()
    x["p"] = apply_curve(x, curve)
    y = (x.modis >= 1).astype(float)
    brier = float(np.mean((x.p - y) ** 2))
    brier_clim = float(np.mean((y.mean() - y) ** 2))
    per_year = x.assign(y=y).groupby("year")[["y", "p"]].mean()
    per_cell = x.assign(y=y).groupby(["ci", "cj"])[["y", "p"]].sum()
    burned = per_cell[(per_cell.y > 0) | (per_cell.p > 0.5)]
    return {
        "years": years,
        "brier": round(brier, 4), "brier_climatology": round(brier_clim, 4),
        "brier_skill": round(1 - brier / brier_clim, 3),
        "share_active_modis_vs_harmonized_by_year": {int(k): [round(float(r.y), 3), round(float(r.p), 3)]
                                                     for k, r in per_year.iterrows()},
        "overall_share_modis": round(float(y.mean()), 3), "overall_share_harmonized": round(float(x.p.mean()), 3),
        "cells_active_seasons_spearman": round(float(stats.spearmanr(burned.y, burned.p).statistic), 3),
        "cells_active_seasons_mean_diff_harmonized_minus_modis": round(float((per_cell.p - per_cell.y).mean()), 3),
        "reliability_by_bin": {f"k>={K_BINS[b]}": {"n": int((k_bin(x.viirs_celldays) == b).sum()),
                                                  "predicted": round(float(curve.iloc[b]), 3),
                                                  "observed": round(float(y[k_bin(x.viirs_celldays) == b].mean()), 3)
                                                  if (k_bin(x.viirs_celldays) == b).any() else None}
                               for b in range(len(K_BINS))},
    }


def flags(t: pd.DataFrame, curve: pd.Series, factor: float) -> pd.DataFrame:
    t = t.copy()
    early = t.year < min(C.VIIRS_YEARS)
    t["active"] = np.where(early, (t.modis >= 1).astype(float), apply_curve(t, curve)).round(3)
    t["active_naive"] = np.where(early, t.modis >= 1, t.viirs_raw >= 1).astype(float)
    t["activity_modis_eq"] = np.where(early, t.modis, t.viirs_celldays * factor).round(2)
    return t


def summarise(t: pd.DataFrame) -> pd.DataFrame:
    last = int(t.year.max())
    n = last - FIRST_CLEAN_YEAR + 1
    early_years, late_years = range(FIRST_CLEAN_YEAR, min(C.VIIRS_YEARS)), range(min(C.VIIRS_YEARS), last + 1)

    def total(col, yrs):
        return t[t.year.isin(yrs)].groupby(["ci", "cj"])[col].sum()

    g = t.groupby(["ci", "cj"])
    s = pd.DataFrame({
        "seasons": n,
        "active_seasons": g.active.sum().round(1),
        "active_seasons_naive": g.active_naive.sum().astype(int),
        "active_2003_2011": total("active", early_years).round(1),
        "active_2012_on": total("active", late_years).round(1),
        "active_before_2020": total("active", range(FIRST_CLEAN_YEAR, NEW_SINCE)).round(2),
        "active_since_2020": total("active", range(NEW_SINCE, last + 1)).round(2),
        "mean_activity_modis_eq": g.activity_modis_eq.mean().round(2),
    })
    likely = t[t.active >= 0.5]
    s["first_likely_active"] = likely.groupby(["ci", "cj"]).year.min()
    s["last_likely_active"] = likely.groupby(["ci", "cj"]).year.max()
    wide = t.pivot_table(index=["ci", "cj"], columns="year", values="active", aggfunc="first").reindex(s.index)
    latest_first = (wide.fillna(0).to_numpy() >= 0.5)[:, ::-1]
    s["streak"] = np.cumprod(latest_first, axis=1).sum(axis=1)  # consecutive likely-active seasons up to the latest
    share = s.active_seasons / n
    s["share_active"] = share.round(3)
    # A cell with no VIIRS fire still gets ~0.05 per VIIRS-era season (MODIS sometimes flags cells VIIRS
    # doesn't - mostly ~1 km geolocation spill-over), so "new" and "rare" use thresholds above that floor.
    s["pattern"] = np.select(
        [(s.active_before_2020 < NEW_BEFORE_MAX) & (s.active_since_2020 >= NEW_SINCE_MIN),
         share >= CHRONIC_SHARE, share >= RECURRING_SHARE, share >= OCCASIONAL_SHARE],
        ["new", "chronic", "recurring", "occasional"], "rare")
    early_share = s.active_2003_2011 / len(early_years)
    s["looks_worse_naive"] = (total("active_naive", late_years) / len(late_years) - early_share) >= TREND_JUMP
    s["looks_worse_harmonized"] = (s.active_2012_on / len(late_years) - early_share) >= TREND_JUMP
    return s.reset_index()


def run():
    grid = pd.read_parquet(C.OUT / "grid.parquet")
    h = json.loads((C.OUT / "harmonization_report.json").read_text())
    factor = h["scaling_factors"]["hills/burn"]
    outage_years = {int(m[:4]) for m in h["validation"]["aqua_outage_months_excluded"] if int(m[5:7]) in C.BURN_SEASON}
    clean = [y for y in h["calibration_years"] if y not in outage_years]
    train = [y for y in clean if y <= CHRONO_TRAIN_END]
    test = [y for y in clean if y > CHRONO_TRAIN_END]

    t = season_table(grid)
    curve_train = fit_curve(t, train)
    curve = fit_curve(t, clean)
    t = flags(t, curve, factor)
    s = summarise(t)
    t.to_parquet(C.OUT / "cell_history.parquet", index=False)
    s.to_parquet(C.OUT / "cell_summary.parquet", index=False)

    x = t[t.year >= min(C.VIIRS_YEARS)]
    share_by_year = t.groupby("year").agg(modis=("modis", lambda v: float((v >= 1).mean())),
                                          harmonized=("active", "mean"), naive=("active_naive", "mean"))
    report = {
        "definition": "active season (expected, MODIS terms) = 1/0 from MODIS Aqua in 2003-2011; "
                      "P(MODIS >= 1 | VIIRS ~1 km cell-days) from 2012, per 0.02 deg cell, Feb-May",
        "clean_overlap_years": clean,
        "curve_fitted_on_train": {"train_years": train,
                                  "p_by_bin": {f"k>={K_BINS[b]}": round(float(v), 3) for b, v in curve_train.items()}},
        "curve_used": {f"k>={K_BINS[b]}": round(float(v), 3) for b, v in curve.items()},
        "holdout_check_with_train_curve": check(t, test, curve_train),
        "in_sample_check_all_clean_years": check(t, clean, curve),
        "share_active_2012_on": {"modis": round(float((x.modis >= 1).mean()), 3),
                                 "harmonized": round(float(x.active.mean()), 3),
                                 "naive": round(float(x.active_naive.mean()), 3)},
        "share_of_cells_active_by_year": {int(y): {k: round(float(v), 3) for k, v in r.items()} for y, r in share_by_year.iterrows()},
        "cells_that_look_worse_after_2012": {"naive_stitch": int(s.looks_worse_naive.sum()),
                                              "harmonized": int(s.looks_worse_harmonized.sum())},
        "pattern_counts": s.pattern.value_counts().to_dict(),
    }
    (C.OUT / "history_report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != "share_of_cells_active_by_year"}, indent=2))


if __name__ == "__main__":
    run()
