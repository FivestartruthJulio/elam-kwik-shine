"""Rank ads, roll up advertisers, and find what changed since last run.

Why "days running" is the signal: advertisers kill ads that lose money.
An ad still running after 30+ days (especially with many variations) is
almost certainly profitable for that advertiser.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from statistics import median


def dedupe(ads_by_keyword: dict[str, list[dict]]) -> list[dict]:
    """Merge the same ad found by several keywords, then collapse copies of
    the same creative (same page + same copy) into its longest-running run."""
    by_id: dict[str, dict] = {}
    for kw, ads in ads_by_keyword.items():
        for ad in ads:
            if ad["id"] in by_id:
                by_id[ad["id"]]["keywords"].append(kw)
            else:
                by_id[ad["id"]] = {**ad, "keywords": [kw]}

    by_creative: dict[tuple, dict] = {}
    for ad in by_id.values():
        key = (ad["page_id"] or ad["page_name"], _norm(ad["body"])[:200] or ad["id"])
        cur = by_creative.get(key)
        if cur is None:
            by_creative[key] = {**ad, "copies": 1}
            continue
        keep, other = (ad, cur) if (ad["days_running"] or 0) > (cur["days_running"] or 0) else (cur, ad)
        merged = {**keep}
        merged["copies"] = cur["copies"] + 1
        merged["variations"] = max(keep["variations"], other["variations"])
        merged["keywords"] = sorted(set(cur["keywords"]) | set(ad["keywords"]))
        by_creative[key] = merged
    return list(by_creative.values())


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower())


def rank(ads: list[dict]) -> list[dict]:
    """Longest running first; ties broken by how hard it's being scaled."""
    return sorted(
        ads,
        key=lambda a: (a["days_running"] or 0, a.get("copies", 1) + a["variations"]),
        reverse=True,
    )


def advertisers(ads: list[dict], proven_days: int) -> list[dict]:
    pages: dict[str, list[dict]] = defaultdict(list)
    for ad in ads:
        pages[ad["page_id"] or ad["page_name"]].append(ad)
    rows = []
    for pid, items in pages.items():
        days = [a["days_running"] or 0 for a in items]
        rows.append({
            "page_id": items[0]["page_id"],
            "page_name": items[0]["page_name"],
            "ads": len(items),
            "proven_ads": sum(d >= proven_days for d in days),
            "longest_days": max(days),
            "median_days": int(median(days)),
            "total_copies": sum(a.get("copies", 1) for a in items),
            "page_url": f"https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country=US&view_all_page_id={items[0]['page_id']}" if items[0]["page_id"] else "",
        })
    return sorted(rows, key=lambda r: (r["proven_ads"], r["total_copies"], r["longest_days"]), reverse=True)


STOP = set("""a an and are as at be by for from has have i if in is it its of on or our
that the this to was we will with you your yours can get more now just all any not no do
does here there their they them who what when how why which about up out so than then""".split())


def top_phrases(ads: list[dict], n: int = 25, min_advertisers: int = 3) -> list[dict]:
    """Two-to-four word phrases used by many *different advertisers* in
    proven ads: the angles the whole market keeps paying to run. Counting
    advertisers (not ads) stops one big spender from drowning out the rest."""
    by_page: dict[str, set] = defaultdict(set)
    for ad in ads:
        text = (ad["body"] + " " + ad["title"]).lower().replace("’", "'")
        words = re.findall(r"[\w$']+", text)
        page = ad.get("page_id") or ad.get("page_name") or ad.get("id")
        for size in (2, 3, 4):
            for i in range(len(words) - size + 1):
                g = words[i : i + size]
                if g[0] in STOP or g[-1] in STOP:
                    continue
                by_page[" ".join(g)].add(page)
    counts = Counter({p: len(pages) for p, pages in by_page.items()})
    rows = [{"phrase": p, "advertisers": c} for p, c in counts.most_common(n * 6) if c >= min_advertisers]
    # Drop a phrase when a longer phrase containing it has the same reach,
    # and a shorter one when it's just a fragment of a listed longer one.
    out = []
    for r in sorted(rows, key=lambda r: (-r["advertisers"], -len(r["phrase"]))):
        if any(r["phrase"] in o["phrase"] and o["advertisers"] >= r["advertisers"] for o in out):
            continue
        out.append(r)
    return out[:n]


def summarize(ads_by_keyword: dict[str, list[dict]], proven_days: int,
              previous_ids_proven: set[str] | None = None,
              previous_ids: set[str] | None = None) -> dict:
    ads = rank(dedupe(ads_by_keyword))
    proven = [a for a in ads if (a["days_running"] or 0) >= proven_days]
    for a in ads:
        a["proven"] = (a["days_running"] or 0) >= proven_days
        a["new_since_last_run"] = previous_ids is not None and a["id"] not in previous_ids
    newly_proven = []
    if previous_ids_proven is not None:
        newly_proven = [a for a in proven if a["id"] not in previous_ids_proven]

    formats = Counter(a["format"] or "UNKNOWN" for a in proven)
    ctas = Counter(a["cta"] for a in proven if a["cta"])
    return {
        "stats": {
            "ads": len(ads),
            "proven": len(proven),
            "advertisers": len({a["page_id"] or a["page_name"] for a in ads}),
            "median_days": int(median([a["days_running"] or 0 for a in ads])) if ads else 0,
            "per_keyword": {k: len(v) for k, v in ads_by_keyword.items()},
        },
        "top_ads": ads[:150],
        "newly_proven": newly_proven[:50],
        "advertisers": advertisers(ads, proven_days)[:40],
        "phrases": top_phrases(proven),
        "formats": formats.most_common(),
        "ctas": ctas.most_common(10),
    }
