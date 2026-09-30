"""Burn Before the Rain - API + demo page. Serves the precomputed pipeline outputs
(run `python -m backend.run_pipeline` first).

Run: python -m uvicorn backend.app:app --port 8765   ->  demo at http://localhost:8765/ , docs at /docs
"""
import json
from functools import lru_cache
from typing import Literal, Optional

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend import config as C

FRONTEND = C.ROOT / "frontend"
Priority = Literal["inspect first", "inspect", "monitor", "none"]

app = FastAPI(title="Burn Before the Rain", version="0.2.0",
              description="Harmonized MODIS+VIIRS burning calendar for Bangladesh and a pre-monsoon inspection-priority "
                          "list (steep, populated places with detected fire activity) for the Chittagong hill districts. "
                          "Not a validated landslide-risk map.")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])


@lru_cache
def table(name: str) -> pd.DataFrame:
    path = C.OUT / f"{name}.parquet"
    if not path.exists():
        raise HTTPException(503, f"{name} not built yet - run: python -m backend.run_pipeline")
    return pd.read_parquet(path)


@lru_cache
def report(name: str) -> dict:
    path = C.OUT / f"{name}.json"
    if not path.exists():
        raise HTTPException(503, f"{name} not built yet - run: python -m backend.run_pipeline")
    return json.loads(path.read_text())


def optional_report(name: str) -> Optional[dict]:
    try:
        return report(name)
    except HTTPException:
        return None


def optional_table(name: str) -> Optional[pd.DataFrame]:
    try:
        return table(name)
    except HTTPException:
        return None


def records(df: pd.DataFrame) -> list[dict]:
    """JSON-safe rows (NaN -> null, numpy scalars -> python)."""
    return json.loads(df.to_json(orient="records", date_format="iso"))


def with_s2(w: pd.DataFrame) -> pd.DataFrame:
    """Attach the Sentinel-2 check (observed season only) where a cell was sampled."""
    s2 = optional_table("s2_check")
    if s2 is None:
        return w
    cols = [c for c in ["ci", "cj", "group", "verdict", "scar_share", "scar_on_steep_share", "dnbr_median",
                        "pre_dates", "post_dates", "thumb_pre", "thumb_post"] if c in s2]
    s2 = s2[cols].rename(columns={c: f"s2_{c}" for c in cols if c not in ("ci", "cj")})
    s2["kind"] = "observed"
    return w.merge(s2, on=["ci", "cj", "kind"], how="left")


@app.get("/api/health")
def health():
    built = {p.stem for p in C.OUT.glob("*.parquet")} | {p.stem for p in C.OUT.glob("*.json")}
    return {"ok": True, "built": sorted(built)}


@app.get("/api/summary")
def summary():
    """Headline numbers for the demo page, each with its own evidence."""
    h, hist, m, w = report("harmonization_report"), report("history_report"), report("model_report"), report("watchlist_summary")
    v = h["validation"]
    best = "region x season ratio on 1 km cell-days"
    chosen = m["chosen_default"]
    return {
        "data_end": h["data_end"],
        "jump": h["apparent_jump_after_2012"],
        "trend_hills_burn_season": h["trend_hills_burn_season"],
        "scaling_factors": h["scaling_factors"],
        "scaling_factors_95ci": h["scaling_factors_95ci_bootstrap_over_years"],
        "chronological_holdout": {"train": v["chronological_holdout"]["train_years"],
                                  "test": v["chronological_holdout"]["test_years"],
                                  "ours": v["chronological_holdout"]["results"][best],
                                  "naive": v["chronological_holdout"]["results"]["naive (raw VIIRS counts, no fix)"]},
        "aqua_drift": {"drift_years": v["aqua_orbit_drift"]["drift_years"],
                       "overpass_h": v["aqua_orbit_drift"]["median_local_overpass_h"]},
        "aqua_outage_months": v["aqua_outage_months_excluded"],
        "aqua_outages": v["aqua_outages_documented"],
        "baselines": {k: v["chronological_holdout"]["results"][name]["annual_median_abs_err_pct"] for k, name in {
            "climatology": "monthly climatology (training-years mean, no satellite data)",
            "global_celldays": "one global ratio on 1 km cell-days",
            "region_only": "region-only ratio on 1 km cell-days"}.items()},
        "baselines_national": {k: v["chronological_holdout"]["results"][name]["national_annual_median_abs_err_pct"]
                               for k, name in {"global_raw": "one global ratio on raw counts",
                                               "global_celldays": "one global ratio on 1 km cell-days"}.items()},
        "cells_that_look_worse_after_2012": hist["cells_that_look_worse_after_2012"],
        "cell_calibration_holdout": {k: hist["holdout_check_with_train_curve"][k]
                                     for k in ["years", "brier_skill", "overall_share_modis", "overall_share_harmonized",
                                              "cells_active_seasons_spearman"]},
        "forecast": {"chosen": chosen, "decision_rule": m["decision_rule"],
                     "comparison": {k: {"precision_at_50": s["precision_at_50"]["mean"], "avg_precision": s["avg_precision"]["mean"],
                                        "roc_auc": s["roc_auc"]["mean"]} for k, s in m["summary_over_test_years"].items()},
                     "base_rate": round(float(np.mean(list(m["test_heavy_burn_rate_by_year"].values()))), 3)},
        "watchlist": w,
        "s2_check": optional_report("s2_report"),
    }


@app.get("/api/validation")
def validation():
    """Every report the pipeline wrote - for independent checking."""
    names = ["harmonization_report", "history_report", "model_report", "watchlist_summary", "s2_report"]
    return {n: optional_report(n) for n in names}


@app.get("/api/calendar")
def calendar(region: Literal["all", "hills", "plains"] = "all"):
    """Monthly burning activity in MODIS-Aqua units: raw MODIS, naive stitch, harmonized (+95% calibration interval)."""
    cal = table("calendar")
    if region != "all":
        cal = cal[cal.region == region]
    cols = ["modis", "viirs_raw", "viirs_celldays", "naive_stitched", "harmonized", "harmonized_lo", "harmonized_hi"]
    g = cal.groupby(["year", "month"])[cols].sum(min_count=1)
    flags = cal.groupby(["year", "month"])[["modis_drifted", "modis_gap"]].any()
    g = g.join(flags).reset_index()
    return {"region": region, "units": "MODIS-Aqua-equivalent fire detections per month",
            "interval_note": "harmonized_lo/hi: 95% bootstrap-over-years interval of the scaling factors (monthly "
                             "bounds of two regions are summed for region=all, which slightly overstates the width)",
            "rows": records(g)}


@app.get("/api/calendar/annual")
def calendar_annual(region: Literal["all", "hills", "plains"] = "all"):
    """Yearly totals with their own 95% bootstrap interval; flags years where MODIS drifted or had an outage."""
    a = table("calendar_annual")
    return {"region": region, "rows": records(a[a.region == region].drop(columns=["region"]))}


@app.get("/api/history/share")
def history_share():
    """Share of hill cells with an active burn season each year: MODIS, harmonized, naive stitch."""
    s = report("history_report")["share_of_cells_active_by_year"]
    return {"rows": [{"year": int(y), **v} for y, v in s.items()]}


@app.get("/api/fires")
def fires(year: int = Query(..., ge=2001, le=2030), month: Optional[int] = Query(None, ge=1, le=12),
          sensor: Literal["MODIS", "VIIRS", "all"] = "all", hills_only: bool = False,
          limit: int = Query(20000, le=100000)):
    """Raw fire points for the map."""
    d = table("detections")
    d = d[(d.year == year) & (d["type"] == 0)]
    if month:
        d = d[d.month == month]
    if sensor != "all":
        d = d[d.sensor == sensor]
    if hills_only:
        d = d[d.district.isin(C.HILL_DISTRICTS)]
    d = d.head(limit)[["sensor", "satellite", "date", "lat", "lon", "frp", "conf", "daynight", "district", "upazila"]]
    return {"count": int(len(d)), "rows": records(d)}


@app.get("/api/grid")
def grid():
    """Inspection-grid cells (~2.2 km) with terrain and population."""
    g = table("grid")
    return {"cell_deg": C.RISK_CELL_DEG, "rows": records(g.drop(columns=["ci", "cj"]).round(3))}


@app.get("/api/watchlist")
def watchlist(kind: Literal["observed", "forecast"] = "observed", priority: Optional[Priority] = None,
              district: Optional[str] = None, limit: int = Query(50, ge=1, le=5000)):
    """Ranked pre-monsoon inspection list: steep, populated places with detected (or likely) fire activity."""
    w = table("watchlist")
    w = w[w.kind == kind]
    if priority:
        w = w[w.priority == priority]
    if district:
        w = w[w.district.str.lower() == district.lower()]
    w = with_s2(w.head(limit)).drop(columns=["ci", "cj"])
    return {"kind": kind, "count": int(len(w)), "rows": records(w)}


@app.get("/api/map")
def map_cells(kind: Literal["observed", "forecast"] = "observed"):
    """Compact rows for drawing the map: every cell with any fire activity (or forecast), plus its priority."""
    w = table("watchlist")
    w = w[(w.kind == kind) & (w.priority != "none")]
    cols = ["lat", "lon", "priority", "rank", "score", "robustness", "nearest_place", "upazila", "pattern"]
    return {"kind": kind, "season": int(w.season.iloc[0]) if len(w) else None, "cell_deg": C.RISK_CELL_DEG,
            "rows": json.loads(w[cols].round(4).to_json(orient="values")), "columns": cols}


@app.get("/api/districts")
def districts(kind: Literal["observed", "forecast"] = "observed"):
    """Per-district roll-up of the inspection list."""
    w = table("watchlist")
    w = w[w.kind == kind]
    g = w.groupby("district").agg(
        cells=("score", "size"),
        inspect_first=("priority", lambda t: int((t == "inspect first").sum())),
        inspect=("priority", lambda t: int((t == "inspect").sum())),
        monitor=("priority", lambda t: int((t == "monitor").sum())),
        max_score=("score", "max")).reset_index().sort_values(["inspect_first", "inspect"], ascending=False)
    g["max_score"] = g["max_score"].round(4)
    return {"kind": kind, "rows": records(g)}


@app.get("/api/cell")
def cell(lat: float, lon: float):
    """Everything about the ~2 km cell containing a point: 24-season harmonized history, both lists, terrain,
    people, the Sentinel-2 check if sampled, and each forecast method's score."""
    ci, cj = int(np.floor(lat / C.RISK_CELL_DEG)), int(np.floor(lon / C.RISK_CELL_DEG))
    g = table("grid")
    row = g[(g.ci == ci) & (g.cj == cj)]
    if row.empty:
        raise HTTPException(404, "point is outside the hill-district study area")
    hist = table("cell_history")
    hist = hist[(hist.ci == ci) & (hist.cj == cj)].sort_values("year")
    summ = table("cell_summary")
    summ = summ[(summ.ci == ci) & (summ.cj == cj)]
    w = with_s2(table("watchlist")[lambda x: (x.ci == ci) & (x.cj == cj)])
    fc = table("forecast")
    fc = fc[(fc.ci == ci) & (fc.cj == cj)]
    score_cols = [c for c in fc.columns if c.startswith("score_")]
    return {
        "cell": records(row.drop(columns=["ci", "cj"]).round(3))[0],
        "history": records(hist[["year", "modis", "viirs_raw", "viirs_celldays", "active", "active_naive", "activity_modis_eq"]]),
        "history_summary": records(summ.drop(columns=["ci", "cj"]))[0] if len(summ) else None,
        "watchlist": records(w.drop(columns=["ci", "cj"])),
        "forecast_scores": records(fc[score_cols].round(4))[0] if len(fc) else None,
    }


(C.OUT / "s2").mkdir(exist_ok=True)
app.mount("/s2", StaticFiles(directory=C.OUT / "s2"), name="s2")
if FRONTEND.exists():
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
