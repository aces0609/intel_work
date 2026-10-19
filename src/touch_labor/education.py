"""Nearby colleges and trade schools from the IPEDS directory (Urban Institute API)."""
from __future__ import annotations

import json

import pandas as pd

from .common import download
from .geo import haversine_miles

IPEDS_URL = "https://educationdata.urban.org/api/v1/college-university/ipeds/directory/2022/"
RADIUS_MILES = 30

# IPEDS sector codes
SECTOR_LABEL = {
    1: "4-year public", 2: "4-year private nonprofit", 3: "4-year for-profit",
    4: "2-year public", 5: "2-year private nonprofit", 6: "2-year for-profit",
    7: "<2-year public", 8: "<2-year private nonprofit", 9: "<2-year for-profit",
}


def _directory() -> pd.DataFrame:
    path = download(IPEDS_URL, "ipeds_dir_2022.json")
    df = pd.DataFrame(json.loads(path.read_text(encoding="utf-8"))["results"])
    df = df[(df["currently_active_ipeds"] == 1) & df["sector"].isin(SECTOR_LABEL)]
    return df.dropna(subset=["latitude", "longitude"])


def nearby_schools(loc: dict) -> pd.DataFrame:
    d = _directory().copy()
    d["miles"] = [
        haversine_miles(loc["lat"], loc["lon"], la, lo) for la, lo in zip(d["latitude"], d["longitude"])
    ]
    d = d[d["miles"] <= RADIUS_MILES].copy()
    d["type"] = d["sector"].map(SECTOR_LABEL)
    d["location_key"] = loc["key"]
    d["miles"] = d["miles"].round(1)
    cols = ["location_key", "inst_name", "type", "city", "miles", "url_school"]
    return d.sort_values("miles")[cols].rename(columns={"inst_name": "institution", "url_school": "website"})


def summarize(schools: pd.DataFrame) -> dict:
    """Counts of the schools most relevant to production-labor pipelines."""
    t = schools["type"]
    two_year_or_less = t.str.startswith(("2-year", "<2-year"))
    return {
        "schools_within_30mi": len(schools),
        "two_year_or_trade_within_30mi": int(two_year_or_less.sum()),
        "public_two_year_within_30mi": int((t == "2-year public").sum()),
        "four_year_within_30mi": int(t.str.startswith("4-year").sum()),
    }
