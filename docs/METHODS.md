# Methods

This page describes how each part of the pipeline works. The code for each step is in `backend/pipeline/`,
and the results and checks are in [VALIDATION.md](VALIDATION.md).

## 1. Fire data

We use NASA FIRMS active-fire detections for Bangladesh:
- MODIS active fire (MCD14ML) from 2000; the archive files are Collection 6 up to 2022 and Collection 6.1 from 2023
- VIIRS S-NPP 375 m from 2012

Both are standard-processing (science-quality) products, and the data runs to 30 June 2026.

We keep only vegetation fires (FIRMS `type = 0`) and drop static industrial heat sources and offshore
detections. Every point is tagged with its district and upazila using geoBoundaries polygons
(`prepare.py`).

For MODIS we use **Aqua only**. Aqua crosses Bangladesh in the early afternoon, like VIIRS S-NPP (about
13:15 local time). Terra passes in the morning, when far fewer fires are burning, so mixing the two
satellites would add its own jump to the record.

## 2. Harmonizing MODIS and VIIRS (`harmonize.py`)

A fire that MODIS records as one 1 km pixel can light up several 375 m VIIRS pixels, and VIIRS also sees
smaller fires. We correct for this in two steps.

1. **Footprint.** VIIRS detections are collapsed to unique (date, 0.01° cell) pairs, which we call
   *cell-days*. A 0.01° cell is about 1.1 km, roughly one MODIS pixel.
2. **Scaling.** For each region (hill districts or rest of Bangladesh) and season (burn season Feb–May,
   or the other months), we compute

   factor = total MODIS detections ÷ total VIIRS cell-days, over the calibration years.

The harmonized record is in MODIS-equivalent detections: MODIS itself for 2003–2011, and VIIRS
cell-days × factor from 2012 on. Using VIIRS for the modern part keeps the record going after MODIS is
retired.

**Calibration years.** These are the years both sensors flew and MODIS was reliable, which gives 2012–2023.
Two problems had to be removed first:

- **Aqua orbit drift.** Aqua's afternoon pass has been drifting later. We measured it from the
  acquisition times of the detections themselves: the median local pass time per year. Aqua sat at
  13.13–13.37 h from 2012 to 2023, then moved to 13.60 h (2024), 14.20 h (2025) and 14.92 h (2026).
  VIIRS S-NPP stayed between 13.13 and 13.28 h. A year counts as drifted when Aqua is more than
  0.15 h (9 minutes) from its 2012–2021 median, which excludes 2024–2026.
- **Aqua outages.** Aqua was in safe mode from 31 March to 17 April 2022 (MODIS Characterization
  Support Team event record). Those days are removed from both sensors before calibration and testing,
  so each month compares the same days. As a cross-check for undocumented gaps, we compare Aqua with
  Terra (the same instrument). Normally Aqua sees fire on about 125% as many days as Terra; a month where
  Terra saw fire on at least 8 days but Aqua on fewer than half as many is flagged. It flags only April
  2022, which the documented outage covers.

**Tests of the conversion.** The conversion is compared with:
- a naive join
- a monthly climatology (the training-years mean, no satellite data)
- one ratio on raw counts
- one ratio on cell-days
- season-only and region-only ratios on cell-days

All are tested in a chronological holdout (fit 2012–2019, test 2020–2023) and in leave-one-year-out. As
well as monthly R² and annual error (per region and Bangladesh-wide), we report the correlation of yearly totals with MODIS. Seasonality
alone can give a high monthly R² with no skill at telling years apart.

**Uncertainty.** We resample the calibration years with replacement 2,000 times (whole years, because
months within a year are not independent). The spread of the resulting factors gives a 95%
calibration-factor interval for the factors, the calendar and the jump ratio. For the trend we report the
share of re-runs that still show a significant decline. It does not cover
missed fires, changes in observing conditions or model mismatch.

**Trend.** Kendall's τ against year plus a Theil-Sen slope, on the harmonized hill-district burn-season totals.

## 3. A harmonized history for every ~2 km hill cell (`history.py`)

The study area is the five Chittagong hill districts: Rangamati, Bandarban, Khagrachhari, Chittagong
and Cox's Bazar. We divide it into 4,351 cells of 0.02° (about 2.2 km).

For each cell and each burn season (Feb–May), we record whether the cell was active, in MODIS terms:

- **2003–2011:** 1 if MODIS Aqua detected fire in the cell, otherwise 0.
- **2012 onward:** the probability that MODIS *would have* detected fire, given how many VIIRS cell-days
  the cell had that season.

That probability comes from a calibration curve learned on seasons when both sensors flew:

| VIIRS cell-days in the cell | 0 | 1 | 2 | 3 | 4 | 5 | 6–7 | 8–9 | 10+ |
|---|---|---|---|---|---|---|---|---|---|
| Chance MODIS saw fire | 0.05 | 0.28 | 0.43 | 0.55 | 0.64 | 0.69 | 0.77 | 0.84 | 0.85 |

The curve is forced to be non-decreasing, so more VIIRS evidence never lowers it. For testing it is fitted
on 2012–2019 and checked on 2020, 2021 and 2023. The final version uses all clean seasons.

We tried a simple threshold first ("active if VIIRS saw at least k fire-days"). It can't match MODIS:
k = 2 flags too many cells and k = 3 too few.

Adding up the 24 seasons gives the expected number of active seasons per cell, and from that a
long-term pattern:
- **chronic:** active in at least half of the seasons
- **recurring:** at least a quarter
- **occasional:** at least 10%
- **rare:** less than that
- **new:** almost no activity from 2003 to 2019, but clearly active since 2020

## 4. Forecasting next season (`model.py`)

The target is **heavy burning**: VIIRS fire on at least 3 separate days in a cell during Feb–May. At least
one detection happens in about 40% of hill cells every year, so that would be too easy a target.

We compare four ways of ranking cells. Each uses only seasons before the one being predicted.

| Method | Score |
|---|---|
| `burned_last_year` | last season's fire-days |
| `viirs_history` | share of past VIIRS seasons (2012 onward) with heavy burning |
| `long_harmonized_history` | mean harmonized activity per season since 2003 |
| `gbm` | gradient boosting on 17 features: history, neighbours, terrain, population |

The methods are scored on 2022–2026 with a rolling origin. For each test season, the cell calibration
curve, the conversion factor and the gradient boosting model are refitted using earlier seasons only, and
every baseline also uses only earlier seasons. The main measure is **precision at 50**: of the 50 cells a
method ranks highest, how many really burned heavily. That is roughly the number of places a district team
could check before the monsoon. Ties at the cut-off are split evenly.

The rule for choosing a default is fixed in the code (`model.choose`): take the simplest method whose
mean precision@50 and mean average precision are both within 0.02 of the best. An earlier, less clean
version of this comparison was known to us when the rule was written.

## 5. Inspection priority (`terrain.py`, `watchlist.py`)

**Terrain.** Slope comes from AWS Terrain Tiles at zoom 12 (about 35 m pixels). We tried zoom 10
(about 140 m) first, but it flattens the hills. In a test area in Thanchi and Ruma, the median slope
under fire points was 11° at 140 m and 16° at 35 m.

**Measured where the fires are, not over the whole cell.** For every VIIRS fire point we take:
- the mean slope inside a ~375 m box (one VIIRS pixel) around the point, and the share of steep
  ground (15° or more) in that box
- the number of people within about 1.5 km (a 3×3 km block of the WorldPop 1 km grid)

**Score per cell:**

score = fire × steepness × people, each scaled from 0 to 1

- **fire:** fire-days in the 2026 season ÷ 3, capped at 1 (so among heavily burned cells the ranking is
  slope × people); for 2027, the forecast score ÷ its 95th percentile
- **steepness:** the median fire-point slope ÷ 25°
- **people:** log(1 + people near the fires) ÷ log(1 + 5,000)

For the 2027 forecast, slope and people come from all past fire points in the cell.

**Stability.** These scales are our own choices, so the ranking is recomputed under 54 combinations:
- heavy-day divisor 2, 3 or 4 (or, for the forecast, a forecast quantile of 0.90, 0.95 or 0.99)
- full-slope value 20, 25 or 30°
- people saturation 2,000, 5,000 or 20,000
- people radius about 1.5 or 2.5 km

**Priority levels:**
- **Inspect first:** in the top 50 in at least 80% of the 54 combinations.
- **Inspect:** in the top 150, with fires on steep ground (median fire slope of 15° or more).
- **Monitor:** any other cell with detected (or forecast) fire.

"Inspect first" is stability within this grid of settings. It is not a probability of being right.

**Backtest.** For each season 2022–2026 the whole ranking is rebuilt from earlier seasons only. We then
count how many of its top 50 burned heavily that season, and how many of those were on steep ground and
near people. We compare that with ranking by the burn forecast alone and with chance.

This is a list of places to look at before the monsoon. It is not a landslide-risk map, and no landslide
data is used.

## 6. Checking detections against Sentinel-2 (`s2check.py`)

A fire detection shows fire activity, not burned area. For a sample of places we compare Sentinel-2
imagery before and after the 2026 burn season. There are three groups of 20 cells, all from Rangamati,
Bandarban and Khagrachhari:

- **priority:** the top 20 places on the 2026 list
- **fire:** 20 random other cells with at least one VIIRS fire day
- **control:** 20 random steep cells (90th-percentile slope of 15° or more) with no VIIRS vegetation-fire
  detection in the Feb–May 2025 and 2026 seasons. No detection does not
  prove there was no fire, and the controls are not matched on land cover.

**Imagery.** Every cell uses the same windows: before is 1 December to 31 January, after is 1 May to
10 June, so no group gets a longer dry-season gap. In each window the three clearest Sentinel-2 L2A scenes
(Earth Search) are combined with a per-pixel median. Clouds and shadows are masked with the scene
classification layer.

**Burn-scar test.** NBR = (B8A − B12) ÷ (B8A + B12) at 40 m, and dNBR = NBR before − NBR after, counted
over pixels that are clear in both composites. A cell passes when at least 5% of those pixels have
dNBR ≥ 0.27 (moderate burn severity or worse, Key and Benson, 2006). We also report results for dNBR
thresholds 0.10, 0.20 and 0.44, and cell shares 3% and 10%. The groups are compared with one-sided
Mann–Whitney tests.

This is spectral evidence consistent with burning, not an independent interpretation of the images.
`tools/blind_review/` holds the image pairs under neutral codes, with a labels sheet, so people can judge
them without knowing the group. The key is written to `data/processed/s2_blind_key.csv`, which is
kept out of the repository. Raters should receive only the `tools/blind_review/` folder, because
`data/processed/s2/` and `s2_check.parquet` hold the same images and groups under cell IDs.

## 7. Web app (`backend/app.py`, `frontend/`)

A FastAPI server serves the pipeline outputs as JSON (see `/docs`) and hosts the demo page. The page is
plain HTML, CSS and JavaScript with Leaflet for the map and hand-drawn SVG charts. The two yearly
charts have a table view and keyboard tooltips, the monthly calendar has a table view, and the map has a
ranked list as a keyboard alternative. The per-place history chart has pointer tooltips only.
