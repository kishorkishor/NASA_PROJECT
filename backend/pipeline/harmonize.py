"""Step 2: harmonize MODIS (1 km) and VIIRS (375 m) into one consistent Burning Activity Calendar.

Why: one fire that MODIS sees as 1 pixel can light up several VIIRS pixels, so simply stitching
MODIS (2003-2011) onto VIIRS (2012+) makes burning look like it jumped after 2012.

Method:
  1. Spatial: collapse VIIRS points to unique ~1 km cell-days (MODIS-sized footprint).
  2. Statistical: learn a VIIRS->MODIS scaling factor per region x season on the overlap years.

MODIS is the *reference* sensor here, not ground truth. We use MODIS Aqua only (Terra's morning
pass sees far fewer Bangladeshi fires). Aqua's orbit has drifted since ~2023 (its afternoon pass
over Bangladesh moves later every year - measured below from the data), so drifted years are
excluded from calibration and the post-2012 record is carried by VIIRS S-NPP, whose orbit is stable.
Documented Aqua outage days (config.AQUA_OUTAGES) are removed from both sensors; a comparison with
Terra (same instrument) is only a cross-check for undocumented gaps.

Validation (see report["validation"]):
  - leave-one-year-out over the calibration years,
  - a chronological holdout (fit 2012-2019, test 2020-2023) with a documented-outage exclusion that
    was added after a first look at the results,
  - errors split by region x season,
  - bootstrap-over-years uncertainty for the factors, the calendar and the trend,
  - calibration with vs without the drifted years.
"""
import json

import numpy as np
import pandas as pd
from scipy import stats

from backend import config as C

FIRST_CLEAN_YEAR = 2003          # first full year of MODIS Aqua
CHRONO_TRAIN_END = 2019          # chronological holdout: fit <= this, test after (pre-drift only)
DRIFT_TOLERANCE_H = 0.15         # a year is "drifted" if Aqua's median pass moved > 9 min from baseline
GAP_MIN_TERRA_DAYS = 8           # Aqua outage test only in months where Terra saw fire on >= 8 days...
GAP_RATIO = 0.5                  # ...and Aqua saw fire on < 50% as many days (normally ~125%)
BOOTSTRAP_N = 2000
BEST = "region x season ratio on 1 km cell-days"


def region_of(district: pd.Series) -> pd.Series:
    return np.where(district.isin(C.HILL_DISTRICTS), "hills", "plains")


def season_of(month: pd.Series) -> pd.Series:
    return np.where(month.isin(C.BURN_SEASON), "burn", "off")


def vegetation_fires(all_satellites: bool = False) -> pd.DataFrame:
    d = pd.read_parquet(C.OUT / "detections.parquet")
    d = d[(d["type"] == 0) & d["district"].notna()].copy()
    if not all_satellites:
        d = d[(d.sensor == "VIIRS") | (d.satellite == "Aqua")]
    d["region"] = region_of(d["district"])
    return d


def modis_gap_months(d_all: pd.DataFrame) -> list:
    """(year, month) where MODIS Aqua has an outage: far fewer fire-days than MODIS Terra that month."""
    days = d_all[d_all.year >= FIRST_CLEAN_YEAR].groupby(["year", "month", "satellite"]).date.nunique().unstack(fill_value=0)
    for sat in ("Aqua", "Terra"):
        if sat not in days:
            days[sat] = 0
    gap = days[(days.Terra >= GAP_MIN_TERRA_DAYS) & (days.Aqua < GAP_RATIO * days.Terra)]
    return [(int(y), int(mo)) for y, mo in gap.index]


def monthly_counts(d: pd.DataFrame) -> pd.DataFrame:
    d = d.copy()
    d["ci"] = np.floor(d.lat / C.HARMONIZE_CELL_DEG).astype(int)
    d["cj"] = np.floor(d.lon / C.HARMONIZE_CELL_DEG).astype(int)
    keys = ["year", "month", "region"]
    modis = d[d.sensor == "MODIS"].groupby(keys).size().rename("modis")
    v = d[d.sensor == "VIIRS"]
    viirs_raw = v.groupby(keys).size().rename("viirs_raw")
    viirs_cd = v.drop_duplicates(["date", "ci", "cj"]).groupby(keys).size().rename("viirs_celldays")
    idx = pd.MultiIndex.from_product(
        [range(FIRST_CLEAN_YEAR, C.LAST_YEAR + 1), range(1, 13), ["hills", "plains"]], names=keys)
    m = pd.concat([modis, viirs_raw, viirs_cd], axis=1).reindex(idx).fillna(0).reset_index()
    m.loc[m.year < min(C.VIIRS_YEARS), ["viirs_raw", "viirs_celldays"]] = np.nan
    end = C.data_end()
    after_end = (m.year > end.year) | ((m.year == end.year) & (m.month > end.month))
    m = m[~after_end].reset_index(drop=True)
    m["season"] = season_of(m["month"])
    return m


def complete_years(m: pd.DataFrame) -> list:
    n = m.groupby("year").month.nunique()
    return sorted(int(y) for y in n[n == 12].index)



def overpass_drift(d: pd.DataFrame) -> dict:
    """Median local (UTC+6) daytime overpass time per year for Aqua and S-NPP, measured from the detections."""
    day = d[(d.daynight == "D") & d.utc_hhmm.notna() & d.satellite.isin(["Aqua", "S-NPP"])].copy()
    hhmm = day.utc_hhmm.astype(float)
    day["local_h"] = hhmm // 100 + (hhmm % 100) / 60 + 6
    med = day.groupby(["year", "satellite"]).local_h.median().unstack()
    base = float(med.loc[2012:2021, "Aqua"].median())
    drift_years = [int(y) for y, v in med["Aqua"].loc[2012:].items() if abs(v - base) > DRIFT_TOLERANCE_H]
    return {
        "median_local_overpass_h": {int(y): {s: round(float(v), 2) for s, v in r.items() if pd.notna(v)}
                                    for y, r in med.loc[2012:].iterrows()},
        "aqua_baseline_2012_2021_h": round(base, 2),
        "tolerance_h": DRIFT_TOLERANCE_H,
        "drift_years": drift_years,
    }



def _ratio(train, col, groups):
    if not groups:
        return {(): train.modis.sum() / max(train[col].sum(), 1)}
    g = train.groupby(groups)
    return (g.modis.sum() / g[col].sum().clip(lower=1)).to_dict()


def _apply(test, col, groups, ratios):
    if not groups:
        return test[col] * ratios[()]
    keys = test[groups].apply(tuple, axis=1) if len(groups) > 1 else test[groups[0]]
    return test[col].values * keys.map(ratios).fillna(1.0).values


METHODS = {
    "naive (raw VIIRS counts, no fix)": ("viirs_raw", None),
    "monthly climatology (training-years mean, no satellite data)": ("climatology", ["region", "month"]),
    "one global ratio on raw counts": ("viirs_raw", []),
    "one global ratio on 1 km cell-days": ("viirs_celldays", []),
    "season-only ratio on 1 km cell-days": ("viirs_celldays", ["season"]),
    "region-only ratio on 1 km cell-days": ("viirs_celldays", ["region"]),
    BEST: ("viirs_celldays", ["region", "season"]),
}


def _predict(train, test, col, groups):
    if col == "climatology":  # same calendar month, mean MODIS count over the training years
        clim = train.groupby(groups).modis.mean()
        p = clim.reindex(pd.MultiIndex.from_frame(test[groups])).fillna(0).values
    else:
        p = test[col].values if groups is None else _apply(test, col, groups, _ratio(train, col, groups))
    return pd.DataFrame({"year": test.year.values, "month": test.month.values, "region": test.region.values,
                         "season": test.season.values, "true": test.modis.values, "pred": p})


def _pct(a, b):
    return float(a / b - 1) * 100 if b else float("nan")


def score(p: pd.DataFrame) -> dict:
    """Errors of monthly predictions `p` (columns true/pred), overall and by region x season."""
    annual = p.groupby(["year", "region"])[["true", "pred"]].sum()
    out = {
        "monthly_MAE": round(float(np.mean(np.abs(p.pred - p.true))), 1),
        "monthly_R2": round(float(1 - np.sum((p.pred - p.true) ** 2) / np.sum((p.true - p.true.mean()) ** 2)), 3),
        "annual_total_bias_pct": round(_pct(annual.pred.sum(), annual.true.sum()), 1),
        "annual_median_abs_err_pct": round(float(np.median(np.abs(annual.pred / annual.true.clip(lower=1) - 1)) * 100), 1),
    }
    by = {}
    for (region, season), g in p.groupby(["region", "season"]):
        yearly = g.groupby("year")[["true", "pred"]].sum()
        # Year-to-year skill: does the method track which years burned more? (seasonality alone can't)
        r = yearly.true.corr(yearly.pred) if yearly.pred.std() > 0 and len(yearly) > 2 else float("nan")
        by[f"{region}/{season}"] = {
            "yearly_r": None if np.isnan(r) else round(float(r), 3),
            "mean_true_per_year": round(float(yearly.true.mean()), 1),
            "bias_pct": round(_pct(yearly.pred.sum(), yearly.true.sum()), 1),
            "median_abs_err_pct": round(float(np.median(np.abs(yearly.pred / yearly.true.clip(lower=1) - 1)) * 100), 1),
        }
    out["by_region_season"] = by
    return out


def evaluate_loyo(m: pd.DataFrame, years: list) -> dict:
    """Leave-one-year-out over `years`: fit on the others, predict the held-out year's monthly MODIS."""
    ov = m[m.year.isin(years)]
    res = {}
    for name, (col, groups) in METHODS.items():
        p = pd.concat([_predict(ov[ov.year != y], ov[ov.year == y], col, groups) for y in years])
        res[name] = score(p)
    return res


def evaluate_chrono(m: pd.DataFrame, train_years: list, test_years: list) -> dict:
    """Chronological holdout: fit once on the early years, test on the later years."""
    train, test = m[m.year.isin(train_years)], m[m.year.isin(test_years)]
    res = {}
    for name, (col, groups) in METHODS.items():
        p = _predict(train, test, col, groups)
        res[name] = score(p)
        # Out-of-sample version of the headline "jump" check: harmonized vs MODIS on unseen years.
        res[name]["test_period_total_pred_vs_modis_pct"] = round(_pct(p.pred.sum(), p.true.sum()), 1)
    return res


def bootstrap_factors(m: pd.DataFrame, years: list, n: int = BOOTSTRAP_N, seed: int = 0) -> pd.DataFrame:
    """Resample calibration *years* with replacement (months within a year are not independent)."""
    col, groups = METHODS[BEST]
    rng = np.random.default_rng(seed)
    by_year = {y: m[m.year == y] for y in years}
    rows = []
    for _ in range(n):
        sample = pd.concat([by_year[y] for y in rng.choice(years, size=len(years), replace=True)])
        rows.append({f"{k[0]}/{k[1]}": v for k, v in _ratio(sample, col, groups).items()})
    return pd.DataFrame(rows)


def per_year_ratio(m: pd.DataFrame) -> dict:
    """MODIS / VIIRS-cell-day ratio for each overlap year and region x season (stability + drift view)."""
    ov = m[m.year >= min(C.VIIRS_YEARS)]
    g = ov.groupby(["year", "region", "season"])[["modis", "viirs_celldays"]].sum()
    r = (g.modis / g.viirs_celldays.clip(lower=1)).unstack(["region", "season"])
    return {int(y): {f"{a}/{b}": round(float(v), 3) for (a, b), v in row.items()} for y, row in r.iterrows()}


def trend(series: pd.Series) -> dict:
    """Mann-Kendall-style test (Kendall tau vs year) plus Theil-Sen slope."""
    s = series.dropna()
    tau, p = stats.kendalltau(s.index, s.values)
    slope, _, _, _ = stats.theilslopes(s.values, s.index)
    return {"kendall_tau": round(float(tau), 3), "p_value": round(float(p), 4),
            "theil_sen_slope_per_year": round(float(slope), 2), "years": [int(s.index.min()), int(s.index.max())]}


def harmonize(m: pd.DataFrame, factors: dict) -> np.ndarray:
    """MODIS before VIIRS existed; VIIRS cell-days x region/season factor after (keeps going once MODIS is gone)."""
    f = np.array([factors.get(k, np.nan) for k in zip(m.region, m.season)])
    return np.where(m.year < min(C.VIIRS_YEARS), m.modis, m.viirs_celldays.values * f)


def outage_days() -> pd.DatetimeIndex:
    days = [pd.date_range(a, b) for a, b, _ in C.AQUA_OUTAGES]
    return days[0].append(days[1:]) if days else pd.DatetimeIndex([])


def run():
    d_all = vegetation_fires(all_satellites=True)
    gaps = modis_gap_months(d_all)  # heuristic cross-check for undocumented gaps
    d = d_all[(d_all.sensor == "VIIRS") | (d_all.satellite == "Aqua")]
    m = monthly_counts(d)
    drift = overpass_drift(d)
    out_days = outage_days()
    out_months = {(t.year, t.month) for t in out_days}
    m["modis_gap"] = [(y, mo) in out_months for y, mo in zip(m.year, m.month)]
    # Calibration/validation counts: documented outage days removed from BOTH sensors, so every month
    # compares the same days. The published series (m) keeps all VIIRS days.
    valid = monthly_counts(d[~d.date.isin(out_days)])
    valid.to_parquet(C.OUT / "calibration_months.parquet", index=False)
    full = complete_years(m)
    overlap_full = [y for y in C.OVERLAP_YEARS if y in full]
    cal_years = [y for y in overlap_full if y not in drift["drift_years"]]
    chrono_train = [y for y in cal_years if y <= CHRONO_TRAIN_END]
    chrono_test = [y for y in cal_years if y > CHRONO_TRAIN_END]
    gap_years = {y for y, _ in out_months}
    ref_years = [y for y in cal_years if y not in gap_years]  # whole years with a clean reference

    col, groups = METHODS[BEST]
    factors = _ratio(valid[valid.year.isin(cal_years)], col, groups)
    boot = bootstrap_factors(valid, cal_years)

    # Calendar: harmonized record in MODIS-Aqua units + calibration uncertainty from the bootstrap.
    m["harmonized"] = harmonize(m, factors).round(1)
    boot_series = np.stack([harmonize(m, {tuple(k.split("/")): v for k, v in row.items()})
                            for row in boot.to_dict("records")])
    m["harmonized_lo"] = np.nanpercentile(boot_series, 2.5, axis=0).round(1)
    m["harmonized_hi"] = np.nanpercentile(boot_series, 97.5, axis=0).round(1)
    m["naive_stitched"] = np.where(m.year < min(C.VIIRS_YEARS), m.modis, m.viirs_raw)
    m["modis_drifted"] = m.year.isin(drift["drift_years"])
    m.to_parquet(C.OUT / "calendar.parquet", index=False)

    # Yearly totals with their own bootstrap interval (summing monthly bounds would overstate the width).
    annual_rows = []
    for region in ("all", "hills", "plains"):
        sel = np.ones(len(m), bool) if region == "all" else (m.region == region).values
        years = np.sort(m.year[sel].unique())
        yidx = np.searchsorted(years, m.year.values[sel])
        sums = np.stack([np.bincount(yidx, weights=b[sel], minlength=len(years)) for b in boot_series])
        for i, y in enumerate(years):
            r = m[sel & (m.year == y).values]
            annual_rows.append({"region": region, "year": int(y), "months": int(len(r.month.unique())),
                                "modis": float(r.modis.sum()), "naive_stitched": float(r.naive_stitched.sum()),
                                "harmonized": float(r.harmonized.sum()),
                                "harmonized_lo": float(np.percentile(sums[:, i], 2.5)),
                                "harmonized_hi": float(np.percentile(sums[:, i], 97.5)),
                                "modis_drifted": bool(r.modis_drifted.any()), "modis_gap": bool(r.modis_gap.any())})
    pd.DataFrame(annual_rows).round(1).to_parquet(C.OUT / "calendar_annual.parquet", index=False)

    # Headline jump: mean annual after-2012 / 2003-2011. MODIS reference only over pre-drift years.
    def jump(values: np.ndarray, after_years: list) -> float:
        s = pd.Series(values, index=m.year).groupby(level=0).sum()
        return float(s.loc[after_years].mean() / s.loc[2003:2011].mean())

    after_all = [y for y in full if y >= 2012]
    jump_boot = [jump(b, after_all) for b in boot_series]
    jumps = {
        "naive_stitched": round(jump(m.naive_stitched.values, after_all), 2),
        "harmonized": round(jump(m.harmonized.values, after_all), 2),
        "harmonized_95ci": [round(float(np.percentile(jump_boot, q)), 2) for q in (2.5, 97.5)],
        "modis_pre_drift_reference": round(jump(m.modis.values, ref_years), 2),
        "harmonized_same_years_as_reference": round(jump(m.harmonized.values, ref_years), 2),
    }

    hills_burn = m[(m.region == "hills") & (m.season == "burn")]
    trend_main = trend(hills_burn.groupby("year").harmonized.sum())
    boot_tau = []
    for b in boot_series:
        s = pd.Series(b, index=m.year)[((m.region == "hills") & (m.season == "burn")).values].groupby(level=0).sum()
        boot_tau.append(stats.kendalltau(s.index, s.values))
    trend_main["bootstrap_share_negative_and_p_lt_0.05"] = round(
        float(np.mean([t.statistic < 0 and t.pvalue < 0.05 for t in boot_tau])), 3)

    factor_ci = {k: [round(float(boot[k].quantile(q)), 3) for q in (0.025, 0.975)] for k in boot.columns}
    calib_sensitivity = {}
    for label, yrs in {
        "pre-drift years (used)": cal_years,
        f"2012-{CHRONO_TRAIN_END} only": chrono_train,
        "all overlap years incl. drifted": sorted(set(overlap_full) | {C.LAST_YEAR}),
        "drifted years only": drift["drift_years"],
    }.items():
        if yrs:
            calib_sensitivity[label] = {"years": [min(yrs), max(yrs)],
                                        "factors": {f"{k[0]}/{k[1]}": round(float(v), 3)
                                                    for k, v in _ratio(valid[valid.year.isin(yrs)], col, groups).items()}}

    report = {
        "method": "MODIS Aqua (reference) + VIIRS S-NPP, vegetation fires only (type 0). VIIRS collapsed to unique "
                  "0.01 deg cell-days, then scaled per region x season. Calibrated on pre-drift overlap years only.",
        "data_end": C.data_end().date().isoformat(),
        "calibration_years": cal_years,
        "reference_years_for_jump": ref_years,
        "scaling_factors": {f"{k[0]}/{k[1]}": round(float(v), 3) for k, v in factors.items()},
        "scaling_factors_95ci_bootstrap_over_years": factor_ci,
        "apparent_jump_after_2012": jumps,
        "apparent_jump_definition": f"mean annual count in later years / mean 2003-2011. 'harmonized' uses "
                                    f"2012-{max(after_all)}; the MODIS reference uses pre-drift years only. "
                                    "harmonized_same_years_as_reference is close to the reference by construction "
                                    "(factors are fitted on those years) - see validation.chronological_holdout "
                                    "for the independent check. Years with an Aqua outage month are left out of the reference.",
        "trend_hills_burn_season": trend_main,
        "trend_note": "Declining harmonized detected fire activity under this calibration. The bootstrap only varies "
                      "the calibration factors; it does not cover missed fires, changing observation conditions "
                      "or model mismatch.",
        "interval_note": "95% calibration-factor uncertainty (bootstrap over calibration years), not a full error bound.",
        "trend_all_bangladesh": trend(m[m.year.isin(full)].groupby("year").harmonized.sum()),
        "validation": {
            "aqua_orbit_drift": drift,
            "aqua_outages_documented": [{"start": a, "end": b, "source": src} for a, b, src in C.AQUA_OUTAGES],
            "aqua_outage_months_excluded": sorted(f"{y}-{mo:02d}" for y, mo in out_months),
            "heuristic_gap_months_found": [f"{y}-{mo:02d}" for y, mo in gaps],
            "heuristic_gaps_covered_by_documented_outages": all((y, mo) in out_months for y, mo in gaps),
            "procedure_note": "The outage exclusion was added after the first holdout run had been inspected; the "
                              "calibration fit itself never uses test years. Documented outage days are removed "
                              "from both sensors.",
            "leave_one_year_out": {"years": cal_years, "results": evaluate_loyo(valid, cal_years)},
            "chronological_holdout": {"train_years": chrono_train, "test_years": chrono_test,
                                      "results": evaluate_chrono(valid, chrono_train, chrono_test)},
            "post_drift_divergence": {
                "note": "Same factors applied to drifted years. Divergence here reflects Aqua's changed sampling time, "
                        "not only harmonization error - MODIS is no longer a stable reference in these years.",
                "years": drift["drift_years"],
                "results": evaluate_chrono(valid, cal_years, [y for y in drift["drift_years"] if y in m.year.values]),
            },
            "calibration_sensitivity": calib_sensitivity,
            "per_year_ratio_modis_per_viirs_cellday": per_year_ratio(valid),
        },
    }
    (C.OUT / "harmonization_report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({k: report[k] for k in ["calibration_years", "scaling_factors", "scaling_factors_95ci_bootstrap_over_years",
                                             "apparent_jump_after_2012", "trend_hills_burn_season"]}, indent=2))


if __name__ == "__main__":
    run()
