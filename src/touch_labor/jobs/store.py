"""SQLite history of postings so reruns can show what is new, still open, or closed.

A posting is keyed on (company, site_key, external_id). Each successful fetch of a
(company, site) scope stamps last_seen on what it saw and closes (closed_on) anything in that
scope it did not see. A failed fetch changes nothing, so an outage never mass-closes postings.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date

from ..common import ROOT

DB_PATH = ROOT / "data" / "jobs" / "jobs.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS postings (
    company TEXT NOT NULL, site_key TEXT NOT NULL, external_id TEXT NOT NULL,
    platform TEXT, title TEXT, url TEXT, city TEXT, state TEXT, lat REAL, lon REAL,
    distance_miles REAL, location_text TEXT, employment_type TEXT, category TEXT,
    labor_class TEXT, pay_min REAL, pay_max REAL, pay_unit TEXT, posted TEXT, store TEXT,
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, closed_on TEXT,
    PRIMARY KEY (company, site_key, external_id)
);
CREATE TABLE IF NOT EXISTS runs (
    run_date TEXT NOT NULL, company TEXT NOT NULL, site_key TEXT NOT NULL,
    status TEXT NOT NULL, n_postings INTEGER, error TEXT, finished_at TEXT,
    PRIMARY KEY (run_date, company, site_key)
);
"""

COLS = ["company", "site_key", "external_id", "platform", "title", "url", "city", "state", "lat", "lon",
        "distance_miles", "location_text", "employment_type", "category", "labor_class", "pay_min",
        "pay_max", "pay_unit", "posted", "store"]


@contextmanager
def connect(path=DB_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    try:
        yield con
        con.commit()
    finally:
        con.close()


def record_scope(con: sqlite3.Connection, run_date: str, company: str, site_key: str, rows: list[dict]) -> tuple[int, int]:
    """Store a successful fetch. Returns (new, closed) counts."""
    seen = {r["external_id"] for r in rows}
    existing = {r["external_id"]: r["closed_on"] for r in con.execute(
        "SELECT external_id, closed_on FROM postings WHERE company=? AND site_key=?", (company, site_key))}
    new = 0
    for r in rows:
        if r["external_id"] not in existing:
            new += 1
        vals = {c: r.get(c) for c in COLS}
        con.execute(
            f"""INSERT INTO postings ({','.join(COLS)}, first_seen, last_seen, closed_on)
                VALUES ({','.join('?' * len(COLS))}, ?, ?, NULL)
                ON CONFLICT(company, site_key, external_id) DO UPDATE SET
                  {','.join(f'{c}=excluded.{c}' for c in COLS[3:])},
                  last_seen=excluded.last_seen, closed_on=NULL""",
            [vals[c] for c in COLS] + [run_date, run_date])
    closed = 0
    for ext, closed_on in existing.items():
        if ext not in seen and closed_on is None:
            con.execute("UPDATE postings SET closed_on=? WHERE company=? AND site_key=? AND external_id=?",
                        (run_date, company, site_key, ext))
            closed += 1
    con.execute("INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?)",
                (run_date, company, site_key, "ok", len(rows), None, date.today().isoformat()))
    return new, closed


def record_failure(con: sqlite3.Connection, run_date: str, company: str, site_key: str, error: str) -> None:
    con.execute("INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?)",
                (run_date, company, site_key, "error", None, error[:500], date.today().isoformat()))
