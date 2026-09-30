"""Tests: core logic on tiny synthetic data, plus API smoke tests on the built outputs."""
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend import config as C
from backend.app import app
from backend.pipeline import harmonize, history, model, s2check, watchlist

BUILT = (C.OUT / "watchlist.parquet").exists()



def test_ratio_scales_viirs_to_modis_per_group():
    train = pd.DataFrame({"region": ["hills", "hills", "plains"], "season": ["burn"] * 3,
                          "modis": [10, 30, 5], "viirs_celldays": [20, 60, 50]})
    r = harmonize._ratio(train, "viirs_celldays", ["region", "season"])
    assert r[("hills", "burn")] == pytest.approx(0.5)
    assert r[("plains", "burn")] == pytest.approx(0.1)
    pred = harmonize._apply(train, "viirs_celldays", ["region", "season"], r)
    assert list(pred) == pytest.approx([10, 30, 5])


def test_trend_detects_decline():
    s = pd.Series([100 - 3 * i for i in range(20)], index=range(2003, 2023))
    t = harmonize.trend(s)
    assert t["kendall_tau"] == pytest.approx(-1.0)
    assert t["theil_sen_slope_per_year"] == pytest.approx(-3.0)


def _fires(year, month, satellite, days):
    return pd.DataFrame({"year": year, "month": month, "satellite": satellite,
                         "date": pd.date_range(f"{year}-{month:02d}-01", periods=days)})


def test_aqua_outage_found_by_comparing_with_terra():
    d = pd.concat([
        _fires(2021, 4, "Terra", 20), _fires(2021, 4, "Aqua", 25),   # normal month
        _fires(2022, 4, "Terra", 18), _fires(2022, 4, "Aqua", 8),    # Aqua outage
        _fires(2022, 7, "Terra", 3), _fires(2022, 7, "Aqua", 0),     # too few Terra days to judge
    ], ignore_index=True)
    assert harmonize.modis_gap_months(d) == [(2022, 4)]


def test_overpass_drift_flags_only_late_years():
    rows = []
    for y in range(2012, 2026):
        aqua = 715 if y < 2024 else (800 if y == 2024 else 840)   # 13:15 local, then later
        rows += [{"year": y, "satellite": "Aqua", "daynight": "D", "utc_hhmm": aqua},
                 {"year": y, "satellite": "S-NPP", "daynight": "D", "utc_hhmm": 715}]
    drift = harmonize.overpass_drift(pd.DataFrame(rows))
    assert drift["aqua_baseline_2012_2021_h"] == pytest.approx(13.25)
    assert drift["drift_years"] == [2024, 2025]


def test_bootstrap_factors_are_exact_when_ratio_is_constant():
    m = pd.DataFrame({"year": np.repeat(range(2012, 2020), 2), "region": "hills", "season": ["burn", "off"] * 8,
                      "modis": 40.0, "viirs_celldays": 100.0})
    b = harmonize.bootstrap_factors(m, list(range(2012, 2020)), n=50)
    assert np.allclose(b["hills/burn"], 0.4) and np.allclose(b["hills/off"], 0.4)


def test_harmonize_keeps_modis_before_viirs_existed():
    m = pd.DataFrame({"year": [2010, 2015], "region": "hills", "season": "burn",
                      "modis": [7.0, 9.0], "viirs_celldays": [np.nan, 20.0]})
    out = harmonize.harmonize(m, {("hills", "burn"): 0.5})
    assert list(out) == [7.0, 10.0]


def test_score_reports_region_season_errors():
    p = pd.DataFrame({"year": [2020, 2020, 2021, 2021], "month": [3, 8, 3, 8], "region": "hills",
                      "season": ["burn", "off", "burn", "off"], "true": [100.0, 10, 100, 10], "pred": [110.0, 10, 90, 10]})
    s = harmonize.score(p)
    assert s["by_region_season"]["hills/burn"]["median_abs_err_pct"] == pytest.approx(10.0)
    assert s["by_region_season"]["hills/off"]["bias_pct"] == pytest.approx(0.0)



def test_calibration_curve_is_monotone():
    t = pd.DataFrame({"year": 2015, "viirs_celldays": [0] * 10 + [1] * 10 + [2] * 10,
                      "modis": [0] * 9 + [1] + [1] * 5 + [0] * 5 + [1] * 3 + [0] * 7})
    curve = history.fit_curve(t, [2015])
    assert curve.iloc[0] == pytest.approx(0.1)
    assert curve.iloc[1] == pytest.approx(0.5)
    assert curve.iloc[2] == pytest.approx(0.5)   # raw 0.3 is lifted: more VIIRS evidence never lowers it
    assert (np.diff(curve.values) >= 0).all()


def test_history_patterns_new_and_chronic():
    years = range(2003, 2027)
    rows = []
    for y in years:
        rows.append({"ci": 1, "cj": 1, "year": y, "active": 1.0, "active_naive": 1.0, "activity_modis_eq": 2.0})
        rows.append({"ci": 2, "cj": 2, "year": y, "active": 0.9 if y >= 2020 else 0.0,
                     "active_naive": 1.0 if y >= 2012 else 0.0, "activity_modis_eq": 1.0})
    s = history.summarise(pd.DataFrame(rows)).set_index(["ci", "cj"])
    assert s.loc[(1, 1), "pattern"] == "chronic" and s.loc[(1, 1), "streak"] == 24
    assert s.loc[(2, 2), "pattern"] == "new"
    assert bool(s.loc[(2, 2), "looks_worse_naive"])



def _toy_grid():
    ci, cj = np.meshgrid(range(3), range(3))
    return pd.DataFrame({"ci": ci.ravel(), "cj": cj.ravel(), "elev_mean": 100.0, "slope_mean": 10.0,
                         "steep_frac": 0.2, "population": 50.0})


def test_features_never_see_target_year():
    grid = _toy_grid()
    viirs = pd.DataFrame({"ci": [1, 1], "cj": [1, 1], "year": [2014, 2016], "days": [4, 9]})
    hist = pd.DataFrame({"ci": [1, 1], "cj": [1, 1], "year": [2010, 2016], "active": [1.0, 1.0],
                         "activity_modis_eq": [3.0, 50.0]})
    f = model.build_features(grid, viirs, hist, 2016).set_index(["ci", "cj"])
    centre = f.loc[(1, 1)]
    assert centre.lag1_days == 0          # 2015: no fire; the 2016 fire must not leak in
    assert centre.lag2 == 1               # 2014 fire
    assert centre.years_since_burn == 2
    assert centre.long_activity == pytest.approx(3.0 / 1)   # only the 2010 season counts, not 2016
    assert f.loc[(0, 0)].nbr_freq > 0     # neighbour of a burned cell
    assert f.loc[(0, 0)].freq_viirs == 0


def test_precision_at_k():
    y = np.array([1, 0, 1, 0, 0])
    s = np.array([0.9, 0.8, 0.7, 0.1, 0.0])
    assert model.precision_at(y, s, 2) == pytest.approx(0.5)
    assert model.precision_at(y, s, 3) == pytest.approx(2 / 3)


def test_precision_at_k_splits_ties_fairly():
    y = np.array([1, 0, 0, 1])
    s = np.array([1.0, 1.0, 1.0, 0.0])   # three cells tie for two places
    assert model.precision_at(y, s, 2) == pytest.approx(1 / 3)


def test_climatology_baseline_uses_training_mean():
    train = pd.DataFrame({"year": [2012, 2013], "month": 3, "region": "hills", "season": "burn",
                          "modis": [100.0, 300.0], "viirs_celldays": [1.0, 1.0]})
    test = train.assign(year=2020, modis=50.0)
    p = harmonize._predict(train, test, "climatology", ["region", "month"])
    assert list(p.pred) == pytest.approx([200.0, 200.0])


def test_decision_rule_prefers_simplest_within_tolerance():
    def s(p50, ap):
        return {"precision_at_50": {"mean": p50}, "avg_precision": {"mean": ap}}
    summary = {"burned_last_year": s(0.40, 0.25), "viirs_history": s(0.52, 0.34),
               "long_harmonized_history": s(0.41, 0.32), "gbm": s(0.53, 0.35)}
    assert model.choose(summary) == "viirs_history"
    summary["viirs_history"] = s(0.45, 0.30)
    assert model.choose(summary) == "gbm"



def _cells():
    idx = pd.MultiIndex.from_tuples([(i, 0) for i in range(60)], names=["ci", "cj"])
    df = pd.DataFrame({"fire_days": 3, "fire_slope": 20.0, "people_r1": 100.0, "people_r2": 300.0,
                       "forecast_score": 0.1}, index=idx)
    df.iloc[0] = [9, 35.0, 8000.0, 20000.0, 0.9]       # clearly the top place under any setting
    df.iloc[1] = [0, 35.0, 8000.0, 20000.0, 0.0]       # no fire: never ranked
    df.iloc[2] = [5, 2.0, 8000.0, 20000.0, 0.5]        # flat ground: low
    return df


def test_score_needs_fire_slope_and_people():
    s = watchlist.score(_cells(), "observed", watchlist.DEFAULT)
    assert s.iloc[0] == s.max()
    assert s.iloc[1] == 0
    assert s.iloc[2] < s.iloc[3]


def test_robustness_and_priority():
    df, rob = watchlist.prioritise(_cells(), "observed")
    assert df.iloc[0]["robustness"] == pytest.approx(1.0)
    assert df.iloc[0]["priority"] == "inspect first"
    assert df.iloc[1]["priority"] == "none"
    assert rob["variants"] == 3 * 3 * 3 * 2


def test_haversine_one_degree_latitude():
    assert watchlist.haversine_km(22.0, 92.0, 23.0, 92.0) == pytest.approx(111.2, abs=0.5)



def test_s2_summary_counts_and_test():
    rows = []
    for g, share in [("priority", 0.12), ("control", 0.01), ("fire", 0.06)]:
        for k in range(8):
            rows.append({"group": g, "verdict": "scar visible" if share + k * 0.001 >= 0.05 else "no clear scar",
                         "scar_share": share + k * 0.001, "dnbr_median": 0.05, "scar_on_steep_share": 0.8,
                         "clear_share": 0.95, **{f"scar_share_{t}": share + k * 0.001 for t in ("10", "20", "27", "44")}})
    rows.append({"group": "control", "verdict": "no clear imagery", "scar_share": np.nan, "dnbr_median": np.nan,
                 "scar_on_steep_share": np.nan, "clear_share": 0.1})
    r = s2check.summarize(pd.DataFrame(rows))
    assert r["groups"]["priority"]["scar_visible"] == 8
    assert r["groups"]["control"]["with_clear_imagery"] == 8 and r["groups"]["control"]["scar_visible"] == 0
    assert r["mann_whitney_scar_share_priority_gt_control_p"] < 0.001
    assert r["groups"]["priority"]["visible_by_threshold"]["dNBR>=0.27, cell>=10%"] == 8
    assert r["groups"]["control"]["common_clear_share_median"] == pytest.approx(0.95)


def test_s2_grid_is_snapped_to_analysis_resolution():
    (x0, y0, x1, y1), (h, w) = s2check.utm_grid((92.40, 21.72, 92.42, 21.74), 32646)
    assert x0 % s2check.RES == 0 and y1 % s2check.RES == 0
    assert (x1 - x0) / s2check.RES == w and (y1 - y0) / s2check.RES == h



client = TestClient(app)


@pytest.mark.skipif(not BUILT, reason="run python -m backend.run_pipeline first")
class TestAPI:
    def test_health(self):
        assert client.get("/api/health").json()["ok"]

    def test_summary_headlines(self):
        s = client.get("/api/summary").json()
        j = s["jump"]
        assert j["naive_stitched"] > 2 and j["harmonized_95ci"][0] <= j["harmonized"] <= j["harmonized_95ci"][1]
        assert s["chronological_holdout"]["ours"]["annual_median_abs_err_pct"] < 20
        assert s["chronological_holdout"]["ours"]["national_annual_median_abs_err_pct"] < 20
        assert "slope_people_only" in s["watchlist"]["forecast_inspection_backtest"]["mean_hits_in_top_50"]
        assert s["forecast"]["chosen"] in model.SIMPLICITY_ORDER

    def test_calendar_shapes_and_intervals(self):
        rows = client.get("/api/calendar?region=hills").json()["rows"]
        end = C.data_end()
        assert len(rows) == 12 * (end.year - 2003) + end.month
        assert all(r["viirs_raw"] is None for r in rows if r["year"] < 2012)
        late = [r for r in rows if r["year"] >= 2012]
        assert all(r["harmonized_lo"] <= r["harmonized"] <= r["harmonized_hi"] for r in late)
        annual = client.get("/api/calendar/annual?region=hills").json()["rows"]
        assert any(r["modis_drifted"] for r in annual) and any(r["modis_gap"] for r in annual)

    def test_history_share(self):
        rows = client.get("/api/history/share").json()["rows"]
        early = [r for r in rows if r["year"] < 2012]
        assert all(r["harmonized"] == r["naive"] == r["modis"] for r in early)

    def test_watchlist_sorted_and_filtered(self):
        rows = client.get("/api/watchlist?kind=observed&priority=inspect first&limit=20").json()["rows"]
        assert rows and all(r["priority"] == "inspect first" for r in rows)
        scores = [r["score"] for r in rows]
        assert scores == sorted(scores, reverse=True)
        assert all(r["robustness"] >= 0.8 for r in rows)

    def test_map_rows_are_compact(self):
        m = client.get("/api/map?kind=forecast").json()
        assert m["columns"][:3] == ["lat", "lon", "priority"]
        assert m["rows"] and all(r[2] != "none" for r in m["rows"])

    def test_cell_lookup_and_outside(self):
        top = client.get("/api/watchlist?limit=1").json()["rows"][0]
        c = client.get(f"/api/cell?lat={top['lat']}&lon={top['lon']}").json()
        assert c["cell"]["district"] == top["district"]
        assert len(c["history"]) == C.data_end().year - 2003 + 1
        assert {w["kind"] for w in c["watchlist"]} == {"observed", "forecast"}
        assert client.get("/api/cell?lat=23.8&lon=90.4").status_code == 404  # Dhaka

    def test_validation_bundle(self):
        v = client.get("/api/validation").json()
        assert v["harmonization_report"]["validation"]["chronological_holdout"]["test_years"]
        assert v["model_report"]["chosen_default"]

    def test_bad_params_rejected(self):
        assert client.get("/api/fires?year=1990").status_code == 422
        assert client.get("/api/watchlist?kind=bogus").status_code == 422
        assert client.get("/api/watchlist?priority=high").status_code == 422

    def test_frontend_served(self):
        r = client.get("/")
        assert r.status_code == 200 and "Burn Before the Rain" in r.text
