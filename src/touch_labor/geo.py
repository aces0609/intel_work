"""Resolve each location to coordinates and its labor-shed counties."""
from __future__ import annotations

import math
import zipfile

import pandas as pd

from .common import download

GAZ = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gazetteer"
SHED_MILES = 40


def _gaz(kind: str) -> pd.DataFrame:
    zpath = download(f"{GAZ}/2023_Gaz_{kind}_national.zip")
    with zipfile.ZipFile(zpath) as z:
        with z.open(z.namelist()[0]) as f:
            df = pd.read_csv(f, sep="\t", dtype=str, encoding="latin-1")
    df.columns = [c.strip() for c in df.columns]
    df["INTPTLAT"] = df["INTPTLAT"].astype(float)
    df["INTPTLONG"] = df["INTPTLONG"].astype(float)
    return df


def haversine_miles(lat1, lon1, lat2, lon2):
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dlmb = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def resolve(locations: list[dict]) -> list[dict]:
    """Add lat/lon, county name, and shed counties to each location dict."""
    places = _gaz("place").set_index("GEOID")
    counties = _gaz("counties")
    cby = counties.set_index("GEOID")
    out = []
    for loc in locations:
        loc = dict(loc)
        if loc.get("place_geoid"):
            row = places.loc[loc["place_geoid"]]
            loc["lat"], loc["lon"] = float(row["INTPTLAT"]), float(row["INTPTLONG"])
            loc["place_label"] = row["NAME"]
        crow = cby.loc[loc["county_geoid"]]
        loc["county_name"] = crow["NAME"]
        d = counties.apply(
            lambda r: haversine_miles(loc["lat"], loc["lon"], r["INTPTLAT"], r["INTPTLONG"]),
            axis=1,
        )
        shed = counties[(d <= SHED_MILES) | (counties["GEOID"] == loc["county_geoid"])]
        loc["shed"] = [
            {"geoid": g, "name": f"{n}, {s}", "miles": round(float(d[i]), 1)}
            for i, (g, n, s) in zip(shed.index, shed[["GEOID", "NAME", "USPS"]].itertuples(index=False))
        ]
        loc["shed"].sort(key=lambda x: x["miles"])
        out.append(loc)
    return out
