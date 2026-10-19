"""Turn the postings database into tables for the workbook and the CLI."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from .models import HOURLY_CLASSES
from .runner import load_sources
from .store import DB_PATH, connect

RECENT_DAYS = 7


def _postings() -> pd.DataFrame:
    with connect() as con:
        return pd.read_sql("SELECT * FROM postings", con)


def _runs() -> pd.DataFrame:
    with connect() as con:
        return pd.read_sql("SELECT * FROM runs", con)


def available() -> bool:
    return DB_PATH.exists() and not _postings().empty


def tables() -> dict[str, pd.DataFrame]:
    p = _postings()
    runs = _runs()
    today = date.today()
    cutoff = (today - timedelta(days=RECENT_DAYS)).isoformat()
    # "new" only counts once a scope has been fetched before; its first run is the baseline
    first_run = runs[runs["status"] == "ok"].groupby(["company", "site_key"])["run_date"].min().rename("baseline")
    p = p.join(first_run, on=["company", "site_key"])
    p["is_active"] = p["closed_on"].isna()
    p["is_new_recent"] = (p["first_seen"] >= cutoff) & (p["first_seen"] > p["baseline"])
    p["is_closed_recent"] = p["closed_on"].fillna("") >= cutoff
    p["is_hourly_class"] = p["labor_class"].isin(HOURLY_CLASSES)
    hourly_pay = p["pay_unit"].eq("hour") & p["pay_min"].notna()
    p["pay_mid"] = ((p["pay_min"] + p["pay_max"].fillna(p["pay_min"])) / 2).where(hourly_pay)

    def agg(df: pd.DataFrame) -> pd.Series:
        act = df[df["is_active"]]
        return pd.Series({
            "active_postings": len(act),
            "active_hourly_class": int(act["is_hourly_class"].sum()),
            "production_trades": int((act["labor_class"] == "production / skilled trades").sum()),
            "warehouse_logistics": int((act["labor_class"] == "warehouse / logistics").sum()),
            "retail_store": int((act["labor_class"] == "retail / store").sum()),
            # a median from a handful of postings misleads, so require at least 10 with stated pay
            "median_posted_hourly_pay": round(act["pay_mid"].median(), 2) if act["pay_mid"].notna().sum() >= 10 else None,
            "postings_with_pay": int(act["pay_mid"].notna().sum()),
            f"new_last_{RECENT_DAYS}d": int(df["is_new_recent"].sum()),
            f"closed_last_{RECENT_DAYS}d": int(df["is_closed_recent"].sum()),
        })

    by_company = p.groupby(["site_key", "company"]).apply(agg, include_groups=False).reset_index() if len(p) else pd.DataFrame()
    by_site = p.groupby("site_key").apply(agg, include_groups=False).reset_index() if len(p) else pd.DataFrame()
    for df in (by_site, by_company):
        for c in df.columns:
            if c not in ("site_key", "company", "median_posted_hourly_pay"):
                df[c] = df[c].astype(int)
    detail_cols = ["site_key", "company", "title", "labor_class", "pay_min", "pay_max", "pay_unit", "employment_type",
                   "category", "city", "state", "distance_miles", "store", "posted", "first_seen", "last_seen", "url"]
    detail = p[p["is_active"]].sort_values(["site_key", "company", "labor_class", "title"])[detail_cols]

    cov = pd.DataFrame([{
        "company": s["company"], "platform": s.get("platform"), "status": s["status"],
        "sites": ", ".join(s["sites"]), "hourly_relevance": s.get("hourly_relevance"),
        "careers_url": s.get("careers_url"), "notes": (s.get("notes") or "").strip(), "source_file": s["source_file"],
    } for s in load_sources()])
    last = runs.sort_values("run_date").groupby(["company", "site_key"]).tail(1)
    failures = last[last["status"] == "error"][["run_date", "company", "site_key", "error"]]
    return {"by_site": by_site, "by_company": by_company, "detail": detail, "coverage": cov, "failures": failures}
