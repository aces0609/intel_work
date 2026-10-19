"""ACS 5-year demographics (via Census Reporter, no key) and County Business Patterns (Census API key).

ACS comes from Census Reporter's public API, which republishes the Census Bureau's ACS tables,
because api.census.gov now needs an activated key. CBP still needs CENSUS_API_KEY
(environment or the gitignored .env) and is skipped with a warning if the key is rejected.
"""
from __future__ import annotations

import os

import pandas as pd
import requests

from .common import ROOT, UA, cached_json

ACS_RELEASE = "acs2024_5yr"  # ACS 2020-2024
ACS_LABEL = "ACS 2020-2024 5-year"
CR = f"https://api.censusreporter.org/1.0/data/show/{ACS_RELEASE}"
CBP_YEAR = 2022
CBP = f"https://api.census.gov/data/{CBP_YEAR}/cbp"

TABLES = ["B01001", "B01002", "B01003", "B15003", "B19013", "B23025", "B25064", "B25077"]
# B01001 (sex by age): ages 18-64 = male 007-019, female 031-043
AGE_18_64 = [f"B01001{i:03d}" for i in list(range(7, 20)) + list(range(31, 44))]
# B15003 (educational attainment, population 25+)
EDU_TOTAL = "B15003001"
EDU_LESS_HS = [f"B15003{i:03d}" for i in range(2, 17)]
EDU_HS = ["B15003017", "B15003018"]                          # diploma, GED
EDU_SOME_COLLEGE = ["B15003019", "B15003020", "B15003021"]   # some college, associate
EDU_BACH_PLUS = [f"B15003{i:03d}" for i in range(22, 26)]
POP = "B01003001"
LF_16PLUS, LF, LF_UNEMP = "B23025001", "B23025002", "B23025005"
COUNT_VARS = (
    [POP] + AGE_18_64 + [EDU_TOTAL] + EDU_LESS_HS + EDU_HS + EDU_SOME_COLLEGE + EDU_BACH_PLUS
    + [LF_16PLUS, LF, LF_UNEMP]
)
MEDIAN_VARS = {
    "B01002001": "median_age",
    "B19013001": "median_hh_income",
    "B25064001": "median_gross_rent",
    "B25077001": "median_home_value",
}


def _fetch_acs(geo_ids: list[str]) -> dict[str, dict[str, float]]:
    """Return {geo_id: {variable: estimate}} for all needed tables."""
    out: dict[str, dict[str, float]] = {}
    for g in geo_ids:
        data = cached_json(CR, {"table_ids": ",".join(TABLES), "geo_ids": g})
        est: dict[str, float] = {}
        for table in data["data"][g].values():
            est.update(table["estimate"])
        out[g] = est
    return out


def _metrics(e: dict[str, float]) -> dict:
    s = lambda cols: float(sum(e[c] for c in cols))
    total25 = e[EDU_TOTAL]
    return {
        "population": e[POP],
        "working_age_18_64": s(AGE_18_64),
        "working_age_share_pct": round(s(AGE_18_64) / e[POP] * 100, 1),
        "adults_25plus": total25,
        "pct_less_than_hs": round(s(EDU_LESS_HS) / total25 * 100, 1),
        "pct_hs_diploma_or_ged": round(s(EDU_HS) / total25 * 100, 1),
        "pct_some_college_or_associate": round(s(EDU_SOME_COLLEGE) / total25 * 100, 1),
        "pct_bachelors_plus": round(s(EDU_BACH_PLUS) / total25 * 100, 1),
        "labor_force": e[LF],
        "unemployment_rate_pct": round(e[LF_UNEMP] / e[LF] * 100, 1),
        "lfpr_pct_16plus": round(e[LF] / e[LF_16PLUS] * 100, 1),
    }


def _medians(e: dict[str, float]) -> dict:
    return {name: e.get(var) for var, name in MEDIAN_VARS.items()}


def acs_profile(loc: dict) -> dict:
    """ACS metrics for the city, its county, and the labor shed (counts summed over shed counties)."""
    shed_ids = [f"05000US{c['geoid']}" for c in loc["shed"]]
    ids = shed_ids + ([f"16000US{loc['place_geoid']}"] if loc.get("place_geoid") else [])
    data = _fetch_acs(ids)
    result: dict[str, dict] = {}
    if loc.get("place_geoid"):
        e = data[f"16000US{loc['place_geoid']}"]
        result["city"] = {**_metrics(e), **_medians(e)}
    ek = data[f"05000US{loc['county_geoid']}"]
    result["county"] = {**_metrics(ek), **_medians(ek)}
    summed = {v: sum(data[g][v] for g in shed_ids) for v in COUNT_VARS}
    result["shed"] = _metrics(summed)
    return result


# --- County Business Patterns (needs an activated Census API key) -------------------------
CBP_SECTORS = {"31-33": "manufacturing", "493": "warehousing", "44-45": "retail", "484": "trucking"}


def _key() -> str | None:
    key = os.environ.get("CENSUS_API_KEY")
    env = ROOT / ".env"
    if not key and env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("CENSUS_API_KEY="):
                key = line.split("=", 1)[1].strip()
    return key


def cbp_available() -> bool:
    key = _key()
    if not key:
        return False
    r = requests.get(CBP, params={"get": "ESTAB", "for": "us:1", "key": key}, headers=UA,
                     timeout=60, allow_redirects=False)
    return r.status_code == 200


def cbp_large_establishments(loc: dict) -> pd.DataFrame:
    """Count of establishments with 500+ employees (size classes 254 = 500-999, 260 = 1,000+)."""
    key = _key()
    rows = []
    for naics, label in CBP_SECTORS.items():
        for size, size_label in (("254", "500-999"), ("260", "1000+")):
            n = 0
            for c in loc["shed"]:
                data = cached_json(CBP, {
                    "get": "ESTAB", "for": f"county:{c['geoid'][2:]}", "in": f"state:{c['geoid'][:2]}",
                    "NAICS2017": naics, "EMPSZES": size, "key": key,
                })
                if data:
                    n += int(data[1][0])
            rows.append({"location_key": loc["key"], "sector": label, "size_class": size_label,
                         "establishments_in_shed": n})
    return pd.DataFrame(rows)
