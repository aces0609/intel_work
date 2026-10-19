"""BLS OEWS (May 2025) employment and wages by occupation for each site's OEWS area.

Inputs, saved in data/raw by hand because bls.gov blocks scripted downloads:
  area_definitions_m2025.xlsx  (https://www.bls.gov/oes/area_definitions_m2025.xlsx)
  oesm25ma/MSA_M2025_dl.xlsx and BOS_M2025_dl.xlsx (metro / nonmetro areas, from
                               https://www.bls.gov/oes/special-requests/oesm25ma.zip)
"""
from __future__ import annotations

import pandas as pd

from .common import RAW

OCCUPATIONS = {
    "00-0000": "All occupations",
    "51-0000": "Production (all)",
    "51-2090": "Misc. assemblers & fabricators",
    "51-2092": "Team assemblers",
    "51-4041": "Machinists",
    "51-4121": "Welders, cutters, solderers",
    "51-9061": "Inspectors, testers, sorters",
    "53-0000": "Transportation & material moving (all)",
    "53-7062": "Laborers & freight/stock movers, hand",
    "53-7065": "Stockers & order fillers",
    "41-2031": "Retail salespersons",
    "41-2011": "Cashiers",
}


def _area_defs() -> pd.DataFrame:
    ad = pd.read_excel(RAW / "area_definitions_m2025.xlsx", dtype=str)
    ad["geoid"] = ad["FIPS Code"] + ad["County Code"]
    return ad.set_index("geoid")


def _oews() -> pd.DataFrame:
    cache = RAW / "oesm25ma" / "oews_selected.pkl"
    if cache.exists():
        return pd.read_pickle(cache)
    frames = []
    for name in ("MSA_M2025_dl.xlsx", "BOS_M2025_dl.xlsx"):  # metro areas, nonmetro areas
        df = pd.read_excel(RAW / "oesm25ma" / name, dtype=str)
        frames.append(df[df["OCC_CODE"].isin(OCCUPATIONS)])
    out = pd.concat(frames, ignore_index=True)
    out.to_pickle(cache)
    return out


def oews_for(loc: dict) -> tuple[str, str, pd.DataFrame]:
    """Return (area code, area title, rows) for the OEWS area containing the site's county."""
    ad = _area_defs()
    row = ad.loc[loc["county_geoid"]]
    code, title = row["May 2025 Area Code"], row["May 2025 Area Title"]
    df = _oews()
    sub = df[df["AREA"] == code].copy()
    for c in ["TOT_EMP", "H_MEAN", "H_MEDIAN", "H_PCT25", "H_PCT75", "LOC_QUOTIENT"]:
        sub[c] = pd.to_numeric(sub[c], errors="coerce")  # '*', '#' -> NaN (suppressed)
    sub["occupation"] = sub["OCC_CODE"].map(OCCUPATIONS)
    total = sub.loc[sub["OCC_CODE"] == "00-0000", "TOT_EMP"]
    total = float(total.iloc[0]) if len(total) else float("nan")
    sub["share_of_jobs_pct"] = (sub["TOT_EMP"] / total * 100).round(2)
    sub.insert(0, "location_key", loc["key"])
    sub.insert(1, "oews_area", f"{title} ({code})")
    cols = ["location_key", "oews_area", "OCC_CODE", "occupation", "TOT_EMP", "share_of_jobs_pct",
            "LOC_QUOTIENT", "H_PCT25", "H_MEDIAN", "H_PCT75"]
    return code, title, sub[cols].rename(columns={
        "OCC_CODE": "soc", "TOT_EMP": "employment", "LOC_QUOTIENT": "location_quotient",
        "H_PCT25": "wage_p25_hr", "H_MEDIAN": "wage_median_hr", "H_PCT75": "wage_p75_hr"})
