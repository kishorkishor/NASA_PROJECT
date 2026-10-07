# Burn Before the Rain, in simple words

This page explains the project without the technical detail. The full method is in
[METHODS.md](METHODS.md) and every check we ran is in [VALIDATION.md](VALIDATION.md).

## In one paragraph

NASA has two satellite fire records for Bangladesh. The older one (MODIS) starts in 2000. The newer one
(VIIRS) starts in 2012 and sees much more detail. If you simply join them, it looks like fires tripled in
2012, but that jump comes from the new camera, not from more burning. We converted the newer record into
the units of the older one, so the 24 years can be read as one story. Read that way, detected burning in
the hills has gone down since 2003. We then used the corrected record, together with slope and population,
to list 16 places in the Chittagong hills that are worth checking before the monsoon. A forecast picks the
places for next season, and a live watch adds new fires from NASA every 3 hours.

## The problem

**Two cameras, two levels of detail.** MODIS sees the ground in squares about 1 km wide. VIIRS sees squares
375 m wide, about seven times smaller in area. So one fire often shows up in VIIRS as several dots, and VIIRS
also catches small fires that MODIS misses.

**A fake jump.** Join the two records end to end (MODIS until 2011, VIIRS from 2012) and Bangladesh seems to
have about 3.3 times more fire after 2012. Nothing changed on the ground. The satellite changed.

**Why it matters here.** In the Chittagong hill districts, farmers clear and burn hillsides for jhum
(shifting cultivation) from February to May, just before the monsoon. The same hills have deadly landslides.
Anyone reading the joined record would think burning is getting much worse. It is not.

## What we did

### 1. Made the two records comparable

- We counted VIIRS fires per 1 km square per day. If VIIRS sees four dots in the same square on the same day,
  that counts once, just like one MODIS pixel would.
- We looked at 2012 to 2023, when both satellites were flying and MODIS was working normally, and learned how
  many MODIS fires one of those VIIRS square-days is worth. We did this separately for the hills and the
  plains, and for the burning season and the rest of the year. In the hills during the burning season, one
  VIIRS square-day is worth 0.383 MODIS fires.
- We used those numbers to turn every VIIRS year into "MODIS units".

We also took care of two known problems in the MODIS record. The Aqua satellite was in safe mode from
31 March to 17 April 2022, so those days were removed from both satellites. And Aqua's afternoon pass has
been drifting later since 2023, so 2024 to 2026 were not used for learning.

### 2. Tested it on years it had never seen

- We learned the conversion from 2012 to 2019 only, then predicted 2020 to 2023. The typical yearly error was
  8.0%. Simply joining the records was off by 609%.
- We repeated the learning 2,000 times on reshuffled sets of years to get a range for the result.

### 3. The result

- After converting, the jump is gone. Fire activity after 2012 is about **0.70 times** the 2003 to 2011 level
  (range 0.63 to 0.77), not 3.3 times.
- In the hills during the burning season, detected burning has **gone down** since 2003 (Kendall trend test,
  tau = -0.49, p = 0.0005).
- We built a 24-season fire history for every ~2 km square in the five hill districts. With the simple join,
  986 of these squares looked like they were burning more since 2012. After converting, only 27 did.

### 4. A list of places to check before the monsoon

Each ~2 km square in the hills gets a score from three things: fire this season, how steep the burned ground
is, and how many people live nearby.

- **16 places** stay in the top 50 in at least 80% of the 54 different ways we tried to set up the score.
  14 are in Bandarban and 2 in Rangamati. About 5,931 people live in these squares.
- **Tested on the past.** For each season from 2022 to 2026 we rebuilt the list using only earlier data. On
  average its top 50 held 14.8 squares that then burned heavily on steep ground near people. Ranking by slope
  and people alone, without any fire history, gives 8.8, so part of the gain comes simply from favouring steep,
  lived-in ground. Picking 50 squares at random gives about 1.2.
- **Checked with photos from space.** We compared Sentinel-2 images from before (December to January) and
  after (May to June) the 2026 burning season. 15 of our top 20 places show a burn scar, against 10 of 20
  random fire places and 2 of 20 places where no fire was detected. The clear difference is against places
  with no fire; the gap between our places and random fire places could still be chance. This measures the
  colour change; a blind visual check by people is prepared but not done yet.

### 5. A forecast for next season

Burning in the hills follows habits: a square that burned heavily in many past seasons is likely to burn
again. For the 2027 season we ranked every square by how often it burned heavily (fire on 3 or more days
between February and May) in the last 15 seasons, then weighed it by slope and people, the same way as the
2026 list. That gives **19 places to check before February 2027**, 16 of them in Bandarban.

We tested this on 2022 to 2026, each time using only the seasons before. Of the top 50 squares, about 26
burned heavily that season. A machine-learning model (gradient boosting) trained on the same history plus
terrain and population caught about 25, and 50 random squares about 5. The simple rule did as well as the
machine-learning model, so it is the default.

### 6. A live fire watch

Every 3 hours a small program on GitHub downloads the last 7 days of fire detections from NASA FIRMS. VIIRS
detections appear there about 3 hours after each satellite pass. The program places them on the same ~2 km
squares and marks fires on steep ground near homes, or on one of the lists. Spots that NASA's own archive
marks as factories or gas flares are left out. The explainer page shows the result as "This week". Outside the
burning season it is mostly quiet.

### 7. Things anyone can open

- A web app with the corrected fire calendar, the map and the history of every place. The main
  [README](../README.md) says how to run it.
- A one-page interactive explainer, [showcase/index.html](../showcase/index.html), that walks through the two
  satellites, the fake jump, the fix and the places to check.

## What this is not

- **Not a landslide forecast.** We did not use any landslide records. A freshly burned steep slope near homes
  is a reason to go and look, not a prediction that it will slide.
- **Not perfect fire counts.** MODIS is our reference, not the truth. Both satellites miss small fires and
  fires under cloud. The 0.63 to 0.77 range covers only the conversion step.
- **Not blame.** Jhum is a traditional way of farming. The list is meant for safety checks done together with
  hill communities, and it has not yet been reviewed by people who work there.
- Satellites cannot see hill-cutting for houses, which causes many landslide deaths in Chittagong and
  Cox's Bazar.

## The data

| What | Source | Used for |
|---|---|---|
| Fire detections from MODIS on the Aqua satellite, 2003 to June 2026 | **NASA FIRMS**, product MCD14ML | the older fire record |
| Fire detections from VIIRS on Suomi NPP, 375 m, 2012 to June 2026 | **NASA FIRMS**, product VNP14IMGTML | the newer, more detailed record |
| Fire detections of the last 7 days | **NASA FIRMS**, VIIRS Suomi NPP near real time | the live fire watch |
| Elevation | **SRTM** (Shuttle Radar Topography Mission, NASA and NGA), through AWS Terrain Tiles | how steep the ground is, relief shading |
| Before and after photos | Copernicus Sentinel-2, through Earth Search | burn-scar check |
| People | WorldPop 2020 | how many people live near a fire |
| District borders | geoBoundaries | hills or plains, district names |
| Village and town names | OpenStreetMap | naming the places |

## Words you might see

- **Fire detection:** a spot the satellite saw as hot enough to be a fire.
- **Pixel:** the smallest square a satellite can see.
- **Harmonize:** convert two records into the same units so they can be compared.
- **Calibration:** learning that conversion from the years when both satellites were flying.
- **Cell-day:** one ~1 km square on one day with at least one VIIRS fire.
- **Jhum:** shifting cultivation; a hillside is cleared and burned before planting.
- **dNBR:** a number from Sentinel-2 that measures how much a place changed the way burned land changes.
- **Near real time:** data published within a few hours of the satellite pass.
- **Gradient boosting:** a common machine-learning method that combines many small decision trees.

Burn Before the Rain, NASA Space Apps Challenge 2026, Team feb, Dhaka.
