"""Step 5: pre-monsoon INSPECTION PRIORITY list - steep, populated places with detected fire activity.

This is a list of places to look at before the monsoon, not a validated landslide-risk map:
a fire detection shows fire activity, not a bare slope (see s2check.py for an imagery check).

Each ~2 km cell gets  score = fire x steepness x people  (each 0-1), measured where the fires actually
were, not over the whole cell:
  fire       observed season: fire-days / HEAVY_BURN_DAYS; forecast: the chosen forecast score (model.py)
  steepness  median slope inside the ~375 m VIIRS footprint of each fire point (35 m DEM)
  people     WorldPop people within ~1.5 km of the fire points (3x3 km box)
The thresholds are hand-picked, so the ranking is re-run under 54 alternative settings; a cell is
"inspect first" only if it stays in the top TOP_N in at least ROBUST_SHARE of them.
"""
import itertools
import json

import numpy as np
import pandas as pd

from backend import config as C
from backend.pipeline.model import neighbour_mean, precision_at
from backend.pipeline.terrain import cell_id, footprint_slope, sample_population

TOP_N = 50            # roughly what a district team can field-check before the monsoon
ROBUST_SHARE = 0.8
DEFAULT = {"heavy_days": C.HEAVY_BURN_DAYS, "forecast_q": 0.95, "slope_full": 25.0, "people_full": 5000, "pop_radius": 1}
VARIANTS = {"heavy_days": [2, 3, 4], "forecast_q": [0.9, 0.95, 0.99], "slope_full": [20.0, 25.0, 30.0],
            "people_full": [2000, 5000, 20000], "pop_radius": [1, 2]}
INSPECT_MIN_SLOPE = 15.0
INSPECT_MIN_PEOPLE = 500

ACTIONS = {
    "inspect first": "Field check before June: look for bare or freshly cleared steep slopes above homes; "
                     "talk with the headman/karbari and farmers about slope-safe practice; inform the "
                     "Upazila disaster management committee.",
    "inspect": "Check recent satellite imagery first; add to the pre-monsoon field survey if capacity allows.",
    "monitor": "No action now; keep in seasonal monitoring.",
    "none": "No fire activity detected.",
}


def haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * np.arcsin(np.sqrt(a))


def nearest_place(cells: pd.DataFrame, places: pd.DataFrame):
    d = haversine_km(cells.lat.values[:, None], cells.lon.values[:, None],
                     places.lat.values[None, :], places.lon.values[None, :])
    i = d.argmin(axis=1)
    return places.name.values[i], d[np.arange(len(cells)), i].round(1)


def fire_points(grid: pd.DataFrame) -> pd.DataFrame:
    """Burn-season VIIRS vegetation-fire points in the grid, with slope and people where each fire was."""
    d = pd.read_parquet(C.OUT / "detections.parquet")
    d = d[(d["type"] == 0) & (d.sensor == "VIIRS") & d.month.isin(C.BURN_SEASON)
          & d.district.isin(C.HILL_DISTRICTS)].copy()
    d["ci"], d["cj"] = cell_id(d.lat, d.lon)
    d = d.merge(grid[["ci", "cj"]], on=["ci", "cj"])
    d["fp_slope"], d["fp_steep"] = footprint_slope(d.lat.values, d.lon.values)
    d["pop_r1"] = sample_population(d.lat.values, d.lon.values, 1)
    d["pop_r2"] = sample_population(d.lat.values, d.lon.values, 2)
    return d


def per_cell(points: pd.DataFrame) -> pd.DataFrame:
    g = points.groupby(["ci", "cj"])
    return pd.DataFrame({
        "fire_points": g.size(),
        "fire_days": g.date.nunique(),
        "fire_slope": g.fp_slope.median(),
        "fire_steep_share": g.fp_steep.mean(),
        "people_r1": g.pop_r1.mean(),
        "people_r2": g.pop_r2.mean(),
    })


def score(df: pd.DataFrame, kind: str, p: dict) -> pd.Series:
    if kind == "observed":
        burn = np.clip(df.fire_days / p["heavy_days"], 0, 1)
    else:
        q = df.forecast_score.quantile(p["forecast_q"])
        burn = np.clip(df.forecast_score / q, 0, 1) if q > 0 else df.forecast_score * 0
    slope = np.clip(df.fire_slope / p["slope_full"], 0, 1)
    people_col = "people_r1" if p["pop_radius"] == 1 else "people_r2"
    people = np.clip(np.log1p(df[people_col]) / np.log1p(p["people_full"]), 0, 1)
    return (burn * slope * people).fillna(0)


def top_set(s: pd.Series, n: int = TOP_N) -> set:
    s = s[s > 0]
    return set(s.sort_values(ascending=False, kind="stable").index[:n])


def robustness(df: pd.DataFrame, kind: str) -> tuple[pd.Series, dict]:
    """Share of the alternative settings in which each cell stays in the top TOP_N."""
    keys = [k for k in VARIANTS if not (kind == "observed" and k == "forecast_q")
            and not (kind == "forecast" and k == "heavy_days")]
    default_top = top_set(score(df, kind, DEFAULT))
    counts = pd.Series(0, index=df.index)
    jacc = []
    combos = list(itertools.product(*[VARIANTS[k] for k in keys]))
    for combo in combos:
        p = {**DEFAULT, **dict(zip(keys, combo))}
        top = top_set(score(df, kind, p))
        if top:
            counts.loc[list(top)] += 1
        jacc.append(len(top & default_top) / max(len(top | default_top), 1))
    share = counts / len(combos)
    return share, {"variants": len(combos), "parameters": {k: VARIANTS[k] for k in keys},
                   "jaccard_top_vs_default": {"median": round(float(np.median(jacc)), 3), "min": round(float(np.min(jacc)), 3)},
                   "default_top_that_are_robust": int((share.loc[list(default_top)] >= ROBUST_SHARE).sum()) if default_top else 0,
                   "cells_robust_anywhere": int((share >= ROBUST_SHARE).sum())}


def prioritise(df: pd.DataFrame, kind: str) -> tuple[pd.DataFrame, dict]:
    df = df.copy()
    df["score"] = score(df, kind, DEFAULT).round(4)
    df["robustness"], rob = robustness(df, kind)
    active = df.fire_days > 0 if kind == "observed" else df.forecast_score > 0
    df["rank"] = df.score.rank(ascending=False, method="first").astype(int)
    # People already weigh in through the score; a separate people cut-off made rank-8 places "monitor".
    inspect = (df["rank"] <= 3 * TOP_N) & (df.fire_slope >= INSPECT_MIN_SLOPE) & (df.score > 0)
    df["priority"] = np.select([df.robustness >= ROBUST_SHARE, inspect, active],
                               ["inspect first", "inspect", "monitor"], "none")
    df["action"] = df.priority.map(ACTIONS)
    return df, rob


BACKTEST_YEARS = range(2022, C.LAST_YEAR + 1)
BACKTEST_MIN_PEOPLE = 200


def history_score(viirs_days: pd.DataFrame, cells: pd.Index, t: int) -> pd.Series:
    """The chosen forecast (share of past VIIRS seasons with heavy burning), from seasons before t only."""
    past = viirs_days[viirs_days.year < t]
    wide = past.pivot_table(index=["ci", "cj"], columns="year", values="days", fill_value=0)
    wide = wide.reindex(columns=range(min(C.VIIRS_YEARS), t), fill_value=0).reindex(cells).fillna(0)
    return (wide >= C.HEAVY_BURN_DAYS).mean(axis=1) + 1e-3 * wide.mean(axis=1)


def backtest(pts: pd.DataFrame, base: pd.DataFrame, viirs_days: pd.DataFrame) -> dict:
    """Backtest the complete forecast inspection ranking, not just the burn forecast.

    For each season t the ranking is rebuilt from seasons before t only (forecast score, and slope/people
    where earlier fires were). Its top TOP_N is then checked against what happened in season t:
      heavy       >= HEAVY_BURN_DAYS fire days
      steep       heavy, and the season-t fires sat on steep ground (median footprint slope >= 15 deg)
      steep_people steep, and >= BACKTEST_MIN_PEOPLE people within ~1.5 km of the season-t fires
    This measures whether the list finds later burning on steep, populated ground - not landslides.
    """
    rows = {}
    for t in BACKTEST_YEARS:
        if not (pts.year == t).any():
            continue
        df = base.join(per_cell(pts[pts.year < t]).drop(columns=["fire_points", "fire_days"]))
        df["forecast_score"] = history_score(viirs_days, df.index, t)
        now = per_cell(pts[pts.year == t]).reindex(df.index)
        heavy = now.fire_days.fillna(0) >= C.HEAVY_BURN_DAYS
        steep = heavy & (now.fire_slope >= INSPECT_MIN_SLOPE)
        outcomes = {"heavy": heavy, "steep": steep, "steep_people": steep & (now.people_r1 >= BACKTEST_MIN_PEOPLE)}
        # slope_people_only: same slope x people terms at earlier fire locations, fire history ignored.
        rankings = {"inspection_ranking": score(df, "forecast", DEFAULT), "burn_forecast_only": df.forecast_score,
                    "slope_people_only": score(df.assign(forecast_score=1.0), "forecast", DEFAULT)}
        rows[t] = {"base_rate": {k: round(float(v.mean()), 4) for k, v in outcomes.items()}}
        for rname, sc in rankings.items():
            # ties at the cut-off are split evenly, as in the forecast comparison (model.precision_at)
            rows[t][rname] = {k: round(precision_at(v.values, sc.values, TOP_N) * TOP_N, 1) for k, v in outcomes.items()}
    mean = lambda f: round(float(np.mean([f(r) for r in rows.values()])), 1)  # noqa: E731
    return {
        "definition": f"top {TOP_N} of each ranking, rebuilt from earlier seasons only, vs what burned in season t "
                      "(ties at the cut-off split evenly); slope_people_only ranks earlier fire locations by "
                      "slope x people without the fire-history term; "
                      f"steep = median season-t fire slope >= {INSPECT_MIN_SLOPE:.0f} deg; people = >= "
                      f"{BACKTEST_MIN_PEOPLE} within ~1.5 km of the season-t fires. Tests finding later burning on "
                      "steep, populated ground, not landslide usefulness.",
        "per_year": rows,
        f"mean_hits_in_top_{TOP_N}": {
            r: {k: mean(lambda x, r=r, k=k: x[r][k]) for k in ["heavy", "steep", "steep_people"]}
            for r in ["inspection_ranking", "burn_forecast_only", "slope_people_only"]},
        f"mean_expected_by_chance_in_top_{TOP_N}": {
            k: mean(lambda x, k=k: x["base_rate"][k] * TOP_N) for k in ["heavy", "steep", "steep_people"]},
    }


def coincidence(df: pd.DataFrame, top: pd.DataFrame) -> dict:
    """Do fire, steep ground and people coincide at the fire points, or only at cell level?
    Population: the same ~3x3 km box around the cell centre and around the fire points. Slope: cell-wide
    mean vs the median of footprint-mean slopes at the fire points (related, not identical, statistics)."""
    burned = df[df.fire_days > 0]
    cell_steep = burned.slope_mean >= INSPECT_MIN_SLOPE
    fire_steep = burned.fire_slope >= INSPECT_MIN_SLOPE
    cell_people = burned.people_cell_r1 >= INSPECT_MIN_PEOPLE
    fire_people = burned.people_r1 >= INSPECT_MIN_PEOPLE
    return {
        "burned_cells": int(len(burned)),
        "steep_by_cell_mean_slope": int(cell_steep.sum()),
        "steep_by_fire_point_median_slope": int(fire_steep.sum()),
        "steep_by_both": int((cell_steep & fire_steep).sum()),
        "500plus_people_3km_box_around_cell_centre": int(cell_people.sum()),
        "500plus_people_3km_box_around_fire_points": int(fire_people.sum()),
        "populated_by_both": int((cell_people & fire_people).sum()),
        "top50_every_cell_median_fire_slope_ge_15": bool((top.fire_slope >= INSPECT_MIN_SLOPE).all()),
        "top50_fire_slope_median_deg": round(float(top.fire_slope.median()), 1),
        "top50_mean_steep_share_of_fire_footprints_range": [round(float(top.fire_steep_share.min()), 2),
                                                             round(float(top.fire_steep_share.max()), 2)],
        "top50_median_people_within_1.5km": int(top.people_r1.median()),
    }


def run():
    grid = pd.read_parquet(C.OUT / "grid.parquet")
    fc = pd.read_parquet(C.OUT / "forecast.parquet")
    summary_hist = pd.read_parquet(C.OUT / "cell_summary.parquet")
    places = pd.read_parquet(C.OUT / "settlements.parquet")
    places = places[places.district.isin(C.HILL_DISTRICTS)]
    model_report = json.loads((C.OUT / "model_report.json").read_text())

    pts = fire_points(grid)
    obs_year = int(pts.year.max())
    base = grid.copy()
    base["pop_cells3x3"] = (base.population + 8 * neighbour_mean(base, "population")).round()
    base["people_cell_r1"] = sample_population(base.lat.values, base.lon.values, 1)
    base = base.set_index(["ci", "cj"])

    observed = base.join(per_cell(pts[pts.year == obs_year]))
    observed[["fire_points", "fire_days"]] = observed[["fire_points", "fire_days"]].fillna(0).astype(int)
    observed, rob_obs = prioritise(observed, "observed")
    observed["kind"], observed["season"] = "observed", obs_year

    # Forecast: slope/people where this cell's fires have usually been (all past seasons).
    hist_cells = per_cell(pts[pts.year <= obs_year]).drop(columns=["fire_points", "fire_days"])
    forecast = base.join(hist_cells).join(fc.set_index(["ci", "cj"])[["forecast_score", "gbm_prob"]])
    forecast["fire_days"] = observed.fire_days  # last season, for context
    forecast["fire_points"] = observed.fire_points
    forecast, rob_fc = prioritise(forecast, "forecast")
    forecast["kind"], forecast["season"] = "forecast", obs_year + 1

    out = pd.concat([observed, forecast]).reset_index()
    out = out.merge(summary_hist[["ci", "cj", "active_seasons", "active_seasons_naive", "share_active", "pattern",
                                  "streak", "first_likely_active"]], on=["ci", "cj"], how="left")
    out["nearest_place"], out["nearest_place_km"] = nearest_place(out, places)
    cols = ["kind", "season", "rank", "priority", "score", "robustness", "action", "lat", "lon", "district",
            "upazila", "fire_days", "fire_points", "forecast_score", "gbm_prob", "fire_slope", "fire_steep_share",
            "slope_p90", "slope_mean", "people_r1", "people_r2", "pop_cells3x3", "people_cell_r1", "active_seasons", "active_seasons_naive",
            "share_active", "pattern", "streak", "first_likely_active", "nearest_place", "nearest_place_km", "ci", "cj"]
    out = out[cols].sort_values(["kind", "rank"])
    for c in ["fire_slope", "fire_steep_share", "people_r1", "people_r2", "robustness", "forecast_score", "gbm_prob"]:
        out[c] = out[c].round(3)
    out.to_parquet(C.OUT / "watchlist.parquet", index=False)

    summary = {"framing": "inspection priority (steep, populated places with detected fire activity) - not validated landslide risk",
               "forecast_method": model_report["chosen_default"],
               "top_n": TOP_N, "robust_share": ROBUST_SHARE, "default_parameters": DEFAULT}
    for kind, d, rob in [("observed", out[out.kind == "observed"], rob_obs), ("forecast", out[out.kind == "forecast"], rob_fc)]:
        first = d[d.priority == "inspect first"]
        grid_pop = grid.set_index(["ci", "cj"]).population
        summary[kind] = {
            "season": int(d.season.iloc[0]),
            "counts": d.priority.value_counts().to_dict(),
            "inspect_first_by_district": first.district.value_counts().to_dict(),
            "people_living_in_inspect_first_cells": int(grid_pop.reindex(pd.MultiIndex.from_frame(first[["ci", "cj"]])).sum()),
            "inspect_first_by_long_term_pattern": first.pattern.value_counts().to_dict(),
            "robustness": rob,
            "top5": d.head(5)[["rank", "priority", "district", "upazila", "nearest_place", "lat", "lon", "score",
                               "robustness", "pattern", "active_seasons"]].to_dict("records"),
        }
    viirs_days = pd.read_parquet(C.OUT / "hill_season_firedays.parquet")
    summary["forecast_inspection_backtest"] = backtest(pts, base, viirs_days)
    summary["robustness_note"] = ("'Inspect first' = in the top 50 in >= 80% of our own 54-setting grid. This is stability "
                                  "within that grid, not a probability of being correct or evidence of field usefulness.")
    summary["coincidence_check_observed"] = coincidence(out[out.kind == "observed"], out[(out.kind == "observed") & (out["rank"] <= TOP_N)])
    (C.OUT / "watchlist_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    run()
