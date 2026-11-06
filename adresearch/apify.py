"""Thin Apify client for the curious_coder Facebook Ad Library scraper.

Pricing (checked Nov 2026): $0.75 per 1,000 ads + $0.00005 per run.
Only the standard library is used so scheduled runs need no installs.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

API = "https://api.apify.com/v2"
ACTOR = "curious_coder~facebook-ads-library-scraper"
PRICE_PER_1000_ADS = 0.75


class ApifyError(RuntimeError):
    pass


def search_url(keyword: str, country: str = "US", active_only: bool = True) -> str:
    """Build the Ad Library website search URL the actor scrapes."""
    params = {
        "active_status": "active" if active_only else "all",
        "ad_type": "all",
        "country": country,
        "q": keyword,
        "search_type": "keyword_unordered",
        "media_type": "all",
    }
    return "https://www.facebook.com/ads/library/?" + urllib.parse.urlencode(params)


def _token() -> str:
    # In cloud sessions the network proxy injects the real token for
    # api.apify.com, so a placeholder is fine there. Locally, set APIFY_TOKEN.
    return os.environ.get("APIFY_TOKEN") or "proxy-injected"


def _request(method: str, path: str, body: dict | None = None, timeout: int = 60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        API + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {_token()}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode() or "null")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:500]
        raise ApifyError(f"Apify {method} {path} -> HTTP {e.code}: {detail}") from e


def run_search(url: str, limit: int, poll_seconds: int = 10, max_wait: int = 1800) -> list[dict]:
    """Run the actor for one search URL and return its dataset items."""
    run = _request(
        "POST",
        f"/acts/{ACTOR}/runs",
        {
            "urls": [{"url": url}],
            "limitPerSource": limit,
            "count": limit,
            "scrapeAdDetails": False,
        },
    )["data"]
    run_id = run["id"]
    waited = 0
    while run["status"] in ("READY", "RUNNING"):
        if waited >= max_wait:
            _request("POST", f"/actor-runs/{run_id}/abort")
            raise ApifyError(f"Run {run_id} timed out after {max_wait}s")
        time.sleep(poll_seconds)
        waited += poll_seconds
        run = _request("GET", f"/actor-runs/{run_id}")["data"]
    if run["status"] != "SUCCEEDED":
        raise ApifyError(f"Run {run_id} ended with status {run['status']}")
    items = _request(
        "GET", f"/datasets/{run['defaultDatasetId']}/items?clean=true&format=json", timeout=300
    )
    # The actor can return up to ~30 more than asked; don't keep (or pay
    # attention to) more than we wanted.
    return (items or [])[: limit + 30]


def run_keywords(
    keywords: list[str],
    limit: int,
    country: str = "US",
    active_only: bool = True,
    workers: int = 3,
    log=print,
) -> dict[str, list[dict]]:
    """Scrape each keyword (a few in parallel). Returns {keyword: items}.

    A failed keyword is logged and skipped so one bad search doesn't sink
    the whole weekly run.
    """

    def one(kw: str):
        try:
            items = run_search(search_url(kw, country, active_only), limit)
            log(f"  ✓ {kw!r}: {len(items)} ads")
            return kw, items
        except Exception as e:  # noqa: BLE001 - keep going on any failure
            log(f"  ✗ {kw!r}: {e}")
            return kw, []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(pool.map(one, keywords))


def estimate_cost(n_keywords: int, limit: int) -> float:
    return n_keywords * limit * PRICE_PER_1000_ADS / 1000
