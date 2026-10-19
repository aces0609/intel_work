"""One connector per job-board platform. All use public, unauthenticated endpoints that the
companies' own careers pages call; none bypasses a login, CAPTCHA or bot check.

Every connector exposes  fetch(site, terms, cfg) -> list[Posting]  where
  site  = {"key", "name", "state", "lat", "lon", "radius_miles"}
  terms = city names for this site (used by platforms with no radius search)
  cfg   = the source's `config` block from the sources YAML
"""
from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta

from ..geo import haversine_miles
from .http import Client
from .models import Posting, parse_pay_range

MAX_PAGES = 400  # safety cap per query
_COMMON_WORDS = {"CITY", "SAN", "LOS", "ST", "ST.", "SAINT", "MOUNT", "MT", "FORT", "FT", "NORTH", "SOUTH",
                 "EAST", "WEST", "NEW", "LAKE", "BEACH", "PORT", "SPRINGS", "HEIGHTS", "VALLEY", "PARK"}


def _dist(site: dict, lat, lon) -> float | None:
    if lat is None or lon is None:
        return None
    return haversine_miles(site["lat"], site["lon"], float(lat), float(lon))


def _iso(ms_or_s: float | None) -> str:
    if not ms_or_s:
        return ""
    v = float(ms_or_s)
    if v > 1e11:  # milliseconds
        v /= 1000
    return datetime.fromtimestamp(v, tz=UTC).date().isoformat()


class Connector:
    platform = ""

    min_interval = 0.25  # seconds between requests; polite default

    def __init__(self, company: str):
        self.company = company
        self.client = Client(self.min_interval)  # own session / cookie jar / rate window per source

    def fetch(self, site: dict, terms: list[str], cfg: dict) -> list[Posting]:
        raise NotImplementedError


# --------------------------------------------------------------------------- Walmart
class WalmartConnector(Connector):
    """careers.walmart.com talks to /api/graphql with a persisted query. A `jobId == '*'`
    search returns an aggregate of every store with open jobs and its coordinates; we pick the
    stores near each site, then pull each store city's jobs with a `city == 'X'` search."""

    platform = "walmart"
    min_interval = 1.0  # Walmart closes the connection (HTTP 444) when hit faster than this for long
    URL = "https://careers.walmart.com/api/graphql"
    QUERY_ID = "b0467c1f-f578-4261-9280-0ea4614f251c"

    def __init__(self, company: str):
        super().__init__(company)
        self.client.s.headers.update({"Origin": "https://careers.walmart.com",
                                      "Referer": "https://careers.walmart.com/us/en/results"})
        self._stores: list[dict] | None = None

    def _search(self, refined: str, page: int = 0) -> dict:
        ctx = {"refined_query": refined, "direct_search": True, "locale": "en_US", "sort": "relevance",
               "active_tab": "jobs", "management_levels": [], "content_page": 0,
               "future_roles_page": 0, "job_page": page}
        body = {"queryId": self.QUERY_ID, "variables": {"chatRequest": {
            "messages": [{"role": "user", "content": [{"type": "text", "text": "search"}]}],
            "thread_id": "S-0-touch-labor", "channel": "job_search",
            "context": {"job_search_context": ctx}}}, "headers": {}}
        r = self.client.post(self.URL, json=body)
        return r.json()["data"]["jobSearchAssistant"]["tool_messages"][0]["artifact"]

    def _all_stores(self) -> list[dict]:
        if self._stores is None:
            self._stores = self._search("jobId == '*'").get("store_jobs_aggregation_result") or []
        return self._stores

    def fetch(self, site, terms, cfg):
        near = [s for s in self._all_stores()
                if _dist(site, s["geoLocation"]["lat"], s["geoLocation"]["lon"]) <= site["radius_miles"]]
        expected: dict[tuple[str, str], int] = {}  # open jobs per city according to the store aggregate
        for s in near:
            expected[(s["city"], s["state"])] = expected.get((s["city"], s["state"]), 0) + s["count"]
        seen: dict[str, Posting] = {}
        for (city, state), want in sorted(expected.items()):
            if "'" in city:
                continue  # query language has no documented escape; rare
            # `city == 'X'` is a loose word match: 'ELLICOTT CITY' also matches every city with 'CITY'
            # in it (15,000+ jobs), though exact matches rank first. So page only until the city's
            # expected job count (from the aggregate) is reached or a page has no exact match. The
            # query language rejects some words ('UNION' gets HTTP 444), so on error retry with the
            # city's most distinctive single word.
            words = [w for w in city.split() if w not in _COMMON_WORDS] or city.split()
            for query_term in (city, max(words, key=len)):
                got, page = 0, 0
                try:
                    while page < MAX_PAGES:
                        jobs = self._search(f"city == '{query_term}'", page).get("jobs") or []
                        hits = [j for j in jobs if (j.get("city") or "").upper() == city and j.get("state") == state]
                        for j in hits:
                            d = _dist(site, j.get("latitude"), j.get("longitude"))
                            if j["job_id"] not in seen and d is not None and d <= site["radius_miles"]:
                                seen[j["job_id"]] = self._posting(j)
                        got += len(hits)
                        page += 1
                        if not hits or got >= want:
                            break
                    break  # this query worked
                except RuntimeError:
                    if query_term == max(words, key=len):
                        # e.g. 'UNION CITY': the query language rejects the word 'UNION' outright
                        print(f"  note Walmart: could not search {city}, {state} ({want} jobs expected); skipped")
        return list(seen.values())

    def _posting(self, j: dict) -> Posting:
        unit = "hour" if (j.get("payFrequency") or "").lower().startswith("hour") else "year"
        pmin, pmax = j.get("minPay") or None, j.get("maxPay") or None
        if not pmin:  # Sam's Club and some stores leave minPay at 0 and fill payRange instead
            for pr in j.get("payRange") or []:
                try:
                    pmin, pmax = float(pr["min"]), float(pr["max"])
                    break
                except (KeyError, TypeError, ValueError):
                    continue
        return Posting(
            company=self.company, platform=self.platform, external_id=j["job_id"],
            title=j.get("jobPostingTitle") or j.get("title") or "",
            url="https://careers.walmart.com/us/en/results",
            city=(j.get("city") or "").title(), state=j.get("state") or "",
            lat=j.get("latitude"), lon=j.get("longitude"),
            location_text=f"{(j.get('city') or '').title()}, {j.get('state')}",
            employment_type=", ".join(j.get("employmentTypes") or []),
            category=(j.get("categories") or [""])[0],
            pay_min=pmin, pay_max=pmax, pay_unit=unit if pmin else "",
            posted=_iso(j.get("jobPostingStartDate")),
            store=f"{j.get('bannerName') or j.get('brand') or ''} #{j.get('storeNumber') or ''}".strip(),
        )


# --------------------------------------------------------------------------- Home Depot
class HomeDepotConnector(Connector):
    """careers.homedepot.com runs on Findly, which fronts a Google Cloud Talent search."""

    platform = "findly"
    URL = "https://jobsapi-google.m-cloud.io/api/job/search"
    COMPANY = "companies/8454851f-07b7-4e4c-9b5f-00e0ffbfcb09"
    PORTALS = ["THD-GH-Construction", "THD-GH-Bell", "THD-GH-Umi", "THD-GH-Cancostile", "THD-GH-Jarrell",
               "THD-GH-MVP", "THD-GH-Victoria", "KBR-5032", "Workday", "Paycom"]
    PAGE = 50

    def fetch(self, site, terms, cfg):
        company = cfg.get("company_name", self.COMPANY)
        filt = "(" + " OR ".join(f'ats_portalid="{p}"' for p in cfg.get("portals", self.PORTALS)) + ")"
        loc = {"distanceInMiles": site["radius_miles"],
               "latLng": {"latitude": site["lat"], "longitude": site["lon"]}, "address": site["name"]}
        out: dict[str, Posting] = {}
        offset = 0
        for _ in range(MAX_PAGES):
            r = self.client.get(self.URL, params={
                "pageSize": self.PAGE, "offset": offset, "companyName": company,
                "customAttributeFilter": filt, "locationFilter[]": json.dumps(loc), "orderBy": ""})
            data = r.json()
            rows = data.get("searchResults") or []
            for row in rows:
                j = row["job"]
                key = str(j.get("ref") or j.get("id"))
                lo = j.get("primary_location") or [None, None]
                lat, lon = (lo[1], lo[0]) if len(lo) == 2 else (None, None)
                pmin, pmax = parse_pay_range(j.get("salary"))
                unit = "hour" if (j.get("job_type") or "").lower() == "hourly" else ("year" if pmin else "")
                out[key] = Posting(
                    company=self.company, platform=self.platform, external_id=key,
                    title=j.get("title") or "", url=j.get("url") or "https://careers.homedepot.com/job-search-results/",
                    city=j.get("primary_city") or "", state=j.get("primary_state") or "", lat=lat, lon=lon,
                    location_text=f"{j.get('primary_city')}, {j.get('primary_state')}",
                    employment_type=j.get("job_type") or "", category=j.get("primary_category") or "",
                    pay_min=pmin, pay_max=pmax, pay_unit=unit,
                    posted=(j.get("open_date") or j.get("created_at") or "")[:10], store=j.get("brand") or "")
            offset += self.PAGE
            if not rows or offset >= (data.get("totalHits") or 0):
                break
        return list(out.values())


# --------------------------------------------------------------------------- Amazon
class AmazonConnector(Connector):
    """amazon.jobs search.json (corporate and operations roles).
    Limitation: hourly fulfillment-center associate roles are posted on hiring.amazon.com, a
    separate system this connector does not read."""

    platform = "amazon"
    URL = "https://www.amazon.jobs/en/search.json"
    PAGE = 100

    def fetch(self, site, terms, cfg):
        out: dict[str, Posting] = {}
        offset = 0
        for _ in range(MAX_PAGES):
            r = self.client.get(self.URL, params={
                "result_limit": self.PAGE, "offset": offset, "latitude": site["lat"],
                "longitude": site["lon"], "radius": f"{site['radius_miles'] * 1.609:.0f}km"})
            data = r.json()
            jobs = data.get("jobs") or []
            for j in jobs:
                best = None  # nearest of the posting's listed locations
                for raw in j.get("locations") or []:
                    loc = json.loads(raw) if isinstance(raw, str) else raw
                    try:
                        la, lo = (float(x) for x in loc["coordinates"].split(","))
                    except (KeyError, ValueError):
                        continue
                    d = _dist(site, la, lo)
                    if best is None or d < best[0]:
                        best = (d, la, lo, loc)
                if best is None or best[0] > site["radius_miles"]:
                    continue
                _, la, lo, loc = best
                out[str(j["id_icims"])] = Posting(
                    company=self.company, platform=self.platform, external_id=str(j["id_icims"]),
                    title=j["title"], url="https://www.amazon.jobs" + j["job_path"],
                    city=loc.get("city", ""), state=loc.get("region", ""), lat=la, lon=lo,
                    location_text=loc.get("normalizedLocation") or loc.get("location", ""),
                    employment_type=j.get("job_schedule_type") or "",
                    category=j.get("job_category") or "", posted=_parse_long_date(j.get("posted_date")),
                    store=", ".join(loc.get("buildingCodeList") or []))
            offset += self.PAGE
            if not jobs or offset >= (data.get("hits") or 0):
                break
        return list(out.values())


def _parse_long_date(s: str | None) -> str:
    if not s:
        return ""
    try:
        return datetime.strptime(re.sub(r"\s+", " ", s.strip()), "%B %d, %Y").date().isoformat()
    except ValueError:
        return ""


# --------------------------------------------------------------------------- text-match platforms
def _match(terms: list[str], *texts: str) -> bool:
    blob = " ".join(t for t in texts if t).lower()
    return any(t.lower() in blob for t in terms)


class WorkdayConnector(Connector):
    platform = "workday"
    PAGE = 20

    def fetch(self, site, terms, cfg):
        tenant, wd, board = cfg["tenant"], cfg.get("wd", "wd1"), cfg["site"]
        url = f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{board}/jobs"
        base = f"https://{tenant}.{wd}.myworkdayjobs.com/{board}"
        out: dict[str, Posting] = {}
        for term in terms:
            offset, total = 0, None
            for _ in range(MAX_PAGES):
                data = self.client.post(url, json={"appliedFacets": {}, "limit": self.PAGE,
                                                   "offset": offset, "searchText": term}).json()
                page = data.get("jobPostings") or []
                if total is None:          # `total` can flip to 0 on later pages (seen on Boeing)
                    total = data.get("total", 0)
                for j in page:
                    if "externalPath" not in j or "title" not in j:
                        continue           # restricted-visibility stub
                    loc = j.get("locationsText") or ""
                    if not _match(terms, loc):
                        continue           # e.g. "2 Locations": cannot place it, skip
                    ext = (j.get("bulletFields") or [j["externalPath"]])[0]
                    out[ext] = Posting(
                        company=self.company, platform=self.platform, external_id=ext, title=j["title"],
                        url=base + j["externalPath"], location_text=loc,
                        employment_type=j.get("timeType") or "", posted=_workday_posted(j.get("postedOn")))
                offset += self.PAGE
                if not page or offset >= (total or 0):
                    break
        return list(out.values())


def _workday_posted(text: str | None) -> str:
    """'Posted Today' / 'Posted Yesterday' / 'Posted 5 Days Ago' -> ISO date; '30+' is a bound, left blank."""
    if not text:
        return ""
    t = text.lower()
    today = date.today()
    if "today" in t:
        return today.isoformat()
    if "yesterday" in t:
        return (today - timedelta(days=1)).isoformat()
    m = re.search(r"(\d+)\s+days? ago", t)
    return (today - timedelta(days=int(m.group(1)))).isoformat() if m else ""


class GreenhouseConnector(Connector):
    platform = "greenhouse"

    def fetch(self, site, terms, cfg):
        data = self.client.get(f"https://boards-api.greenhouse.io/v1/boards/{cfg['token']}/jobs").json()
        out = []
        for j in data.get("jobs", []):
            loc = (j.get("location") or {}).get("name") or ""
            if _match(terms, loc):
                out.append(Posting(
                    company=self.company, platform=self.platform, external_id=str(j["id"]), title=j["title"],
                    url=j.get("absolute_url", ""), location_text=loc,
                    posted=(j.get("first_published") or j.get("updated_at") or "")[:10]))
        return out


class LeverConnector(Connector):
    platform = "lever"

    def fetch(self, site, terms, cfg):
        data = self.client.get(f"https://api.lever.co/v0/postings/{cfg['company']}", params={"mode": "json"}).json()
        out = []
        for j in data:
            cat = j.get("categories") or {}
            loc = cat.get("location") or ""
            if _match(terms, loc):
                out.append(Posting(
                    company=self.company, platform=self.platform, external_id=j["id"], title=j["text"],
                    url=j.get("hostedUrl", ""), location_text=loc, employment_type=cat.get("commitment") or "",
                    category=cat.get("team") or "", posted=_iso(j.get("createdAt"))))
        return out


class SmartRecruitersConnector(Connector):
    platform = "smartrecruiters"
    PAGE = 100

    def fetch(self, site, terms, cfg):
        company = cfg["company"]
        out: dict[str, Posting] = {}
        for term in terms:
            offset = 0
            for _ in range(MAX_PAGES):
                data = self.client.get(f"https://api.smartrecruiters.com/v1/companies/{company}/postings",
                                       params={"limit": self.PAGE, "offset": offset, "q": term}).json()
                rows = data.get("content") or []
                for j in rows:
                    loc = j.get("location") or {}
                    text = f"{loc.get('city', '')}, {loc.get('region', '')}"
                    if not _match(terms, text):
                        continue
                    out[j["id"]] = Posting(
                        company=self.company, platform=self.platform, external_id=j["id"], title=j["name"],
                        url=f"https://jobs.smartrecruiters.com/{company}/{j['id']}", city=loc.get("city", ""),
                        state=loc.get("region", ""), location_text=text,
                        category=(j.get("function") or {}).get("label", ""),
                        employment_type=(j.get("typeOfEmployment") or {}).get("label", ""),
                        posted=(j.get("releasedDate") or "")[:10])
                offset += self.PAGE
                if not rows or offset >= (data.get("totalFound") or 0):
                    break
        return list(out.values())


class EightfoldConnector(Connector):
    platform = "eightfold"
    PAGE = 25

    def fetch(self, site, terms, cfg):
        host, domain = cfg["host"], cfg["domain"]
        out: dict[str, Posting] = {}
        for term in terms:
            start = 0
            for _ in range(MAX_PAGES):
                data = self.client.get(f"https://{host}/api/pcsx/search", params={
                    "domain": domain, "query": "", "location": term, "start": start, "num": self.PAGE}).json()
                d = data.get("data") or {}
                rows = d.get("positions") or []
                for j in rows:
                    locs = "; ".join(j.get("locations") or [])
                    if not _match(terms, locs):
                        continue
                    out[str(j["id"])] = Posting(
                        company=self.company, platform=self.platform, external_id=str(j["id"]), title=j["name"],
                        url=f"https://{host}{j.get('positionUrl', '')}", location_text=locs,
                        category=j.get("department") or "", employment_type=j.get("workLocationOption") or "",
                        posted=_iso(j.get("postedTs")))
                start += self.PAGE
                if not rows or start >= (d.get("count") or 0):
                    break
        return list(out.values())


CONNECTORS: dict[str, type[Connector]] = {
    "walmart": WalmartConnector,
    "homedepot": HomeDepotConnector,
    "amazon": AmazonConnector,
    "workday": WorkdayConnector,
    "greenhouse": GreenhouseConnector,
    "lever": LeverConnector,
    "smartrecruiters": SmartRecruitersConnector,
    "eightfold": EightfoldConnector,
}
