"""Connectors for career-site systems that the sources YAML labels `platform: other`
(Oracle Recruiting Cloud, Phenom, Jibe, UKG, Teamtailor, ApplicantPro, ADP, Paylocity).

The connector kind is inferred from the source's config text (see other_kind) and its ids
(host, site number, board id) are pulled out of that config. Importing this module registers
the connectors in CONNECTORS.
"""
from __future__ import annotations

import json
import re

from .connectors import CONNECTORS, MAX_PAGES, Connector, _match
from ..geo import US_STATES
from .models import Posting

STATE_NAMES = {"CA": "California", "MD": "Maryland", "UT": "Utah", "WV": "West Virginia", "FL": "Florida",
               "MS": "Mississippi", "AL": "Alabama", "TN": "Tennessee", "PA": "Pennsylvania"}


def _blob(cfg: dict) -> str:
    return json.dumps(cfg)


def _in_states(site: dict, text: str) -> bool:
    """True if the text names one of the site's states, or names no US state at all."""
    states = site.get("states") or [site["state"]]
    low = text.lower()
    for s in states:
        if re.search(rf"\b{s}\b", text) or STATE_NAMES.get(s, "~").lower() in low:
            return True
    any_state = any(re.search(rf"\b{a}\b", text) for a in US_STATES) or any(
        n.lower() in low for n in US_STATES.values())
    return not any_state


def _place_ok(site: dict, terms: list[str], *texts: str) -> bool:
    text = " ".join(t for t in texts if t)
    return _match(terms, text) and _in_states(site, text)


_PAY = re.compile(r"\$\s?(\d{1,3}(?:\.\d{1,2})?)\s*(?:-|to)\s*\$?\s?(\d{1,3}(?:\.\d{1,2})?)\s*(?:/|per\s)?\s*(?:hour|hr)",
                  re.I)


def _pay_from_text(text: str) -> tuple[float | None, float | None, str]:
    m = _PAY.search(text or "")
    return (float(m.group(1)), float(m.group(2)), "hour") if m else (None, None, "")


class OracleConnector(Connector):
    """Oracle Recruiting Cloud 'candidate experience' REST API. Keyword searches are fuzzy and an
    empty keyword returns nothing, so each city term is searched and the location re-checked."""

    platform = "oracle"
    PAGE = 200

    def fetch(self, site, terms, cfg):
        b = _blob(cfg)
        host = cfg.get("host") or re.search(r"https://([\w.-]+)/hcmRestApi", b).group(1)
        number = cfg.get("site_number") or re.search(r"siteNumber=(\w+)", b).group(1)
        url = f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
        out: dict[str, Posting] = {}

        def page(keyword: str, offset: int) -> tuple[int, list[dict]]:
            finder = (f"findReqs;siteNumber={number},limit={self.PAGE},offset={offset},"
                      f"keyword={keyword},sortBy=POSTING_DATES_DESC")
            data = self.client.get(url, params={"onlyData": "true", "expand": "requisitionList.secondaryLocations",
                                                "finder": finder}).json()
            item = (data.get("items") or [{}])[0]
            return item.get("TotalJobsCount") or 0, item.get("requisitionList") or []

        def keep(rows: list[dict]) -> None:
            for j in rows:
                loc = j.get("PrimaryLocation") or ""
                if not _place_ok(site, terms, loc):
                    continue
                ext = str(j["Id"])
                out[ext] = Posting(
                    company=self.company, platform=self.platform, external_id=ext, title=j.get("Title", ""),
                    url=f"https://{host}/hcmUI/CandidateExperience/en/sites/{number}/job/{ext}",
                    location_text=loc, category=j.get("JobFamily") or "",
                    employment_type=j.get("WorkplaceType") or "", posted=(j.get("PostedDate") or "")[:10])

        # Small boards: an empty keyword lists everything, so page it all. Big boards (and some that
        # return nothing for an empty keyword): search each city term, because the API stops paging
        # near 10,000 results and the keyword match is fuzzy (the location is re-checked above).
        total, rows = page("", 0)
        if 0 < total <= self.PAGE * 10:
            keep(rows)
            for offset in range(self.PAGE, total, self.PAGE):
                keep(page("", offset)[1])
        else:
            for term in terms:
                for offset in range(0, self.PAGE * 10, self.PAGE):
                    _, rows = page(term, offset)
                    keep(rows)
                    if len(rows) < self.PAGE:
                        break
        return list(out.values())


class PhenomConnector(Connector):
    """Phenom People career sites: one `refineSearch` widget call per state, then a city filter."""

    platform = "phenom"
    SIZE = 50

    def fetch(self, site, terms, cfg):
        host = re.search(r"https://([\w.-]+)/widgets", _blob(cfg)).group(1)
        out: dict[str, Posting] = {}
        for st in site.get("states") or [site["state"]]:
            offset, total = 0, None
            for _ in range(60):
                body = {"lang": "en_us", "deviceType": "desktop", "country": "us", "pageName": "search-results",
                        "ddoKey": "refineSearch", "sortBy": "", "subsearch": "", "from": offset, "jobs": True,
                        "counts": True, "all_fields": ["category", "country", "state", "city", "type"],
                        "size": self.SIZE, "clearAll": False, "jdsource": "facets", "isSliderEnable": False,
                        "pageId": cfg.get("page_id", "page11"), "siteType": "external", "keywords": "",
                        "global": True, "selected_fields": {"state": [STATE_NAMES[st]]}, "locationData": {}}
                data = self.client.post(f"https://{host}/widgets", json=body).json().get("refineSearch") or {}
                jobs = (data.get("data") or {}).get("jobs") or []
                total = data.get("totalHits") if total is None else total
                for j in jobs:
                    loc = j.get("location") or f"{j.get('city')}, {j.get('state')}"
                    if not _place_ok(site, terms, j.get("city") or "", loc):
                        continue
                    ext = str(j.get("jobId") or j.get("reqId") or j.get("jobSeqNo"))
                    out[ext] = Posting(
                        company=self.company, platform=self.platform, external_id=ext, title=j.get("title", ""),
                        url=j.get("applyUrl") or f"https://{host}/us/en/job/{ext}", city=j.get("city") or "",
                        state=j.get("state") or "", location_text=loc, category=j.get("category") or "",
                        employment_type=j.get("type") or "", posted=(j.get("postedDate") or "")[:10])
                offset += self.SIZE
                if not jobs or offset >= (total or 0):
                    break
        return list(out.values())


class JibeConnector(Connector):
    """Jibe (iCIMS) career sites: GET /api/jobs?location=..&limit=..&page=.."""

    platform = "jibe"

    def fetch(self, site, terms, cfg):
        host = re.search(r"https://([\w.-]+)/api/jobs", _blob(cfg)).group(1)
        out: dict[str, Posting] = {}
        for term in terms:
            for page in range(1, 20):
                data = self.client.get(f"https://{host}/api/jobs", params={
                    "location": f"{term}, {site['state']}", "limit": 100, "page": page}).json()
                rows = data.get("jobs") or []
                for r in rows:
                    j = r.get("data") or r
                    if not _place_ok(site, terms, j.get("city") or "", f"{j.get('city')}, {j.get('state')}"):
                        continue
                    ext = str(j.get("req_id") or j.get("slug") or j.get("id"))
                    cats = j.get("categories")
                    out[ext] = Posting(
                        company=self.company, platform=self.platform, external_id=ext, title=j.get("title", ""),
                        url=f"https://{host}/jobs/{j.get('slug') or ext}", city=j.get("city") or "",
                        state=j.get("state") or "", lat=j.get("latitude"), lon=j.get("longitude"),
                        location_text=j.get("location_name") or "", employment_type=j.get("employment_type") or "",
                        category=", ".join(map(str, cats)) if isinstance(cats, list) else "",
                        posted=(j.get("posted_date") or "")[:10])
                if len(rows) < 100:
                    break
        return list(out.values())


class UkgConnector(Connector):
    """UKG / UltiPro recruiting job boards."""

    platform = "ukg"
    PAGE = 50

    def fetch(self, site, terms, cfg):
        m = re.search(r"https://([\w.-]+)/(\w+)/JobBoard/([\w-]+)", _blob(cfg))  # ultipro.com or *.rec.pro.ukg.net
        base = f"https://{m.group(1)}/{m.group(2)}/JobBoard/{m.group(3)}"
        out: dict[str, Posting] = {}
        skip, total = 0, None
        for _ in range(MAX_PAGES):
            body = {"opportunitySearch": {"Top": self.PAGE, "Skip": skip, "QueryString": "",
                                          "OrderBy": [{"Value": "postedDateDesc", "PropertyName": "PostedDate",
                                                       "Ascending": False}], "Filters": []},
                    "matchCriteria": {"PreferredJobs": [], "Criteria": [], "SearchScore": 0, "Relationship": 0,
                                      "PreferredScore": 0, "FilterByScore": False}}
            data = self.client.post(f"{base}/JobBoardView/LoadSearchResults", json=body).json()
            rows = data.get("opportunities") or []
            total = data.get("totalCount") if total is None else total
            for j in rows:
                addr = ((j.get("Locations") or [{}])[0].get("Address") or {})
                city, st = addr.get("City") or "", (addr.get("State") or {}).get("Code") or ""
                if not _place_ok(site, terms, f"{city}, {st}"):
                    continue
                out[j["Id"]] = Posting(
                    company=self.company, platform=self.platform, external_id=j["Id"], title=j.get("Title", ""),
                    url=f"{base}/OpportunityDetail?opportunityId={j['Id']}", city=city, state=st,
                    location_text=f"{city}, {st}", category=j.get("JobCategoryName") or "",
                    posted=(j.get("PostedDate") or "")[:10])
            skip += self.PAGE
            if not rows or skip >= (total or 0):
                break
        return list(out.values())


def _rfc_date(s: str | None) -> str:
    from email.utils import parsedate_to_datetime

    try:
        return parsedate_to_datetime(s).date().isoformat() if s else ""
    except (TypeError, ValueError):
        return ""


class TeamtailorRssConnector(Connector):
    """Teamtailor jobs.rss feed; pay is usually stated in the description text."""

    platform = "teamtailor"

    def fetch(self, site, terms, cfg):
        import xml.etree.ElementTree as ET

        url = re.search(r"https://[\w.-]+/jobs\.rss", _blob(cfg)).group(0)
        root = ET.fromstring(self.client.get(url).content)
        out = []
        for item in root.iter("item"):
            locs = [n.text or "" for n in item.iter() if n.tag.endswith("}name") and n.text]
            loc = "; ".join(locs)
            if not _match(terms, loc):
                continue
            pmin, pmax, unit = _pay_from_text(re.sub(r"<[^>]+>", " ", item.findtext("description") or ""))
            out.append(Posting(
                company=self.company, platform=self.platform,
                external_id=item.findtext("guid") or item.findtext("link") or "", title=item.findtext("title") or "",
                url=item.findtext("link") or "", location_text=loc, pay_min=pmin, pay_max=pmax, pay_unit=unit,
                posted=_rfc_date(item.findtext("pubDate"))))
        return out


class ApplicantProConnector(Connector):
    platform = "applicantpro"

    def fetch(self, site, terms, cfg):
        m = re.search(r"https://([\w-]+)\.applicantpro\.com/core/jobs/(\d+)", _blob(cfg))
        sub, dom = m.group(1), m.group(2)
        data = self.client.get(f"https://{sub}.applicantpro.com/core/jobs/{dom}", params={"getParams": "{}"},
                               headers={"Accept": "application/json"}).json()
        out = []
        for j in (data.get("data") or {}).get("jobs") or []:
            city, st = j.get("city") or "", j.get("abbreviation") or ""
            if _place_ok(site, terms, f"{city}, {st}"):
                out.append(Posting(
                    company=self.company, platform=self.platform, external_id=str(j["id"]), title=j.get("title", ""),
                    url=f"https://{sub}.applicantpro.com/jobs/{j['id']}.html", city=city, state=st,
                    location_text=f"{city}, {st}", category=j.get("classification") or ""))
        return out


class AdpMyJobsConnector(Connector):
    platform = "adp"
    PAGE = 100

    def fetch(self, site, terms, cfg):
        orgoid = re.search(r"orgoid[:\s\"']+(\w{12,})", _blob(cfg)).group(1)
        out: dict[str, Posting] = {}
        skip = 0
        for _ in range(MAX_PAGES):
            data = self.client.get("https://myjobs.adp.com/public/staffing/v1/job-requisitions",
                                   params={"$top": self.PAGE, "$skip": skip},
                                   headers={"orgoid": orgoid, "Accept": "application/json"}).json()
            rows = data.get("jobRequisitions") or []
            for j in rows:
                a = ((j.get("requisitionLocations") or [{}])[0].get("address") or {})
                city = a.get("cityName") or ""
                st = (a.get("countrySubdivisionLevel1") or {}).get("codeValue") or ""
                if _place_ok(site, terms, f"{city}, {st}"):
                    ext = str(j.get("reqId") or j.get("itemID"))
                    out[ext] = Posting(
                        company=self.company, platform=self.platform, external_id=ext, title=j.get("jobTitle", ""),
                        url="https://myjobs.adp.com/", city=city, state=st, location_text=f"{city}, {st}",
                        posted=(j.get("postDate") or "")[:10])
            skip += self.PAGE
            if len(rows) < self.PAGE:
                break
        return list(out.values())


class PaylocityConnector(Connector):
    platform = "paylocity"

    def fetch(self, site, terms, cfg):
        url = re.search(r"https://recruiting\.paylocity\.com/Recruiting/Jobs/All/[\w-]+", _blob(cfg)).group(0)
        html = self.client.get(url).text
        m = re.search(r"window\.pageData\s*=\s*(\{.*?\});\s*(?:window|</script>)", html, re.S)
        data = json.loads(m.group(1)) if m else {}
        out = []
        for j in data.get("Jobs") or []:
            loc = j.get("LocationName") or ""
            if _match(terms, loc):
                ext = str(j.get("JobId"))
                out.append(Posting(
                    company=self.company, platform=self.platform, external_id=ext, title=j.get("JobTitle", ""),
                    url=f"https://recruiting.paylocity.com/Recruiting/Jobs/Details/{ext}", location_text=loc,
                    posted=(j.get("PublishedDate") or "")[:10]))
        return out


CONNECTORS.update({
    "oracle": OracleConnector, "phenom": PhenomConnector, "jibe": JibeConnector, "ukg": UkgConnector,
    "teamtailor": TeamtailorRssConnector, "applicantpro": ApplicantProConnector, "adp": AdpMyJobsConnector,
    "paylocity": PaylocityConnector,
})

_OTHER_KINDS = [("taleo", None),  # Oracle Taleo is a different API from Oracle Recruiting Cloud; no connector yet
                ("oracle", "oracle"), ("phenom", "phenom"), ("jibe", "jibe"), ("ukg", "ukg"), ("ultipro", "ukg"),
                ("teamtailor", "teamtailor"), ("applicantpro", "applicantpro"), ("adp", "adp"),
                ("paylocity", "paylocity")]


def other_kind(cfg: dict) -> str | None:
    """Infer the connector for a `platform: other` source from its config text."""
    tag = f"{cfg.get('type', '')} {cfg.get('ats', '')}".strip().lower() or _blob(cfg).lower()
    for needle, kind in _OTHER_KINDS:
        if needle in tag:
            return kind
    return None
