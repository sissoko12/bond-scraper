#!/usr/bin/env python3
"""
BondSupermart scraper.

Pulls every bond on bondsupermart.com and stores it in a dedicated MySQL
database called `bondsupermart`.

Phases
------
1. filter   : POST /main/ws/v3/bond-selector/filter -> ALL bonds in one call
              (the pageSize/pageNumber params are ignored by the server, so a
              single request returns the full list). Seeds the `bonds` table
              with the fields available in the list.
2. detail   : GET /main/ws/v4/bond-info/bond-factsheet/{isin}      -> rich fields
              GET /main/ws/v4/bond-info/bond-factsheet-chart/{isin} -> history
              The detail call fills in cusip, issue/announcement dates, issue
              price/yield, seniority, total issue size, etc. The chart call
              fills `bond_chart`.
3. prices   : GET /main/ws/v1/bond-exchange/bond/price?symbolList=...
              Live exchange prices, batched. Only exchange-listed bonds return
              data; everything else returns an HTML maintenance page and is
              skipped.

Rate limiting (to avoid being blocked)
--------------------------------------
* random 3-5s delay between every request
* rotate between 3 User-Agent strings
* re-warm session cookies every 100 requests
* on HTTP 429 -> sleep 120s and retry; on 403 -> sleep 300s and retry
* hard cap of 200 requests per rolling hour

Usage
-----
    python scraper.py                 # full run, all phases, all bonds
    python scraper.py --test          # only the first 5 bonds (for detail/chart/prices)
    python scraper.py --no-prices     # skip phase 3
    python scraper.py --no-chart      # skip historical chart fetch
    python scraper.py --limit 50      # cap phase 2/3 to N bonds
    python scraper.py --resume        # skip bonds already recorded in scrape_progress
"""

import argparse
import collections
import datetime as dt
import json
import random
import sys
import time

import requests

try:
    import mysql.connector
except ImportError:
    sys.exit("mysql-connector-python is required: pip install mysql-connector-python")


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
DB_CONFIG = {
    "host": "localhost",
    "port": 3306,
    "user": "root",
    "password": "qaz123wsx",
    # database is selected explicitly below so we always write to bondsupermart
}
DB_NAME = "bondsupermart"

BASE = "https://www.bondsupermart.com"
FILTER_URL = f"{BASE}/main/ws/v3/bond-selector/filter"
DETAIL_URL = f"{BASE}/main/ws/v4/bond-info/bond-factsheet/{{isin}}"
CHART_URL = f"{BASE}/main/ws/v4/bond-info/bond-factsheet-chart/{{isin}}"
PRICE_URL = f"{BASE}/main/ws/v1/bond-exchange/bond/price"
WARM_URL = f"{BASE}/bsm/bond-selector"

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
]

# Rate-limit knobs
MIN_DELAY = 3.0
MAX_DELAY = 5.0
REWARM_EVERY = 100
MAX_REQ_PER_HOUR = 200
SLEEP_ON_429 = 120
SLEEP_ON_403 = 300
PRICE_BATCH = 50

# Price snap endpoint field codes (decoded from the live response)
PF_BID_PRICE = "100004"
PF_ASK_PRICE = "100006"
PF_BID_YIELD = "100008"
PF_ASK_YIELD = "100009"
PF_CHG_BID = "2949"   # best-effort: net change fields, bid side
PF_CHG_ASK = "2951"   # best-effort: net change fields, ask side
PF_TIMESTAMP = "-31"
PF_ISIN = "111752"
PF_SYMBOL = "-1"


# --------------------------------------------------------------------------- #
# HTTP client with rate limiting / retry / session re-warming
# --------------------------------------------------------------------------- #
class Client:
    def __init__(self):
        self.session = requests.Session()
        self.req_count = 0
        self._req_times = collections.deque()  # timestamps for the rolling hour cap
        self._warm()

    def _warm(self):
        """Establish / refresh session cookies by hitting a normal page."""
        ua = random.choice(USER_AGENTS)
        try:
            self.session.get(
                WARM_URL,
                headers={"user-agent": ua, "accept": "text/html"},
                timeout=30,
            )
            print(f"  [session warmed, cookies={len(self.session.cookies)}]")
        except requests.RequestException as e:
            print(f"  [warm failed: {e}]")

    def _throttle(self):
        # Re-warm cookies periodically.
        if self.req_count and self.req_count % REWARM_EVERY == 0:
            print(f"  [re-warming session after {self.req_count} requests]")
            self._warm()

        # Enforce the rolling-hour cap.
        now = time.time()
        while self._req_times and now - self._req_times[0] > 3600:
            self._req_times.popleft()
        if len(self._req_times) >= MAX_REQ_PER_HOUR:
            wait = 3600 - (now - self._req_times[0]) + 1
            print(f"  [hourly cap reached, sleeping {wait:.0f}s]")
            time.sleep(max(wait, 0))

        # Random polite delay between requests.
        time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

    def _headers(self, extra=None):
        h = {
            "accept": "application/json, text/plain, */*",
            "user-agent": random.choice(USER_AGENTS),
        }
        if extra:
            h.update(extra)
        return h

    def request(self, method, url, *, headers=None, json_body=None, label=""):
        """Make a rate-limited request, retrying on 429/403. Returns Response."""
        while True:
            self._throttle()
            self.req_count += 1
            self._req_times.append(time.time())
            try:
                resp = self.session.request(
                    method,
                    url,
                    headers=self._headers(headers),
                    json=json_body,
                    timeout=60,
                )
            except requests.RequestException as e:
                print(f"  [{label}] network error: {e}; retrying in 30s")
                time.sleep(30)
                continue

            if resp.status_code == 429:
                print(f"  [{label}] 429 rate limited; sleeping {SLEEP_ON_429}s")
                time.sleep(SLEEP_ON_429)
                continue
            if resp.status_code == 403:
                print(f"  [{label}] 403 forbidden; sleeping {SLEEP_ON_403}s then re-warming")
                time.sleep(SLEEP_ON_403)
                self._warm()
                continue
            return resp

    def get_json(self, url, headers=None, label=""):
        resp = self.request("GET", url, headers=headers, label=label)
        return _safe_json(resp)

    def post_json(self, url, body, headers=None, label=""):
        resp = self.request("POST", url, headers=headers, json_body=body, label=label)
        return _safe_json(resp)


def _safe_json(resp):
    """Return parsed JSON, or None if the body is not JSON (e.g. maintenance HTML)."""
    ctype = resp.headers.get("content-type", "")
    if "json" not in ctype:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def epoch_ms_to_date(ms):
    if ms in (None, "", 0):
        return None
    try:
        return dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).date()
    except (ValueError, OverflowError, OSError):
        return None


def to_decimal(v):
    if v in (None, "", "-", "***", "N.R"):
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def clean_rating(v):
    """Normalize the masked S&P placeholder to None.

    BondSupermart returns '***' for S&P on every bond because its ratings are
    licensed and cannot be redistributed. Store that as NULL so the column never
    holds a misleading placeholder. 'N.R' (Not Rated) is a real agency value and
    is preserved as-is (e.g. Fitch).
    """
    if v in (None, "", "***"):
        return None
    return v


def parse_price_ts(s):
    """'2026-06-19 08:58:00.3513' -> datetime (truncated to ms)."""
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #
class DB:
    def __init__(self):
        # Ensure the database exists, then connect to it.
        boot = mysql.connector.connect(**DB_CONFIG)
        bcur = boot.cursor()
        bcur.execute(
            f"CREATE DATABASE IF NOT EXISTS {DB_NAME} "
            "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        )
        boot.commit()
        bcur.close()
        boot.close()

        self.conn = mysql.connector.connect(database=DB_NAME, **DB_CONFIG)
        self.conn.autocommit = False
        self._create_tables()

    def _create_tables(self):
        cur = self.conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bonds (
              isin VARCHAR(32) NOT NULL,
              cusip VARCHAR(32),
              bond_issuer VARCHAR(255),
              guarantor VARCHAR(255),
              announcement_date DATE,
              issue_date DATE,
              maturity_date DATE,
              years_to_maturity DECIMAL(10,3),
              next_call_date DATE,
              issue_price DECIMAL(18,6),
              issue_yield DECIMAL(18,6),
              coupon_type VARCHAR(32),
              coupon_rate DECIMAL(18,6),
              coupon_frequency VARCHAR(16),
              seniority VARCHAR(64),
              exchange_listed VARCHAR(32),
              bond_currency VARCHAR(16),
              total_issue_size DECIMAL(30,2),
              min_investment DECIMAL(20,2),
              incremental_quantity DECIMAL(20,2),
              bond_type VARCHAR(32),
              bond_sector VARCHAR(64),
              bond_sub_sector VARCHAR(64),
              sp_rating VARCHAR(32),
              fitch_rating VARCHAR(32),
              shariah_compliant VARCHAR(8),
              sukuk_investing VARCHAR(8),
              raw_json LONGTEXT,
              scraped_at DATETIME,
              PRIMARY KEY (isin),
              KEY idx_issuer (bond_issuer),
              KEY idx_maturity (maturity_date)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bond_prices (
              id BIGINT NOT NULL AUTO_INCREMENT,
              isin VARCHAR(32),
              symbol VARCHAR(64),
              bid_price DECIMAL(18,6),
              ask_price DECIMAL(18,6),
              bid_yield DECIMAL(18,6),
              ask_yield DECIMAL(18,6),
              change_bid_price DECIMAL(18,6),
              change_ask_price DECIMAL(18,6),
              price_timestamp DATETIME(3),
              scraped_at DATETIME,
              PRIMARY KEY (id),
              KEY idx_isin (isin),
              KEY idx_symbol (symbol)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bond_chart (
              id BIGINT NOT NULL AUTO_INCREMENT,
              isin VARCHAR(32),
              chart_date DATE,
              ask_yield_to_worst DECIMAL(18,6),
              bid_yield_to_worst DECIMAL(18,6),
              ask_yield_to_maturity DECIMAL(18,6),
              bid_yield_to_maturity DECIMAL(18,6),
              ask_price DECIMAL(18,6),
              bid_price DECIMAL(18,6),
              PRIMARY KEY (id),
              UNIQUE KEY uniq_isin_date (isin, chart_date),
              KEY idx_isin (isin)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS scrape_progress (
              phase VARCHAR(32) NOT NULL,
              isin VARCHAR(32) NOT NULL,
              done_at DATETIME,
              PRIMARY KEY (phase, isin)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
        self.conn.commit()
        cur.close()

    # -- bonds ------------------------------------------------------------- #
    def upsert_bond_from_filter(self, info, perf):
        """Insert/seed a bond row from the filter list payload."""
        row = {
            "isin": info.get("issueCode"),
            "bond_issuer": info.get("bondIssuer"),
            "guarantor": info.get("bondGuarantor"),
            "maturity_date": epoch_ms_to_date(info.get("maturityDate")),
            "years_to_maturity": to_decimal(info.get("yearsToMaturity")),
            "next_call_date": epoch_ms_to_date(info.get("nextCallDate")),
            "coupon_type": info.get("couponType"),
            "coupon_rate": to_decimal(info.get("couponRate")),
            "coupon_frequency": info.get("couponFrequency"),
            "exchange_listed": info.get("exchangeList"),
            "bond_currency": info.get("bondCurrencyCode"),
            "min_investment": to_decimal(info.get("minInvestQuantity")),
            "bond_type": info.get("bondType"),
            "bond_sector": info.get("bondSector"),
            "bond_sub_sector": info.get("subSector"),
            "sp_rating": clean_rating(info.get("bondSnpRating")),
            "fitch_rating": clean_rating(info.get("bondFitchRating")),
            "shariah_compliant": info.get("shariahCompliance"),
            "raw_json": json.dumps({"filter": {"bondInfo": info, "bondPerformance": perf}}),
            "scraped_at": dt.datetime.now(),
        }
        cols = list(row)
        placeholders = ", ".join(["%s"] * len(cols))
        updates = ", ".join(f"{c}=VALUES({c})" for c in cols if c != "isin")
        sql = (
            f"INSERT INTO bonds ({', '.join(cols)}) VALUES ({placeholders}) "
            f"ON DUPLICATE KEY UPDATE {updates}"
        )
        cur = self.conn.cursor()
        cur.execute(sql, [row[c] for c in cols])
        cur.close()

    def update_bond_detail(self, isin, fs):
        """Fill in the rich fields from bondFactSheetDisplay."""
        fields = {
            "cusip": fs.get("cusip"),
            "bond_issuer": fs.get("bondIssuer"),
            "guarantor": fs.get("bondGuarantor"),
            "announcement_date": epoch_ms_to_date(fs.get("announcementDate")),
            "issue_date": epoch_ms_to_date(fs.get("issueDate")),
            "maturity_date": epoch_ms_to_date(fs.get("maturityDate")),
            "issue_price": to_decimal(fs.get("issuePrice")),
            "issue_yield": to_decimal(fs.get("issueYield")),
            "coupon_type": fs.get("couponType"),
            "coupon_rate": to_decimal(fs.get("couponRate")),
            "coupon_frequency": fs.get("couponFrequency"),
            "seniority": fs.get("seniority"),
            "exchange_listed": fs.get("exListed"),
            "bond_currency": fs.get("bondCurrencyCode"),
            "total_issue_size": to_decimal(fs.get("totalIssueSize")),
            "min_investment": to_decimal(fs.get("prospectusMinInitial")),
            "incremental_quantity": to_decimal(fs.get("prospectusMinSub")),
            "bond_type": fs.get("bondType"),
            "bond_sector": fs.get("bondSector"),
            "bond_sub_sector": fs.get("bondSectorSub"),
            "shariah_compliant": fs.get("shariahCompliance"),
            "scraped_at": dt.datetime.now(),
        }
        # Merge the detail payload into raw_json alongside the filter data.
        cur = self.conn.cursor(dictionary=True)
        cur.execute("SELECT raw_json FROM bonds WHERE isin=%s", (isin,))
        existing = cur.fetchone()
        merged = {}
        if existing and existing.get("raw_json"):
            try:
                merged = json.loads(existing["raw_json"])
            except ValueError:
                merged = {}
        merged["detail"] = fs
        fields["raw_json"] = json.dumps(merged)
        cur.close()

        sets = ", ".join(f"{c}=%s" for c in fields)
        cur = self.conn.cursor()
        cur.execute(
            f"UPDATE bonds SET {sets} WHERE isin=%s",
            [*fields.values(), isin],
        )
        cur.close()

    # -- chart ------------------------------------------------------------- #
    def replace_chart(self, isin, rows):
        cur = self.conn.cursor()
        cur.execute("DELETE FROM bond_chart WHERE isin=%s", (isin,))
        if rows:
            cur.executemany("""
                INSERT INTO bond_chart
                  (isin, chart_date, ask_yield_to_worst, bid_yield_to_worst,
                   ask_yield_to_maturity, bid_yield_to_maturity, ask_price, bid_price)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            """, rows)
        cur.close()

    # -- prices ------------------------------------------------------------ #
    def insert_prices(self, rows):
        if not rows:
            return
        cur = self.conn.cursor()
        cur.executemany("""
            INSERT INTO bond_prices
              (isin, symbol, bid_price, ask_price, bid_yield, ask_yield,
               change_bid_price, change_ask_price, price_timestamp, scraped_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, rows)
        cur.close()

    # -- progress ---------------------------------------------------------- #
    def mark_done(self, phase, isin):
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO scrape_progress (phase, isin, done_at) VALUES (%s,%s,%s) "
            "ON DUPLICATE KEY UPDATE done_at=VALUES(done_at)",
            (phase, isin, dt.datetime.now()),
        )
        cur.close()

    def done_set(self, phase):
        cur = self.conn.cursor()
        cur.execute("SELECT isin FROM scrape_progress WHERE phase=%s", (phase,))
        s = {r[0] for r in cur.fetchall()}
        cur.close()
        return s

    def commit(self):
        self.conn.commit()

    def count(self, table):
        cur = self.conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        n = cur.fetchone()[0]
        cur.close()
        return n

    def close(self):
        self.conn.close()


# --------------------------------------------------------------------------- #
# Phases
# --------------------------------------------------------------------------- #
def phase1_filter(client, db):
    """Fetch the full bond list (one request) and seed the bonds table."""
    print("\n=== PHASE 1: bond list ===")
    body = {
        "orderBy": "bondIssuer",
        "order": "asc",
        "pageSize": "100",   # ignored by server, kept for parity with the captured request
        "pageNumber": "0",
        "locale": "en-us",
    }
    data = client.post_json(
        FILTER_URL, body,
        headers={"content-type": "application/json", "origin": BASE, "referer": WARM_URL},
        label="filter",
    )
    if not data:
        print("  ERROR: filter endpoint returned no JSON")
        return []
    bonds = data.get("bondList", []) or []
    print(f"  bondList={len(bonds)}")
    bond_ids = []  # (isin, bondId, listed) for later phases
    listed_n = 0
    for b in bonds:
        info = b.get("bondInfo", {})
        perf = b.get("bondPerformance", {})
        isin = info.get("issueCode")
        if not isin:
            continue
        db.upsert_bond_from_filter(info, perf)
        # "exchange-listed" for the live-price endpoint = on the iFAST Bond
        # Express (bex). Other bonds return a maintenance HTML page.
        listed = bool(
            info.get("bexFullLotEnabled") == "Y"
            or info.get("bexOddLotEnabled") == "Y"
            or info.get("executionType")
        )
        listed_n += listed
        bond_ids.append((isin, info.get("bondId"), listed))
    db.commit()
    print(f"  upserted {len(bond_ids)} bonds into `bonds`  ({listed_n} exchange-listed)")
    return bond_ids


def parse_chart(chart_json, isin):
    """Merge yield + price 'SINCE_INCEPTION' series into per-date rows."""
    if not chart_json:
        return []
    by_date = {}  # date -> dict of metrics

    def collect(series_list, mapping):
        for series in series_list or []:
            target = mapping.get(series.get("name", "").strip())
            if not target:
                continue
            for point in series.get("data", []):
                if not point or len(point) < 2:
                    continue
                d = epoch_ms_to_date(point[0])
                if d is None:
                    continue
                by_date.setdefault(d, {})[target] = to_decimal(point[1])

    yld = (chart_json.get("yieldChartMap") or {}).get("SINCE_INCEPTION")
    prc = (chart_json.get("priceChartMap") or {}).get("SINCE_INCEPTION")
    collect(yld, {
        "Bid Yield to Maturity": "bid_ytm",
        "Ask Yield to Maturity": "ask_ytm",
        "Bid Yield To Worst": "bid_ytw",
        "Offer Yield To Worst": "ask_ytw",
        "Ask Yield To Worst": "ask_ytw",
    })
    collect(prc, {
        "Bid Price": "bid_price",
        "Ask Price": "ask_price",
    })

    rows = []
    for d in sorted(by_date):
        m = by_date[d]
        rows.append((
            isin, d,
            m.get("ask_ytw"), m.get("bid_ytw"),
            m.get("ask_ytm"), m.get("bid_ytm"),
            m.get("ask_price"), m.get("bid_price"),
        ))
    return rows


def phase2_detail(client, db, bond_ids, fetch_chart=True, resume=False):
    """For each bond fetch full detail (+ chart) and update the DB."""
    print("\n=== PHASE 2: detail" + (" + chart" if fetch_chart else "") + " ===")
    done = db.done_set("detail") if resume else set()
    total = len(bond_ids)
    for i, (isin, _bond_id, _listed) in enumerate(bond_ids, 1):
        if isin in done:
            continue
        # Detail
        detail = client.get_json(
            DETAIL_URL.format(isin=isin),
            headers={"referer": f"{BASE}/bsm/bond-factsheet/{isin}"},
            label=f"detail {isin}",
        )
        fs = (detail or {}).get("bondFactSheetDisplay") if detail else None
        if fs:
            db.update_bond_detail(isin, fs)
        else:
            print(f"  [{i}/{total}] {isin}: no detail payload")

        # Chart
        if fetch_chart:
            chart = client.get_json(
                CHART_URL.format(isin=isin),
                headers={"referer": f"{BASE}/bsm/bond-factsheet/{isin}"},
                label=f"chart {isin}",
            )
            rows = parse_chart(chart, isin)
            db.replace_chart(isin, rows)
            print(f"  [{i}/{total}] {isin}: detail{'+' if fs else '?'} chart={len(rows)} pts")
        else:
            print(f"  [{i}/{total}] {isin}: detail{'+' if fs else '?'}")

        db.mark_done("detail", isin)
        db.commit()


def _price_rows_from_data(data, sym_to_isin):
    """Turn a price-endpoint Data dict into bond_prices rows."""
    rows = []
    for sym, rec in (data.get("Data") or {}).items():
        if not isinstance(rec, dict):
            continue
        rows.append((
            rec.get(PF_ISIN) or sym_to_isin.get(sym),
            rec.get(PF_SYMBOL, sym),
            to_decimal(rec.get(PF_BID_PRICE)),
            to_decimal(rec.get(PF_ASK_PRICE)),
            to_decimal(rec.get(PF_BID_YIELD)),
            to_decimal(rec.get(PF_ASK_YIELD)),
            to_decimal(rec.get(PF_CHG_BID)),
            to_decimal(rec.get(PF_CHG_ASK)),
            parse_price_ts(rec.get(PF_TIMESTAMP)),
            dt.datetime.now(),
        ))
    return rows


def phase3_prices(client, db, bond_ids, resume=False):
    """Fetch live exchange prices for exchange-listed bonds, batched.

    Only bonds on the iFAST Bond Express return data; everything else returns a
    maintenance HTML page (and a single bad symbol poisons a whole batch), so we
    restrict the symbol list to the exchange-listed set. If a batch still comes
    back as non-JSON, we fall back to fetching each symbol individually.
    """
    print("\n=== PHASE 3: live prices ===")
    done = db.done_set("price") if resume else set()
    pending = [(isin, bid) for isin, bid, listed in bond_ids
               if listed and bid and isin not in done]
    print(f"  {len(pending)} exchange-listed bonds to price")
    batches = [pending[i:i + PRICE_BATCH] for i in range(0, len(pending), PRICE_BATCH)]
    total_rows = 0
    for bi, batch in enumerate(batches, 1):
        sym_to_isin = {f"8000.9.{bid}": isin for isin, bid in batch}
        data = client.get_json(
            f"{PRICE_URL}?symbolList={','.join(sym_to_isin)}",
            headers={"referer": f"{BASE}/bsm/bond-selector"},
            label=f"prices batch {bi}/{len(batches)}",
        )
        if data and isinstance(data.get("Data"), dict):
            rows = _price_rows_from_data(data, sym_to_isin)
        else:
            # Batch poisoned -> retry symbol by symbol.
            print(f"  batch {bi} non-JSON; falling back to per-symbol")
            rows = []
            for sym, isin in sym_to_isin.items():
                one = client.get_json(
                    f"{PRICE_URL}?symbolList={sym}",
                    headers={"referer": f"{BASE}/bsm/bond-selector"},
                    label=f"price {isin}",
                )
                if one and isinstance(one.get("Data"), dict):
                    rows.extend(_price_rows_from_data(one, {sym: isin}))
        db.insert_prices(rows)
        for isin, _bid in batch:
            db.mark_done("price", isin)
        db.commit()
        total_rows += len(rows)
        print(f"  batch {bi}/{len(batches)}: {len(rows)} priced (of {len(batch)} requested)")
    print(f"  total price rows: {total_rows}")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="BondSupermart scraper")
    ap.add_argument("--test", action="store_true", help="only process first 5 bonds in phase 2/3")
    ap.add_argument("--limit", type=int, default=None, help="cap number of bonds in phase 2/3")
    ap.add_argument("--no-prices", action="store_true", help="skip phase 3")
    ap.add_argument("--no-chart", action="store_true", help="skip historical chart fetch")
    ap.add_argument("--resume", action="store_true", help="skip bonds already in scrape_progress")
    args = ap.parse_args()

    db = DB()
    client = Client()
    try:
        bond_ids = phase1_filter(client, db)

        limit = 5 if args.test else args.limit
        if limit:
            bond_ids = bond_ids[:limit]
            print(f"\n  (limited to {len(bond_ids)} bonds for phases 2/3)")

        phase2_detail(client, db, bond_ids, fetch_chart=not args.no_chart, resume=args.resume)

        if not args.no_prices:
            phase3_prices(client, db, bond_ids, resume=args.resume)

        print("\n=== ROW COUNTS ===")
        for t in ("bonds", "bond_prices", "bond_chart", "scrape_progress"):
            print(f"  {t}: {db.count(t)}")
    finally:
        db.commit()
        db.close()


if __name__ == "__main__":
    main()
