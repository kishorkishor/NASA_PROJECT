"""Live fire watch: parsing, flags and the 'normal week' baseline (flag tests use the built pipeline outputs)."""
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import pytest

from backend import config as C
from backend.pipeline import live

BUILT = (C.OUT / "grid.parquet").exists() and (C.OUT / "detections.parquet").exists()
HEADER = "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,confidence,version,bright_ti5,frp,daynight\n"


def _csv(rows):
    return HEADER + "".join(f"{la},{lo},330,0.4,0.4,{d},{t},N,n,2.0NRT,295,{frp},D\n" for la, lo, d, t, frp in rows)


def test_read_keeps_the_bangladesh_box_and_pads_times():
    d, last = live.read(_csv([(37.4, 59.9, "2026-03-01", "830", 2.0), (22.0, 92.4, "2026-03-02", "733", 3.0)]))
    assert len(d) == 1 and d.acq_time.iloc[0] == "0733"
    assert last == "2026-03-02"


def test_same_day_handles_29_february():
    assert live.same_day(date(2024, 2, 29), 2023) == date(2023, 2, 28)
    assert live.same_day(date(2026, 10, 2), 2020) == date(2020, 10, 2)


def _clean(cells: pd.DataFrame, static: set, listed: dict) -> pd.DataFrame:
    """Squares whose centre is not near a known heat source and that are on no inspection list."""
    ok = ~live.is_static(cells.lat.values, cells.lon.values, static)
    ok &= np.array([(int(a), int(b)) not in listed for a, b in zip(cells.ci, cells.cj)], dtype=bool)
    return cells[ok]


@pytest.mark.skipif(not BUILT, reason="needs the pipeline outputs")
def test_build_flags_steep_lived_in_squares_only():
    g = pd.read_parquet(C.OUT / "grid.parquet")
    g = g[g.district.isin(["Bandarban", "Rangamati", "Khagrachhari"])]
    static, listed = live.static_cells(), live.lists()
    steep = _clean(g[(g.slope_mean >= C.STEEP_SLOPE_DEG + 2) & (g.population >= 2 * live.PEOPLE_MIN)],
                   static, listed).iloc[0]
    flat = _clean(g[g.slope_mean < 8], static, listed).iloc[0]
    plain = (24.30, 89.00)  # rural Pabna, outside the hill grid
    assert not live.is_static([plain[0]], [plain[1]], static)[0]
    rows = [(steep.lat, steep.lon, "2026-03-10", "0730", 5.0),
            (flat.lat, flat.lon, "2026-03-11", "0730", 4.0),
            (plain[0], plain[1], "2026-03-12", "0730", 3.0)]
    d, last = live.read(_csv(rows))
    out = live.build(d, last, datetime(2026, 3, 13, tzinfo=timezone.utc), "test")
    c = out["counts"]
    assert (c["bangladesh"], c["hills"], c["check"]) == (3, 2, 1)
    assert [a["check"] for a in out["alerts"]] == [True, False]
    assert [f[5] for f in out["fires"]] == [2, 1, 0]
    assert (out["from"], out["to"]) == ("2026-03-06", "2026-03-12")


@pytest.mark.skipif(not BUILT, reason="needs the pipeline outputs")
def test_known_heat_sources_are_set_aside():
    d = pd.read_parquet(C.OUT / "detections.parquet", columns=["type", "lat", "lon", "district"])
    flare = d[(d.type == 2) & d.district.notna()].iloc[0]
    rows, _ = live.read(_csv([(flare.lat, flare.lon, "2026-03-12", "0730", 9.0)]))
    out = live.build(rows, "2026-03-12", datetime(2026, 3, 13, tzinfo=timezone.utc), "test")
    assert out["counts"]["static"] == 1 and out["counts"]["bangladesh"] == 0


@pytest.mark.skipif(not BUILT, reason="needs the pipeline outputs")
def test_normal_week_is_busier_in_the_burning_season():
    g = pd.read_parquet(C.OUT / "grid.parquet")
    static = live.static_cells()
    assert live.normal(date(2026, 4, 5), g, static) > 10 * live.normal(date(2026, 10, 2), g, static)
