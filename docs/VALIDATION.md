# Results and validation

Every number here is produced by `python -m backend.run_pipeline` and saved in `data/processed/*.json`
(the running app serves them all at `/api/validation`). Methods are in [METHODS.md](METHODS.md).
MODIS Aqua is used as the reference throughout. It is not ground truth: both sensors miss small fires and
fires under cloud or smoke.

## 1. Harmonizing MODIS and VIIRS

The FIRMS archive files are labelled MODIS Collection 6 up to 2022 and Collection 6.1 from 2023, so
the calibration period (2012–2023) contains one year of Collection 6.1. That change is mainly a
calibration update of the input radiances, and the 2023 conversion ratio is in line with earlier years
(see `validation.per_year_ratio_modis_per_viirs_cellday` in the report).

### Chronological holdout

The conversion factors were fitted on 2012–2019 only and tested once on 2020–2023. We removed Aqua's
documented safe-mode days (31 March – 17 April 2022) from both sensors before calibration and testing. We
added that exclusion after we had looked at a first holdout run, so this is a chronological holdout with a
documented outage exclusion. The fit itself never used the test years.

| Method (converting VIIRS to MODIS terms) | Monthly R² | Median yearly error | Overall bias |
|---|---|---|---|
| Naive join (raw VIIRS counts) | −9.04 | 609% | +371% |
| Monthly climatology (training-years mean, no satellite data) | 0.57 | 21.9% | +9.6% |
| One ratio on raw counts | 0.96 | 65.8% | −0.5% |
| One ratio on ~1 km cell-days | 0.91 | 93.3% | −2.0% |
| Season-only ratio on cell-days | 0.96 | 40.5% | +0.3% |
| Region-only ratio on cell-days | 0.98 | 7.6% | +1.2% |
| **Region × season ratio on cell-days (used)** | **0.99** | **8.0%** | **+0.9%** |

Per group for the method we use: median yearly error, bias, and the correlation of yearly totals with MODIS.

| Group | Detections per year | Median yearly error | Bias | Year-to-year r |
|---|---|---|---|---|
| Hills, burn season (Feb–May) | ~1,720 | 8.1% | +1.1% | 0.99 |
| Hills, rest of year | ~24 | 20.9% | −37.8% | 0.97 |
| Rest of Bangladesh, burn season | ~69 | 23.3% | +17.3% | 0.93 |
| Rest of Bangladesh, rest of year | ~82 | 6.3% | −7.0% | 0.99 |

How to read this:

- **Median is not a bound.** These are medians over only four test years.
- **Where most of the gain comes from.** Separating hills from plains does most of the work: region-only
  is as good overall. The season split mainly fixes the small off-season hill counts (190% error without
  it, 21% with it).
- **Climatology is a strong baseline, but has no year-to-year skill.** In the hills burn season it
  reaches 13.3% median error because the season is so regular. It cannot say which years burned more.
  VIIRS itself tracks the year-to-year changes (r = 0.99 over the four test years, 0.79 in
  leave-one-year-out over 2012–2023); a constant conversion factor does not change that correlation.
- **Small groups are unreliable.** Accuracy is much weaker in groups with only a few dozen detections a
  year.
- **Leave-one-year-out (2012–2023)** gives R² 0.93, 10.6% median yearly error and +0.2% bias. Hills in
  the burn season: 9.7%. The climatology's yearly r of −1.0 in this test is an artefact of leaving one
  year out, not real skill.

### Conversion factors, with 95% calibration-factor uncertainty

| Group | MODIS detections per VIIRS cell-day | 95% interval |
|---|---|---|
| Hills, burn season | 0.383 | 0.342–0.428 |
| Hills, rest of year | 0.125 | 0.098–0.158 |
| Rest of Bangladesh, burn season | 0.099 | 0.088–0.114 |
| Rest of Bangladesh, rest of year | 0.110 | 0.098–0.118 |

The intervals come from resampling calibration years. They cover uncertainty in the conversion factors
only, not missed fires, changes in observing conditions, or model mismatch. The band on the charts is
labelled accordingly.

The hills burn-season factor hardly changes with the calibration period:

| Calibration years | Factor |
|---|---|
| 2012–2019 | 0.384 |
| 2012–2023 (used) | 0.383 |
| All years including the drift years | 0.385 |
| Drift years only | 0.392 |

### The jump and the trend

| Series | Mean yearly count after 2012 ÷ 2003–2011 |
|---|---|
| Naive join | 3.29× |
| Harmonized, 2012–2025 | 0.70× (95% calibration-factor interval 0.63–0.77) |
| MODIS Aqua alone, on its reliable years | 0.73× |

On MODIS's own reliable years the harmonized ratio is also 0.73×, but that agreement is built in by the
calibration. The independent evidence is the holdout above.

**Trend in harmonized detected fire activity, under this calibration:**

- **Hill districts, Feb–May, 2003–2026:** Kendall τ = −0.49 (p = 0.0005), Theil-Sen slope −60 detections
  per year.
- **All Bangladesh, 2003–2025:** τ = −0.50 (p = 0.0006).
- **Bootstrap:** all 2,000 bootstrap re-runs stay negative and significant. Those re-runs vary only the
  calibration factors. The observed series is held fixed, and missed fires are not modelled.

### MODIS problems found in the data

- **Orbit drift.** Aqua's median afternoon pass over Bangladesh was 13.13–13.37 h in 2012–2023, then 13.60 h
  (2024), 14.20 h (2025) and 14.92 h (2026). VIIRS S-NPP stayed at 13.13–13.28 h. Years more than
  0.15 h from Aqua's 2012–2021 median are excluded from calibration (2024–2026).
- **Outage.** Aqua was in safe mode from 31 March to 17 April 2022 (MODIS Characterization Support Team
  event record). An independent check found no other gaps: we compared fire-days per month with MODIS
  Terra, and that check only flags April 2022, which the documented outage covers. Other documented
  interruptions can be added to `backend/config.py`.

## 2. The harmonized history of each hill cell

Each season since 2012 is expressed as the chance that MODIS would have seen fire, given the VIIRS
fire-days in that cell. The calibration curve was fitted on 2012–2019 and checked on 2020, 2021 and 2023.

| Season | Share of cells active: MODIS | Harmonized |
|---|---|---|
| 2020 | 26.0% | 24.4% |
| 2021 | 24.6% | 26.3% |
| 2023 | 25.3% | 25.3% |

- **The shares agree in aggregate.** Brier skill against a constant rate is 0.32.
- **Per-cell agreement is moderate.** The rank correlation of active-season counts is 0.54 over the three
  held-out seasons. It is 0.88 over all eleven clean seasons, which is
  in-sample. Both figures use only cells with some activity (MODIS active in at least one season, or
  more than 0.5 expected active seasons).
- **Share of cells active per season since 2012:** MODIS 22.1%, harmonized 22.5%, naive join 42.2%.
- **Apparent increases.** 986 hill cells show an apparent increase since 2012 under the naive join,
  against 27 after calibration. We have shown that calibration removes most apparent increases, not that
  every removed increase was false.
- **Per-place counts are expected values.** A figure such as "13.6 of 24 seasons" is an expected number
  of MODIS-equivalent active seasons, not a count of observed burned seasons.

## 3. Forecasting next season

The target is heavy burning: VIIRS fire on at least 3 days in a cell in Feb–May. The test is a rolling
origin over 2022–2026. For each test season, the cell calibration curve, the conversion factor and the
gradient boosting model are refitted using earlier seasons only, and every baseline also uses only earlier
seasons. Ties at the top-50 cut-off are split evenly (expected precision).

| Method | Precision@50 (range over seasons) | Precision@20 | Avg. precision | ROC AUC |
|---|---|---|---|---|
| viirs_history (default) | 0.53 (0.42–0.64) | 0.59 | 0.339 | 0.850 |
| gbm | 0.50 (0.26–0.80) | 0.53 | 0.335 | 0.856 |
| long_harmonized_history | 0.41 (0.30–0.55) | 0.49 | 0.324 | 0.847 |
| burned_last_year | 0.40 (0.31–0.54) | 0.40 | 0.245 | 0.780 |
| random | about 0.10 | | | |

- **The simple history rule performed best in this comparison** on precision@50, precision@20 and
  average precision. The ML model is close (about 25 vs 26 of 50), has a slightly higher ROC AUC
  (0.856 vs 0.850), and varies much more from season to season. This does not show that simple rules beat machine
  learning in general.
- **The default was set by a fixed rule:** the simplest method within 0.02 of the best mean precision@50
  and the best mean average precision. The rule is in `model.py`. An earlier, less clean version of this
  comparison was known to us when the rule was written.
- **The long record does not help here.** The 24-season harmonized record does not improve next-season
  forecasts.

## 4. Backtest of the full inspection ranking

The precision above applies to cells ranked by the burn forecast alone. The forecast-mode inspection list
(the 2027 list) multiplies that forecast by slope and population, so we also backtested that complete
ranking. The 2026 list uses same-season fire-days instead of a forecast and is not backtested. For each season 2022–2026, we
rebuilt the ranking from earlier seasons only (forecast score, plus slope and people where earlier fires
were), then checked its top 50 against what burned that season.

Average number of the top 50 per season:

| Outcome in season t | Inspection ranking | Burn forecast alone | Expected by chance |
|---|---|---|---|
| Heavy burning (≥ 3 fire days) | 18.6 | 26.2 | 4.8 |
| Heavy burning with fires on steep ground (median slope ≥ 15°) | 17.2 | 8.0 | 1.8 |
| …and ≥ 200 people within ~1.5 km of those fires | 14.8 | 4.4 | 1.2 |

- **The inspection ranking gives up some burning hits** to find far more burning on steep, populated
  ground.
- **What this does and does not test.** It tests whether the list locates later burning on steep,
  populated terrain. Whether that terrain goes on to slide is not tested.
- **The outcome is partly built into the ranking.** The outcome definitions use the same kind of terrain
  measure as the ranking, so some agreement is expected by construction.

## 5. Inspection priority for 2026 and 2027

**2026 (observed):**
- **Inspect first:** 16 places (14 in Bandarban, 2 in Rangamati), with about 5,931 people living in those
  cells. 8 are chronic and 8 recurring, with 7.6–15.2 expected MODIS-equivalent active seasons out of 24.
- **Inspect:** 123 places. **Monitor:** 1,561 places.

**2027 (forecast):** 19 inspect first, 116 inspect.

**How the score behaves:**
- The fire term counts fire-days up to 3 and then stops. All top-50 cells have 3–5 fire-days, so among
  heavily burned cells the ranking is decided by slope × people.
- "Inspect first" means the place stays in the top 50 in at least 80% of 54 settings of our own scoring
  grid. That is stability within the grid we chose. It is not a probability of being right, and not
  evidence of usefulness in the field. The median Jaccard similarity of the top 50 with the default is
  only 0.35 (2026) and 0.43 (2027), which means about 26 and 30 of the 50 places are shared.

**Coincidence check:** population uses the same 3×3 km box, once around the cell centre and once around
the fire points. Slope compares the cell-wide mean slope with the median of footprint-mean slopes at the
fire points, so the two slope measures are related but not identical.

| Check | Around the cell | Around the fire points | Both |
|---|---|---|---|
| Steep (mean or median slope ≥ 15°) | 581 of 1,700 burned cells | 580 | 476 |
| ≥ 500 people within ~1.5 km | 792 | 772 | 729 |

Cell-level and fire-point measures agree for most cells; we use the fire-point version because it is the
more direct measure. Every top-50 cell has a median fire slope of at least 15°, but within those cells only
47–93% of the ground around the fire points (375 m footprints, averaged) is steep.

## 6. Sentinel-2 burn-scar test

All three groups come from Rangamati, Bandarban and Khagrachhari and use the same before (1 Dec – 31 Jan)
and after (1 May – 10 Jun) windows. A cell passes when at least 5% of its commonly clear pixels have
dNBR ≥ 0.27.

| Group | Pass the test | Median share of pixels with dNBR ≥ 0.27 | Clear in both images (median) |
|---|---|---|---|
| Top 20 places on the 2026 list | 15 of 20 | 6% | 97.7% (min 61.2%) |
| 20 random other cells with a fire detection | 10 of 20 | 4.6% | 100% (min 79.5%) |
| 20 steep cells with no fire detection in the Feb–May 2025–26 seasons | 2 of 20 | 1.4% | 100% (min 67.5%) |

How the counts change with the thresholds:

| Threshold | Top 20 | Random fire | No detection |
|---|---|---|---|
| dNBR ≥ 0.10, cell ≥ 5% | 20 | 20 | 18 |
| dNBR ≥ 0.20, cell ≥ 5% | 19 | 14 | 8 |
| dNBR ≥ 0.27, cell ≥ 3% | 19 | 13 | 6 |
| dNBR ≥ 0.27, cell ≥ 5% | 15 | 10 | 2 |
| dNBR ≥ 0.27, cell ≥ 10% | 4 | 6 | 0 |
| dNBR ≥ 0.44, cell ≥ 5% | 1 | 3 | 0 |

One-sided Mann–Whitney tests on the share of burned pixels:

| Comparison | p |
|---|---|
| Top places vs no-detection cells | 1.01e-05 |
| Random fire cells vs no-detection cells | 0.00197 |
| Top places vs random fire cells | 0.182 |

How to read this:

- **What the test shows.** It is spectral evidence consistent with burning. The whole-cell median
  change is highest in the no-detection cells (median of cell dNBR medians: top places 0.028, random fire 0.043, no detection 0.066); the groups differ in the share of strongly changed pixels, not in the typical pixel. It is not an independent
  interpretation of the images, and the "no detection" cells are not proof of no fire. Some may have had
  undetected fires or other vegetation change, and they are not matched on land cover.
- **What it does not show.** The comparison between the top places and random fire cells does not
  establish that the ranking improves scar detection over ordinary fire locations.
- **Next step.** The image pairs are ready for a blind visual check in `tools/blind_review/` (neutral
  codes, no group labels). Raters must receive only that folder: `data/processed/s2/` and
  `s2_check.parquet` hold the same images and their groups under cell IDs, and the code key is kept
  outside this repository.

## 7. Software checks

- **Tests.** `python -m pytest backend/tests` runs 29 tests. They cover:
  - outage and drift detection
  - the baselines and the bootstrap
  - the calibration curve and cell patterns
  - feature leakage, and precision with ties
  - the default-choice rule, the score and the stability test
  - the Sentinel-2 summary
  - every API endpoint
- **Web app.** `tools/capture_screenshots.py` checks the app at 1280, 390 and 360 px wide, in light and
  dark mode:
  - horizontal overflow
  - text contrast
  - tap-target size
  - console errors
  - the error, loading and empty states

## 8. Limitations

1. **No landslide records were used.** A burned steep slope is a warning sign that deserves a look, not a
   proven landslide risk.
2. **Hill-cutting is invisible to fire satellites.** Many landslide deaths happen where hills are cut for
   housing (Chittagong city, Rangamati town, the Cox's Bazar camps).
3. **The uncertainty bands are partial.** They cover the conversion factors only.
4. **The Sentinel-2 test is spectral.** It is not an independent interpretation of the imagery, and the
   controls are not matched on land cover.
5. **The scoring scales are ours.** Only 16 places are stable across them.
6. **Some inputs are coarse or old.** Population is 2020 at 1 km, the elevation model is SRTM-based
   (~35 m), and OpenStreetMap place names are sparse in the hills.
7. **Only one VIIRS satellite.** NOAA-20 and NOAA-21 VIIRS are not used yet.
8. **No local review yet.** People who live or work in the hill districts have not reviewed the list.
   Jhum is a traditional livelihood; the list is for safety checks and support with communities.

## 9. Next steps

- Match the inspection list against landslide records for past monsoons.
- Run the blind visual review of the Sentinel-2 image pairs (`tools/blind_review/`).
- Match Sentinel-2 controls on land cover, and test local dNBR thresholds.
- Add NOAA-20/21 VIIRS, and test a conversion that uses fire radiative power.
- Review the list and the suggested actions with district officials and hill communities.
