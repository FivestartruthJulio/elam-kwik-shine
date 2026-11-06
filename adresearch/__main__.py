"""Command line entry point.

  python -m adresearch estimate              # what a run will cost
  python -m adresearch run                   # scrape + build data/latest.json
  python -m adresearch run --vertical medicare --limit 20
  python -m adresearch report --raw data/raw/2026-11-06.json   # rebuild, no scraping
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from . import apify
from .analyze import summarize
from .normalize import normalize

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def load_config(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def pick_verticals(cfg: dict, only: str | None) -> dict:
    verts = cfg["verticals"]
    if only:
        if only not in verts:
            sys.exit(f"Unknown vertical {only!r}. Options: {', '.join(verts)}")
        return {only: verts[only]}
    return verts


def previous_snapshot(before: str) -> dict | None:
    hist = sorted((DATA / "history").glob("*.json.gz"))
    hist = [h for h in hist if h.name.split(".")[0] < before]
    if not hist:
        return None
    with gzip.open(hist[-1], "rt") as f:
        return json.load(f)


def build_report(raw: dict, cfg: dict, date: str) -> dict:
    """raw = {vertical: {keyword: [raw items]}} -> dashboard data."""
    now = datetime.now(timezone.utc)
    prev = previous_snapshot(date)
    report = {
        "generated_at": now.isoformat(timespec="seconds"),
        "date": date,
        "previous_run": prev["date"] if prev else None,
        "country": cfg.get("country", "US"),
        "proven_days": cfg.get("proven_days", 30),
        "verticals": {},
    }
    snapshot = {"date": date, "verticals": {}}
    for key, by_kw in raw.items():
        vcfg = cfg["verticals"].get(key, {})
        norm = {kw: [a for a in (normalize(i, now) for i in items) if a] for kw, items in by_kw.items()}
        pv = (prev or {}).get("verticals", {}).get(key)
        summary = summarize(
            norm,
            report["proven_days"],
            previous_ids_proven=set(pv["proven"]) if pv else None,
            previous_ids=set(pv["all"]) if pv else None,
        )
        report["verticals"][key] = {"label": vcfg.get("label", key), "primary": vcfg.get("primary", False), **summary}
        all_ads = [a for ads in norm.values() for a in ads]
        snapshot["verticals"][key] = {
            "all": sorted({a["id"] for a in all_ads}),
            "proven": sorted({a["id"] for a in all_ads if (a["days_running"] or 0) >= report["proven_days"]}),
        }
    return report, snapshot


def write_outputs(report: dict, snapshot: dict) -> None:
    """Write history + latest.json, keeping verticals this run didn't touch."""
    (DATA / "history").mkdir(parents=True, exist_ok=True)
    hist_path = DATA / "history" / f"{snapshot['date']}.json.gz"
    if hist_path.exists():
        with gzip.open(hist_path, "rt") as f:
            snapshot["verticals"] = {**json.load(f)["verticals"], **snapshot["verticals"]}
    with gzip.open(hist_path, "wt") as f:
        json.dump(snapshot, f)
    latest = DATA / "latest.json"
    if latest.exists():
        old = json.loads(latest.read_text()).get("verticals", {})
        report["verticals"] = {**old, **report["verticals"]}
    latest.write_text(json.dumps(report, indent=1, ensure_ascii=False))
    print(f"\nWrote {DATA / 'latest.json'}")
    for key, v in report["verticals"].items():
        s = v["stats"]
        print(f"  {v['label']}: {s['ads']} unique ads, {s['proven']} proven, "
              f"{s['advertisers']} advertisers, {len(v['newly_proven'])} newly proven")
        for a in v["top_ads"][:3]:
            print(f"     {a['days_running']:>4}d  {a['page_name'][:30]:30}  {a['hook'][:70]}")


def export_dashboard(report: dict, out: Path) -> list[Path]:
    """Flatten latest.json into the row files the dashboard reads."""
    out.mkdir(parents=True, exist_ok=True)
    tables: dict[str, list[dict]] = {k: [] for k in
                                     ("summary", "ads", "advertisers", "phrases", "formats", "ctas")}
    for vkey, v in report["verticals"].items():
        s = v["stats"]
        newly = {a["id"] for a in v["newly_proven"]}
        tables["summary"].append({
            "vertical": vkey, "label": v["label"], "primary": v["primary"],
            "ads": s["ads"], "proven": s["proven"], "advertisers": s["advertisers"],
            "median_days": s["median_days"], "newly_proven": len(newly),
            "longest_days": max((a["days_running"] or 0 for a in v["top_ads"]), default=0),
            "run_date": report["date"], "previous_run": report["previous_run"],
            "proven_days": report["proven_days"],
        })
        seen = set()
        for a in v["top_ads"] + v["newly_proven"]:
            if a["id"] in seen:
                continue
            seen.add(a["id"])
            tables["ads"].append({
                "vertical": vkey, "id": a["id"], "advertiser": a["page_name"],
                "days_running": a["days_running"], "start_date": a["start_date"],
                "active": a["is_active"], "proven": a.get("proven", False),
                "newly_proven": a["id"] in newly, "new_since_last_run": a.get("new_since_last_run", False),
                "copies": a.get("copies", 1), "variations": a["variations"],
                "format": a["format"], "cta": a["cta"], "hook": a["hook"],
                "headline": a["title"], "body": a["body"][:700],
                "landing_page": a["link_url"], "platforms": ", ".join(a["platforms"]),
                "keywords": ", ".join(a.get("keywords", [])), "library_url": a["library_url"],
            })
        for r in v["advertisers"]:
            tables["advertisers"].append({"vertical": vkey, **r})
        for r in v["phrases"]:
            tables["phrases"].append({"vertical": vkey, **r})
        for fmt, n in v["formats"]:
            tables["formats"].append({"vertical": vkey, "format": fmt, "proven_ads": n})
        for cta, n in v["ctas"]:
            tables["ctas"].append({"vertical": vkey, "cta": cta, "proven_ads": n})
    paths = []
    for name, rows in tables.items():
        p = out / f"{name}.json"
        p.write_text(json.dumps(rows, ensure_ascii=False))
        paths.append(p)
    return paths


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="adresearch", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", type=Path, default=ROOT / "config.toml")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("run", "estimate"):
        sp = sub.add_parser(name)
        sp.add_argument("--vertical", help="only this vertical (e.g. medicare)")
        sp.add_argument("--limit", type=int, help="ads per keyword (overrides config)")
    rp = sub.add_parser("report", help="rebuild latest.json from a saved raw file")
    rp.add_argument("--raw", type=Path, required=True)
    sub.add_parser("export", help="write dashboard row files to data/dashboard/")
    args = p.parse_args(argv)
    cfg = load_config(args.config)

    if args.cmd == "export":
        report = json.loads((DATA / "latest.json").read_text())
        for path in export_dashboard(report, DATA / "dashboard"):
            print(f"{path}  ({path.stat().st_size // 1024} KB)")
        return

    if args.cmd == "report":
        raw = json.loads(args.raw.read_text())
        write_outputs(*build_report(raw, cfg, args.raw.stem))
        return

    verts = pick_verticals(cfg, args.vertical)
    limit = args.limit or cfg.get("ads_per_keyword", 100)
    n_kw = sum(len(v["keywords"]) for v in verts.values())
    cost = apify.estimate_cost(n_kw, limit)
    print(f"{n_kw} keyword searches × up to {limit} ads ≈ ${cost:.2f} per run "
          f"(≈ ${cost * 4.33:.2f}/month weekly)")
    if args.cmd == "estimate":
        return

    date = datetime.now(timezone.utc).date().isoformat()
    raw = {}
    for key, v in verts.items():
        print(f"\n{v.get('label', key)}:")
        raw[key] = apify.run_keywords(
            v["keywords"], limit, cfg.get("country", "US"), cfg.get("active_only", True)
        )
    (DATA / "raw").mkdir(parents=True, exist_ok=True)
    (DATA / "raw" / f"{date}.json").write_text(json.dumps(raw))
    if not any(items for by_kw in raw.values() for items in by_kw.values()):
        sys.exit("No ads came back from any search — check the Apify token and account credit.")
    write_outputs(*build_report(raw, cfg, date))


if __name__ == "__main__":
    main()
