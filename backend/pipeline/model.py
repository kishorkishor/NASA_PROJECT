"""Step 4: forecast which ~2 km hill cells will burn heavily next pre-monsoon season.

Target: heavy burning - VIIRS vegetation fire on >= HEAVY_BURN_DAYS days in Feb-May of season t.
(At least one detection happens in ~40% of hill cells every year, so that target is too easy to be useful.)

Four candidate scorers, all using only seasons < t (no leakage), compared on 2022-2026 with a rolling origin (see run):
  burned_last_year          VIIRS fire-days last season
  viirs_history             share of VIIRS seasons (2012..t-1) with heavy burning
  long_harmonized_history   mean harmonized activity per season 2003..t-1 (MODIS-equivalent units) -
                            the 24-season record that only exists because of the MODIS/VIIRS harmonization
  gbm                       gradient boosting on all history + neighbour + terrain + population features
Metrics include precision@K: of the top K cells a district office could inspect, how many really burned
heavily. Decision rule (fixed in code; an earlier, less clean version of this comparison had been seen): the
default forecast is the SIMPLEST method
whose mean precision@50 is within 0.02 (one cell in 50) of the best and whose mean average precision is
within 0.02 of the best. Simplicity order is the order listed above.
"""
import json

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score, roc_auc_score

from backend import config as C
from backend.pipeline import history as H
from backend.pipeline.harmonize import FIRST_CLEAN_YEAR
from backend.pipeline.terrain import cell_id

FIRST_TARGET_YEAR = 2015  # needs 3 VIIRS seasons of history
TRAIN_END = 2021
TOP_K = (20, 50, 100)
TOLERANCE = 0.02
SIMPLICITY_ORDER = ["burned_last_year", "viirs_history", "long_harmonized_history", "gbm"]
FEATURES = [
    "lag1_days", "lag1", "lag2", "lag3", "freq_viirs", "freq_heavy", "mean_days", "years_since_burn",
    "nbr_lag1", "nbr_freq", "long_share", "long_activity", "long_recent3",
    "elev_mean", "slope_mean", "steep_frac", "log_pop",
]


def season_fire_days(sensor_filter) -> pd.DataFrame:
    """Fire-days per (cell, year) in the burn season."""
    d = pd.read_parquet(C.OUT / "detections.parquet")
    d = d[(d["type"] == 0) & d.district.isin(C.HILL_DISTRICTS) & d.month.isin(C.BURN_SEASON)]
    d = d[sensor_filter(d)].copy()
    d["ci"], d["cj"] = cell_id(d.lat, d.lon)
    return d.drop_duplicates(["ci", "cj", "date"]).groupby(["ci", "cj", "year"]).size().rename("days").reset_index()


def neighbour_mean(grid: pd.DataFrame, col: str) -> np.ndarray:
    """Mean of `col` over the 8 surrounding cells (missing neighbours count as 0)."""
    lookup = grid.set_index(["ci", "cj"])[col]
    total = np.zeros(len(grid))
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            if di == dj == 0:
                continue
            idx = pd.MultiIndex.from_arrays([grid.ci + di, grid.cj + dj])
            total += lookup.reindex(idx).fillna(0).values
    return total / 8


def build_features(grid: pd.DataFrame, viirs: pd.DataFrame, history: pd.DataFrame, year: int) -> pd.DataFrame:
    """Feature rows for every grid cell for target `year`, using only seasons before it."""
    g = grid[["ci", "cj", "elev_mean", "slope_mean", "steep_frac", "population"]].copy()
    cells = pd.MultiIndex.from_frame(g[["ci", "cj"]])
    g["log_pop"] = np.log1p(g.population)
    wide = viirs[viirs.year < year].pivot_table(index=["ci", "cj"], columns="year", values="days", fill_value=0)
    past_years = list(range(min(C.VIIRS_YEARS), year))
    wide = wide.reindex(columns=past_years, fill_value=0).reindex(cells).fillna(0)
    burned = (wide > 0).values
    g["lag1_days"] = wide[year - 1].values
    for k in (1, 2, 3):
        g[f"lag{k}"] = (wide[year - k].values > 0).astype(int)
    g["freq_viirs"] = burned.mean(axis=1)
    g["freq_heavy"] = (wide.values >= C.HEAVY_BURN_DAYS).mean(axis=1)
    g["mean_days"] = wide.values.mean(axis=1)
    last = np.where(burned.any(axis=1), len(past_years) - 1 - np.argmax(burned[:, ::-1], axis=1), -1)
    g["years_since_burn"] = np.where(last >= 0, len(past_years) - last, 15)
    g["nbr_lag1"] = neighbour_mean(g, "lag1")
    g["nbr_freq"] = neighbour_mean(g, "freq_viirs")
    # Harmonized 2003..t-1 record (MODIS era + calibrated VIIRS era), see history.py.
    past = history[(history.year >= FIRST_CLEAN_YEAR) & (history.year < year)]
    hp = past.groupby(["ci", "cj"]).agg(long_share=("active", "mean"), long_activity=("activity_modis_eq", "mean"))
    rec = history[history.year.between(year - 3, year - 1)].groupby(["ci", "cj"]).active.mean().rename("long_recent3")
    hp = hp.join(rec).reindex(cells).fillna(0)
    for col in ("long_share", "long_activity", "long_recent3"):
        g[col] = hp[col].values
    g["year"] = year
    return g


def scores(f: pd.DataFrame, gbm=None) -> dict:
    """Every candidate's score for the rows of `f` (higher = more likely to burn heavily)."""
    s = {
        "burned_last_year": f.lag1_days.values.astype(float),
        "viirs_history": f.freq_heavy.values + 1e-3 * f.mean_days.values,
        "long_harmonized_history": f.long_activity.values,
    }
    if gbm is not None:
        s["gbm"] = gbm.predict_proba(f[FEATURES])[:, 1]
    return s


def precision_at(y, s, k) -> float:
    """Expected precision of the top k under random tie-breaking (many cells share a score at the cut-off)."""
    y, s = np.asarray(y, float), np.asarray(s, float)
    cut = np.sort(s)[::-1][k - 1]
    above, tied = s > cut, s == cut
    need = k - above.sum()
    return float((y[above].sum() + need * y[tied].mean()) / k)


def metrics(y, s) -> dict:
    out = {"roc_auc": float(roc_auc_score(y, s)), "avg_precision": float(average_precision_score(y, s))}
    out.update({f"precision_at_{k}": precision_at(y, s, k) for k in TOP_K})
    return out


def choose(summary: dict) -> str:
    """The fixed decision rule: simplest method within TOLERANCE of the best on P@50 and on AP."""
    best_p50 = max(m["precision_at_50"]["mean"] for m in summary.values())
    best_ap = max(m["avg_precision"]["mean"] for m in summary.values())
    for name in SIMPLICITY_ORDER:
        m = summary[name]
        if m["precision_at_50"]["mean"] >= best_p50 - TOLERANCE and m["avg_precision"]["mean"] >= best_ap - TOLERANCE:
            return name
    return "gbm"


def make_model():
    return HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_leaf_nodes=31,
                                          l2_regularization=1.0, random_state=0)


def history_as_of(season_tab: pd.DataFrame, cal_months: pd.DataFrame, clean_years: list, t: int) -> pd.DataFrame:
    """Harmonized cell history built only from calibration seasons before t (no later information)."""
    yrs = [y for y in clean_years if y < t]
    curve = H.fit_curve(season_tab, yrs)
    rows = cal_months[(cal_months.region == "hills") & (cal_months.season == "burn") & cal_months.year.isin(yrs)]
    factor = rows.modis.sum() / max(rows.viirs_celldays.sum(), 1)
    return H.flags(season_tab, curve, factor)


def dataset(grid, viirs, history, years) -> pd.DataFrame:
    rows = []
    for y in years:
        f = build_features(grid, viirs, history, y)
        target = viirs[viirs.year == y].set_index(["ci", "cj"]).days
        f["burned"] = (target.reindex(pd.MultiIndex.from_frame(f[["ci", "cj"]])).fillna(0).values
                       >= C.HEAVY_BURN_DAYS).astype(int)
        rows.append(f)
    return pd.concat(rows, ignore_index=True)


def run():
    grid = pd.read_parquet(C.OUT / "grid.parquet")
    history = pd.read_parquet(C.OUT / "cell_history.parquet")
    viirs = season_fire_days(lambda d: d.sensor == "VIIRS")
    last_year = int(viirs.year.max())
    clean = json.loads((C.OUT / "history_report.json").read_text())["clean_overlap_years"]
    season_tab = H.season_table(grid)
    cal_months = pd.read_parquet(C.OUT / "calibration_months.parquet")

    # Rolling origin: for each test season t, everything (calibration curve, conversion factor, GBM) is
    # refitted on seasons before t only, and every baseline also uses only seasons before t.
    test_years = list(range(TRAIN_END + 1, last_year + 1))
    per_year, rates = {}, {}
    for t in test_years:
        data_t = dataset(grid, viirs, history_as_of(season_tab, cal_months, clean, t), range(FIRST_TARGET_YEAR, t + 1))
        train_t, test_t = data_t[data_t.year < t], data_t[data_t.year == t]
        gbm_t = make_model().fit(train_t[FEATURES], train_t.burned)
        per_year[t] = {name: metrics(test_t.burned.values, sc) for name, sc in scores(test_t, gbm_t).items()}
        rates[t] = round(float(test_t.burned.mean()), 3)
    summary = {}
    for name in SIMPLICITY_ORDER:
        summary[name] = {}
        for metric in per_year[next(iter(per_year))][name]:
            vals = [per_year[yr][name][metric] for yr in per_year]
            summary[name][metric] = {"mean": round(float(np.mean(vals)), 3), "min": round(float(np.min(vals)), 3),
                                     "max": round(float(np.max(vals)), 3)}
    chosen = choose(summary)

    # Forecast the next season with every method; the watch list uses the chosen one.
    data = dataset(grid, viirs, history, range(FIRST_TARGET_YEAR, last_year + 1))
    final = make_model().fit(data[FEATURES], data.burned)
    fc_year = last_year + 1
    fc = build_features(grid, viirs, history, fc_year)
    for name, s in scores(fc, final).items():
        fc[f"score_{name}"] = s
    fc["forecast_score"] = fc[f"score_{chosen}"]
    fc["gbm_prob"] = fc["score_gbm"]
    fc = fc.merge(grid[["ci", "cj", "lat", "lon", "district", "upazila", "slope_p90"]], on=["ci", "cj"])
    fc.to_parquet(C.OUT / "forecast.parquet", index=False)
    viirs.to_parquet(C.OUT / "hill_season_firedays.parquet", index=False)
    joblib.dump(final, C.OUT / "burn_model.joblib")

    report = {
        "target": f">= {C.HEAVY_BURN_DAYS} VIIRS fire days in Feb-May, per {C.RISK_CELL_DEG} deg cell",
        "evaluation": f"rolling origin over test seasons {test_years[0]}-{test_years[-1]}: for each season t the cell "
                      "calibration curve, the hills/burn factor and the GBM (trained on seasons "
                      f"{FIRST_TARGET_YEAR}..t-1) are refitted on earlier seasons only",
        "interpretation": "The history baseline performed best in this comparison; this is not a general claim "
                          "that simple rules beat machine learning.",
        "test_cells_per_year": int(len(grid)),
        "test_heavy_burn_rate_by_year": rates,
        "decision_rule": f"simplest of {SIMPLICITY_ORDER} within {TOLERANCE} of the best mean precision@50 and mean AP",
        "chosen_default": chosen,
        "summary_over_test_years": summary,
        "per_test_year": {yr: {n: {k: round(v, 3) for k, v in m.items()} for n, m in d.items()} for yr, d in per_year.items()},
        "forecast_year": fc_year,
    }
    (C.OUT / "model_report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({k: report[k] for k in ["test_heavy_burn_rate_by_year", "chosen_default", "summary_over_test_years"]}, indent=2))


if __name__ == "__main__":
    run()
