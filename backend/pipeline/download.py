"""Step 0: download NASA FIRMS yearly country CSVs (MODIS 2000+, VIIRS S-NPP 2012+). No API key needed."""
import urllib.request

from backend import config as C

SOURCES = {"modis": C.MODIS_YEARS, "viirs-snpp": C.VIIRS_YEARS}


def run():
    C.RAW.mkdir(parents=True, exist_ok=True)
    for sensor, years in SOURCES.items():
        for y in years:
            name = f"{sensor}_{y}_{C.COUNTRY}.csv"
            path = C.RAW / name
            if path.exists():
                continue
            url = f"https://firms.modaps.eosdis.nasa.gov/data/country/{sensor}/{y}/{name}"
            try:
                urllib.request.urlretrieve(url, path)
                print("ok  ", name, path.stat().st_size // 1024, "KB")
            except Exception as e:
                print("MISS", name, e)


if __name__ == "__main__":
    run()
