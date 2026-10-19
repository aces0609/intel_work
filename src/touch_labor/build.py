"""Build output/touch_labor_comparison.xlsx.

Run:  uv --directory <repo> run python -m touch_labor.build [--skip-census]
"""
from __future__ import annotations

import sys

import pandas as pd

from . import bls, census, education
from .common import OUT, ROOT, load_locations
from .geo import SHED_MILES, resolve
from .jobs import report as jobs_report

SOURCES = [
    ("Population, age, education, labor force, income, rent, home value",
     f"Census {census.ACS_LABEL} estimates (tables B01001, B01002, B01003, B15003, B19013, B23025, B25064, B25077), retrieved via Census Reporter's API"),
    ("Large establishments (500+ employees) by sector",
     f"Census County Business Patterns {census.CBP_YEAR}, summed over the labor-shed counties (only if a Census API key is active)"),
    ("Occupational employment and wages",
     "BLS OEWS May 2025, metropolitan / nonmetropolitan area containing the site's county (https://www.bls.gov/oes/)"),
    ("Colleges and trade schools", "IPEDS institutional directory 2022 via the Urban Institute Education Data API, within 30 miles"),
    ("Named large employers", "Web research per row; each row carries its source URL, as-of year and confidence (data/employers_*.csv)"),
]
ASSUMPTIONS = [
    f"Labor shed = all counties whose centroid is within {SHED_MILES} straight-line miles of the site, plus the site's own county. This is not drive time.",
    "Rocket Center: requested as 'Rocket Center, WY'; no such place exists, so Rocket Center, WV (Mineral County) is assumed. Unincorporated, so no city-level ACS.",
    "Iuka: requested as 'Luka, MS'; Iuka, MS (Tishomingo County) is assumed.",
    "ACS values for very small places (Corinne, Iuka) have wide margins of error; treat city-level figures as rough.",
    "OEWS wages are regional (metro / nonmetro area), not city-specific. Los Angeles-area sites (Palmdale, Redondo Beach) share one OEWS area.",
    "Large-employer headcounts come from varied sources and years; some are projections or multi-site totals (see notes column). The list is not exhaustive.",
    "For Palmdale, Redondo Beach, San Diego and Sunnyvale the 40-mile shed collapses to one or a few huge counties (e.g. all of Los Angeles County), which overstates the local pool; use the city and county rows there. Palmdale's real pool is the Antelope Valley, not all of LA County.",
    "County Business Patterns counts of 500+ employee establishments are zero when none exist or the cell is suppressed, and a single large site is counted once regardless of headcount.",
    "Active job postings come from employers' own career sites via data/job_sources_*.yaml (see src/touch_labor/jobs/README.md): a posting is demand, not headcount, and only covered employers appear. Hourly pay is shown only where the posting states it, and a site median needs at least 10 such postings.",
    "Medians (age, income, rent, home value) are shown for city and county only; they cannot be summed across the shed.",
]


def _load_employers() -> pd.DataFrame:
    parts = [pd.read_csv(p, dtype=str).fillna("") for p in sorted((ROOT / "data").glob("employers_*.csv"))]
    return pd.concat(parts, ignore_index=True)


def _top_employers(emp: pd.DataFrame, key: str, n: int = 3) -> str:
    e = emp[(emp["location_key"] == key) & (emp["touch_labor_overlap"] == "high")]
    return "; ".join(e["employer"].head(n))


def main(skip_census: bool = False) -> None:
    locs = resolve(load_locations())
    emp = _load_employers()
    use_cbp = not skip_census and census.cbp_available()
    if not skip_census and not use_cbp:
        print("warning: Census API key missing/not activated; skipping County Business Patterns")
    demo_rows, edu_rows, wage_frames, school_frames, cbp_frames, summary = [], [], [], [], [], []

    for loc in locs:
        prof = None if skip_census else census.acs_profile(loc)
        code, title, wages = bls.oews_for(loc)
        schools = education.nearby_schools(loc)
        sch = education.summarize(schools)
        school_frames.append(schools)
        wage_frames.append(wages)
        w = wages.set_index("soc")
        shed_names = "; ".join(f"{c['name']} ({c['miles']} mi)" for c in loc["shed"])

        row = {
            "Location (as requested)": loc["as_given"],
            "Resolved to": f"{loc['name']}, {loc['state']}" + (" (assumed)" if loc.get("assumed") else ""),
            "County": loc["county_name"],
            "OEWS area": f"{title} ({code})",
        }
        if prof:
            c, k, s = prof.get("city", {}), prof["county"], prof["shed"]
            for scope, d in (("city", c), ("county", k), ("shed", s)):
                if d:
                    demo_rows.append({"location": loc["as_given"], "scope": scope, **d})
            row.update({
                "City population": c.get("population"),
                "County population": k["population"],
                f"Labor-shed population ({SHED_MILES} mi)": s["population"],
                "Shed working-age (18-64)": s["working_age_18_64"],
                "Shed % HS diploma or less": round(s["pct_less_than_hs"] + s["pct_hs_diploma_or_ged"], 1),
                "Shed % some college / associate": s["pct_some_college_or_associate"],
                "Shed % bachelor's+": s["pct_bachelors_plus"],
                "Shed unemployment %": s["unemployment_rate_pct"],
                "County median HH income": k["median_hh_income"],
                "County median rent": k["median_gross_rent"],
            })
            if use_cbp:
                cbp = census.cbp_large_establishments(loc)
                cbp_frames.append(cbp)
                big = cbp.groupby("sector")["establishments_in_shed"].sum()
                row.update({
                    "Shed mfg estabs 500+": int(big.get("manufacturing", 0)),
                    "Shed warehousing estabs 500+": int(big.get("warehousing", 0)),
                    "Shed retail estabs 500+": int(big.get("retail", 0)),
                })
            edu_rows.append({"location": loc["as_given"], "scope": "shed",
                             **{k2: v for k2, v in s.items() if k2.startswith("pct_")}, **sch})
        row.update({
            "Production jobs (OEWS area)": w.loc["51-0000", "employment"],
            "Production location quotient": w.loc["51-0000", "location_quotient"],
            "Production median $/hr": w.loc["51-0000", "wage_median_hr"],
            "Assembler median $/hr": w.loc["51-2090", "wage_median_hr"],
            "Hand laborer/material mover median $/hr": w.loc["53-7062", "wage_median_hr"],
            "Retail salesperson median $/hr": w.loc["41-2031", "wage_median_hr"],
            "2-yr / trade schools within 30 mi": sch["two_year_or_trade_within_30mi"],
            "Public 2-yr colleges within 30 mi": sch["public_two_year_within_30mi"],
            "Main high-overlap employers": _top_employers(emp, loc["key"]),
            "Labor-shed counties": shed_names,
            "Notes": loc.get("note", "").strip(),
        })
        summary.append(row)

    jobs = jobs_report.tables() if jobs_report.available() else None
    if jobs:
        by_site = jobs["by_site"].set_index("site_key")
        for row, loc in zip(summary, locs):
            if loc["key"] in by_site.index:
                b = by_site.loc[loc["key"]]
                row["Active postings, tracked employers"] = int(b["active_postings"])
                row["...of which production/warehouse/retail"] = int(b["active_hourly_class"])
                row["...production / skilled trades"] = int(b["production_trades"])
                row["Median posted hourly pay"] = b["median_posted_hourly_pay"]

    OUT.mkdir(exist_ok=True)
    path = OUT / "touch_labor_comparison.xlsx"
    sheets = {"Summary": pd.DataFrame(summary)}
    if demo_rows:
        sheets["Demographics"] = pd.DataFrame(demo_rows)
        sheets["Education"] = pd.DataFrame(edu_rows)
        if cbp_frames:
            sheets["Large estabs (CBP)"] = pd.concat(cbp_frames, ignore_index=True)
    sheets["Labor & wages"] = pd.concat(wage_frames, ignore_index=True)
    sheets["Schools"] = pd.concat(school_frames, ignore_index=True)
    sheets["Large employers"] = emp
    if jobs:
        sheets["Jobs by site"] = jobs["by_site"]
        sheets["Jobs by company"] = jobs["by_company"]
        sheets["Active jobs"] = jobs["detail"]
        sheets["Job sources"] = jobs["coverage"]
    sheets["Sources & assumptions"] = pd.DataFrame(
        [{"Item": "Source", "Detail": f"{a}: {b}"} for a, b in SOURCES]
        + [{"Item": "Assumption", "Detail": a} for a in ASSUMPTIONS]
    )
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        for name, df in sheets.items():
            df.to_excel(xw, sheet_name=name[:31], index=False)
            ws = xw.sheets[name[:31]]
            ws.freeze_panes = "B2" if name != "Sources & assumptions" else "A2"
            for i, col in enumerate(df.columns, 1):
                width = min(60, max(len(str(col)), *(len(str(v)) for v in df[col].head(60))) + 2)
                ws.column_dimensions[ws.cell(1, i).column_letter].width = width
    print(f"wrote {path}")


if __name__ == "__main__":
    main(skip_census="--skip-census" in sys.argv)
