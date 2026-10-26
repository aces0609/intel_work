"""Print the MEMO.md tables (side-by-side and active postings) from the built workbook.

Run after `build`:  uv --directory <repo> run python -m touch_labor.memo_tables
Paste the output over the matching tables in MEMO.md, then update the prose that cites them.
"""
from __future__ import annotations

import pandas as pd

from .common import OUT, load_locations


def _n(v, fmt="{:,.0f}") -> str:
    return "n/a" if pd.isna(v) else fmt.format(v)


def main() -> None:
    x = pd.read_excel(OUT / "touch_labor_comparison.xlsx", sheet_name=None)
    s = x["Summary"]
    shed_col = next(c for c in s.columns if c.startswith("Labor-shed population"))
    bach = next(c for c in s.columns if c.startswith("Shed % bachelor"))

    print("| Site | City pop. | County pop. | Pop. within ~40 mi | HS or less | Bachelor's+ | Unemployment | "
          "Production median $/hr | Production LQ | Laborer / mover $/hr | Retail $/hr |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for _, r in s.iterrows():
        print(f"| {r['Resolved to'].replace(' (assumed)', '')} | {_n(r['City population'])} | "
              f"{_n(r['County population'])} | {_n(r[shed_col])} | {_n(r['Shed % HS diploma or less'], '{:.0f}%')} | "
              f"{_n(r[bach], '{:.0f}%')} | "
              f"{_n(r['Shed unemployment %'], '{:.1f}%')} | {_n(r['Production median $/hr'], '{:.2f}')} | "
              f"{_n(r['Production location quotient'], '{:.2f}')} | "
              f"{_n(r['Hand laborer/material mover median $/hr'], '{:.2f}')} | "
              f"{_n(r['Retail salesperson median $/hr'], '{:.2f}')} |")

    if "Jobs by site" not in x:
        return
    by_site = x["Jobs by site"].set_index("site_key")
    by_co = x["Jobs by company"]
    print()
    print("| Site | Active postings | Hourly-class | Production / trades | Warehouse | Retail / store | "
          "Biggest hourly-class posters (hourly-class / all) |")
    print("|---|---|---|---|---|---|---|")
    for loc in load_locations():
        b = by_site.loc[loc["key"]]
        top = by_co[by_co["site_key"] == loc["key"]].sort_values("active_hourly_class", ascending=False).head(4)
        names = ", ".join(f"{c.split(' (')[0]} {h}/{a}" for c, h, a in
                          zip(top["company"], top["active_hourly_class"], top["active_postings"]))
        c = {k: int(b[k]) for k in ("active_postings", "active_hourly_class", "production_trades",
                                    "warehouse_logistics", "retail_store")}
        print(f"| {loc['name']}, {loc['state']} | {c['active_postings']:,} | {c['active_hourly_class']:,} | "
              f"{c['production_trades']:,} | {c['warehouse_logistics']:,} | {c['retail_store']:,} | {names} |")


if __name__ == "__main__":
    main()
