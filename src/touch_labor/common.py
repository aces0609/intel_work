"""Shared paths, config loading, and cached downloads."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "output"
UA = {"User-Agent": "intel_work touch-labor research"}


def load_locations() -> list[dict]:
    with open(ROOT / "locations.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)["locations"]


def download(url: str, name: str | None = None) -> Path:
    """Download url into data/raw once; later calls reuse the file."""
    RAW.mkdir(parents=True, exist_ok=True)
    path = RAW / (name or url.rsplit("/", 1)[-1])
    if not path.exists():
        r = requests.get(url, headers=UA, timeout=300)
        r.raise_for_status()
        path.write_bytes(r.content)
    return path


def cached_json(url: str, params: dict | None = None) -> object:
    """GET json with an on-disk cache keyed on url+params (API key excluded)."""
    RAW.mkdir(parents=True, exist_ok=True)
    keyless = {k: v for k, v in (params or {}).items() if k != "key"}
    h = hashlib.sha1(json.dumps([url, keyless], sort_keys=True).encode()).hexdigest()[:16]
    path = RAW / f"api_{h}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    r = requests.get(url, params=params, headers=UA, timeout=120)
    r.raise_for_status()
    if r.history and "key" in r.url:  # Census redirects to missing_key / invalid_key pages
        raise SystemExit(f"Census API rejected the key ({r.url}); check CENSUS_API_KEY / activation email")
    data = [] if r.status_code == 204 else r.json()  # 204 = no data for this query
    path.write_text(json.dumps(data), encoding="utf-8")
    return data
