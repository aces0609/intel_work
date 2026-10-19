"""Load sources, fetch every (company, site) scope, and record the results."""
from __future__ import annotations

import glob
from datetime import date

import yaml

from ..common import ROOT, load_locations
from ..geo import haversine_miles, parse_city_state, place_coords, resolve
from . import store
from .connectors import CONNECTORS
from .connectors_other import _in_states, other_kind
from .models import classify

DEFAULT_RADIUS_MILES = 25


def load_sources() -> list[dict]:
    """All data/job_sources_*.yaml entries, with `sites: all` expanded and a coverage status added."""
    all_keys = [loc["key"] for loc in load_locations()]
    out = []
    for path in sorted(glob.glob(str(ROOT / "data" / "job_sources_*.yaml"))):
        with open(path, encoding="utf-8") as f:
            for entry in yaml.safe_load(f) or []:
                entry = dict(entry)
                sites = entry.get("sites") or []
                entry["sites"] = all_keys if sites == "all" else [s for s in sites if s in all_keys]
                entry["source_file"] = path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
                platform = entry.get("platform")
                kind = other_kind(entry.get("config") or {}) if platform == "other" else platform
                entry["connector"] = kind if kind in CONNECTORS else None
                if entry["connector"] and entry.get("verified"):
                    entry["status"] = "covered"
                elif entry["connector"]:
                    entry["status"] = "skipped: not verified live"
                else:
                    entry["status"] = f"skipped: no connector for platform '{platform}'"
                out.append(entry)
    return out


def _sites() -> dict[str, dict]:
    sites = {}
    for loc in resolve(load_locations()):
        sites[loc["key"]] = {
            "key": loc["key"], "name": loc["name"], "state": loc["state"], "lat": loc["lat"], "lon": loc["lon"],
            "radius_miles": loc.get("job_radius_miles", DEFAULT_RADIUS_MILES),
            "states": loc.get("job_states", [loc["state"]]),
        }
    return sites


def run(site_keys: list[str] | None = None, companies: list[str] | None = None,
        run_date: str | None = None, exclude: list[str] | None = None) -> dict:
    """Fetch and store postings. Returns {"ok": n, "failed": n}."""
    run_date = run_date or date.today().isoformat()
    sites = _sites()
    wanted = [c.lower() for c in companies] if companies else None
    skip = {c.lower() for c in exclude or []}
    stats = {"ok": 0, "failed": 0}
    with store.connect() as con:
        for src in load_sources():
            if src["status"] != "covered" or (wanted and src["company"].lower() not in wanted):
                continue
            if src["company"].lower() in skip:
                continue
            connector = CONNECTORS[src["connector"]](src["company"])
            for key in src["sites"]:
                if site_keys and key not in site_keys:
                    continue
                site = sites[key]
                terms = (src.get("search_terms") or {}).get(key) or [site["name"]]
                try:
                    postings = connector.fetch(site, terms, src.get("config") or {})
                except Exception as e:  # one bad source must not stop the run
                    store.record_failure(con, run_date, src["company"], key, f"{type(e).__name__}: {e}")
                    con.commit()
                    stats["failed"] += 1
                    print(f"  FAIL {src['company']} @ {key}: {e}")
                    continue
                rows = []
                for p in postings:
                    keep, p.lat, p.lon, p.city, p.state = localize(
                        site, p.lat, p.lon, p.city, p.state, p.location_text)
                    if not keep:
                        continue
                    d = p.as_dict()
                    d["site_key"] = key
                    d["labor_class"] = classify(p.title, p.category)
                    dist = haversine_miles(site["lat"], site["lon"], p.lat, p.lon) if p.lat is not None else None
                    d["distance_miles"] = round(dist, 1) if dist is not None else None
                    rows.append(d)
                new, closed = store.record_scope(con, run_date, src["company"], key, rows)
                con.commit()
                stats["ok"] += 1
                print(f"  ok   {src['company']} @ {key}: {len(rows)} postings (new {new}, closed {closed})")
    export_snapshot(run_date)
    return stats


def localize(site: dict, lat, lon, city: str, state: str, location_text: str):
    """Decide whether a posting belongs to a site: it must fall inside the site's radius. Sources that
    give coordinates are checked against them (Home Depot's own radius filter returns jobs well outside
    it, even nationwide); for the rest, the state must fit and the city is geocoded from the Census
    gazetteer. Returns (keep, lat, lon, city, state)."""
    if lat is not None:
        inside = haversine_miles(site["lat"], site["lon"], lat, lon) <= site["radius_miles"] + 0.5
        return inside, lat, lon, city, state
    if not _in_states(site, location_text):
        return False, lat, lon, city, state  # e.g. Lancaster, PA
    c, st = (city, state) if city and state else parse_city_state(location_text)
    xy = place_coords(c, st) if c and st else None
    if xy and haversine_miles(site["lat"], site["lon"], xy[0], xy[1]) > site["radius_miles"]:
        return False, lat, lon, city, state
    if xy:
        return True, xy[0], xy[1], city or c, state or st
    return True, lat, lon, city, state  # city could not be placed: keep, the text matched


def export_snapshot(run_date: str) -> None:
    """Write the active postings as data/jobs/snapshots/active_<date>.csv.gz so history survives
    even though the SQLite file itself is not committed."""
    from .report import tables  # local import: report imports runner

    out = ROOT / "data" / "jobs" / "snapshots"
    out.mkdir(parents=True, exist_ok=True)
    tables()["detail"].to_csv(out / f"active_{run_date}.csv.gz", index=False)
