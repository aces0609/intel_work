"""Resolve each location to coordinates and its labor-shed counties."""
from __future__ import annotations

import math
import re
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


US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts",
    "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
_STATE_ABBR_BY_NAME = {n.lower(): a for a, n in US_STATES.items()}
_PLACE_SUFFIX = re.compile(r"\s+(city|town|village|borough|municipality|cdp|\(balance\))$", re.I)
_places: dict[tuple[str, str], tuple[float, float]] | None = None


def _norm_place(name: str) -> str:
    name = name.lower().replace("saint ", "st. ").replace("mt.", "mount").strip()
    return re.sub(r"\s+", " ", _PLACE_SUFFIX.sub("", name))


def place_coords(city: str, state: str) -> tuple[float, float] | None:
    """(lat, lon) of a US city from the Census gazetteer, or None. `state` is an abbreviation or name."""
    global _places
    if _places is None:
        df = _gaz("place")
        df["ALAND"] = df["ALAND"].astype(float)
        # when a name repeats within a state, prefer the incorporated place, then the larger one
        df = df.assign(_cdp=df["LSAD"].eq("57")).sort_values(["_cdp", "ALAND"], ascending=[True, False])
        _places = {}
        for usps, name, lat, lon in df[["USPS", "NAME", "INTPTLAT", "INTPTLONG"]].itertuples(index=False):
            _places.setdefault((_norm_place(name), usps), (lat, lon))
    st = state.strip()
    st = st.upper() if len(st) == 2 else _STATE_ABBR_BY_NAME.get(st.lower(), "")
    return _places.get((_norm_place(city), st))


def parse_city_state(text: str) -> tuple[str, str]:
    """Pull ('Palmdale', 'CA') out of text like 'Palmdale, CA, United States' or 'Palmdale, California'."""
    m = re.search(r"(?<![A-Za-z])([A-Z]{2}) - ([A-Za-z][A-Za-z .']+)$", (text or "").strip())  # Workday: 'USA - CA - Palmdale'
    if m and m.group(1) in US_STATES:
        return m.group(2).strip(), m.group(1)
    parts = [p.strip() for p in re.split(r"[,;|]", text or "") if p.strip()]
    for i in range(1, len(parts)):
        p = parts[i]
        if p.upper() in US_STATES:
            return parts[i - 1], p.upper()
        if p.lower() in _STATE_ABBR_BY_NAME:
            return parts[i - 1], _STATE_ABBR_BY_NAME[p.lower()]
        m = re.match(r"^([A-Z]{2})\b", p)
        if m and m.group(1) in US_STATES:
            return parts[i - 1], m.group(1)
    return "", ""
