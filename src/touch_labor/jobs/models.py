"""Normalized posting model, labor-class classifier, and pay parsing."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field


@dataclass
class Posting:
    company: str
    platform: str
    external_id: str
    title: str
    url: str = ""
    city: str = ""
    state: str = ""
    lat: float | None = None
    lon: float | None = None
    location_text: str = ""
    employment_type: str = ""
    category: str = ""
    pay_min: float | None = None      # hourly dollars when pay_unit == "hour"
    pay_max: float | None = None
    pay_unit: str = ""                # "hour", "year" or ""
    posted: str = ""                  # ISO date when the source gives one
    store: str = ""                   # store / facility id when the source gives one
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        d = asdict(self)
        d.pop("extra")
        return d


# Ordered: first match wins. Professional/management titles are screened out first, then
# production and warehouse come before retail so "Stocking" in a distribution center is not
# mistaken for a sales-floor role.
_CLASS_RULES = [
    ("professional / other", r"\b(engineer\w*|developer|scientist|analyst|architect|consultant|software|"
                             r"attorney|counsel|recruiter|accountant|auditor|marketing|finance|financial|"
                             r"director|vice president|vp|manager|principal|researcher|designer|"
                             r"strategist|specialist ii|sr\.?)\b"),
    ("production / skilled trades", r"\b(assembl\w*|machinist|welder|fabricat\w*|production|manufactur\w*|"
                                    r"cnc|mechanic|electrician|technician|tech|inspector|operator|"
                                    r"maintenance|millwright|painter|composite\w*|sheet metal|tooling|"
                                    r"line lead|packaging|packager|tire|battery|lube)\b"),
    ("warehouse / logistics", r"\b(warehouse|distribution|fulfillment|forklift|material handler|picker|packer|"
                              r"freight|receiving|loader|unloader|shipping|logistics|order filler|"
                              r"yard|driver|delivery|inventory|replenishment|flow)\b"),
    ("retail / store", r"\b(cashier|associate|sales specialist|stocker|stocking|merchandis\w*|"
                       r"customer service|team member|team lead|pharmacy|deli|bakery|baker|produce|"
                       r"self.?checkout|front.?end|department supervisor|store|lot|cart|cafe|meat|"
                       r"rotisserie|custodian|janitor|supervisor)\b"),
]
_CLASS_RES = [(label, re.compile(rx, re.I)) for label, rx in _CLASS_RULES]
HOURLY_CLASSES = {"production / skilled trades", "warehouse / logistics", "retail / store"}


def classify(title: str, category: str = "") -> str:
    """Rough labor class from the job title only (categories are too coarse). Keyword based, so
    treat it as a guide; unmatched titles fall into 'professional / other'."""
    for label, rx in _CLASS_RES:
        if rx.search(title or ""):
            return label
    return "professional / other"


_MONEY = re.compile(r"\$?\s*([0-9][0-9,]*(?:\.[0-9]+)?)")


def parse_pay_range(text: str | None) -> tuple[float | None, float | None]:
    """'$20.00 - $21.00' -> (20.0, 21.0); a single value gives (v, v)."""
    if not text:
        return None, None
    vals = [float(m.replace(",", "")) for m in _MONEY.findall(text)]
    if not vals:
        return None, None
    return min(vals), max(vals)
