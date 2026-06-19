# BondSupermart Scraper

Scrapes every bond listed on [bondsupermart.com](https://www.bondsupermart.com) and
stores the data in a dedicated MySQL database called **`bondsupermart`**.

## What it collects

| Table | Source endpoint | Notes |
|-------|-----------------|-------|
| `bonds` | `POST v3/bond-selector/filter` + `GET v4/bond-info/bond-factsheet/{isin}` | One filter call returns **all ~3,835 bonds**; the per-bond factsheet fills the rich fields (cusip, issue/announcement dates, issue price/yield, seniority, total issue size, …). |
| `bond_chart` | `GET v4/bond-info/bond-factsheet-chart/{isin}` | "Since Inception" history — bid/ask price and bid/ask yield-to-maturity & yield-to-worst, one row per date. |
| `bond_prices` | `GET v1/bond-exchange/bond/price?symbolList=...` | Live exchange quotes, batched 50 symbols/request. **Only exchange-listed bonds return data** — others return a maintenance HTML page and are skipped. |
| `scrape_progress` | — | Per-phase, per-ISIN checkpoints so a run can resume. |

### Endpoint reality check
* The filter endpoint **ignores `pageSize`/`pageNumber`** and returns the entire
  bond list in a single POST — Phase 1 is one request, not a paginated loop.
* The fields your `bonds` table needs (cusip, dates, issue price/yield, seniority,
  issue size) are **not** in the chart endpoint — they come from the separate
  `bond-factsheet/{isin}` detail endpoint, so Phase 2 makes two calls per bond.
* The price endpoint uses an opaque numeric-field format; the relevant codes are
  decoded as constants at the top of `scraper.py` (`PF_*`).

## Setup

```bash
pip install -r requirements.txt
# MySQL must be running on localhost:3306 (user=root, password as configured in scraper.py)
```

The scraper creates the database and tables itself; `schema.sql` is also provided
if you prefer to create them manually.

## Usage

```bash
python scraper.py                      # full run: all bonds, all phases
python scraper.py --test --no-prices --no-chart   # quick smoke test (first 5 bonds)
python scraper.py --limit 50           # cap phase 2/3 to 50 bonds
python scraper.py --no-chart           # skip historical chart
python scraper.py --resume             # skip bonds already done (uses scrape_progress)
```

## Rate limiting

Designed to stay under the radar:

* random **3–5s** delay between every request
* rotates **3 User-Agent** strings
* **re-warms session cookies every 100 requests**
* on **429** → sleep 120s and retry; on **403** → sleep 300s, re-warm, retry
* hard cap of **200 requests per rolling hour**

> A full run is large: ~3,835 bonds × 2 calls (detail + chart) under a 200/hour
> cap means Phase 2 alone spans ~38 hours. Use `--resume` to continue across runs.

## Config

DB credentials and all rate-limit knobs live at the top of `scraper.py`.
