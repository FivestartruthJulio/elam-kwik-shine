# Medicare Ad Intel

Finds the longest-running Facebook ads for Medicare, final expense / life, and
ACA lead generation. Ads that keep running are ads that pay, so ranking by
days running shows what's working.

**Dashboard:** https://claude.ai/artifact/SYd2VFpiWxYQBoaMFhJFuJ (refreshed weekly)

## How it works

1. For each keyword in `config.toml`, scrape active US ads from the Facebook
   Ad Library through Apify's
   [`curious_coder/facebook-ads-library-scraper`](https://apify.com/curious_coder/facebook-ads-library-scraper)
   ($0.75 per 1,000 ads). Meta's official API doesn't return US commercial ads,
   so a scraper is the only way to get them.
2. Merge duplicates, rank by days running, flag ads running 30+ days as
   **proven**, roll up advertisers, and find phrases used by many advertisers.
3. Compare with last week's snapshot in `data/history/` to find **newly proven** ads.
4. Export row files to `data/dashboard/` for the dashboard.

## Use it

```bash
export APIFY_TOKEN=...            # not needed in the cloud environment (injected by the proxy)
python3 -m adresearch estimate    # cost of a run
python3 -m adresearch run         # scrape everything (≈ $1.43, 19 keywords × 100 ads)
python3 -m adresearch run --vertical medicare --limit 20   # quick, cheap test
python3 -m adresearch export      # write data/dashboard/*.json
python3 -m unittest discover -s tests -t .
```

Only the Python 3.11+ standard library is used.

## Change what it tracks

Edit `config.toml`: add keywords, new verticals (`[verticals.solar]` with a
`label` and `keywords`), the number of ads per keyword, or the proven threshold.
Cost is roughly keywords × ads_per_keyword × $0.00075.

## Files

| Path | What |
| --- | --- |
| `adresearch/apify.py` | Starts scraper runs and fetches results |
| `adresearch/normalize.py` | Flattens raw Ad Library items |
| `adresearch/analyze.py` | Dedupe, ranking, advertisers, phrases, week-over-week diff |
| `adresearch/__main__.py` | CLI and dashboard export |
| `data/latest.json` | Full latest report |
| `data/history/` | Weekly snapshots of ad ids (for "newly proven") |
| `data/dashboard/` | Row files the dashboard loads |
| `data/raw/` | Raw scraper output (not committed) |
