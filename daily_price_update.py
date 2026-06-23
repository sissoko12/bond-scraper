#!/usr/bin/env python3
"""
Daily indicative-price refresh for BondSupermart.

A single call to the bond-selector filter endpoint returns the full bond list
with indicative bid/ask prices and yields for EVERY bond (not just the
exchange-listed set). This script:

  1. Hits POST /main/ws/v3/bond-selector/filter once (same session handling,
     headers and rate-limited client as scraper.py).
  2. Extracts the indicative price/yield fields per bond.
  3. UPDATEs the matching rows in bondsupermart.bonds.
  4. Appends a dated snapshot to bond_prices_history (one row per bond per day;
     re-running on the same day replaces that day's snapshot so it stays
     idempotent).

Run it from cron once a day:

    python daily_price_update.py
"""

import datetime as dt

import mysql.connector

import scraper
from scraper import (
    Client,
    DB_CONFIG,
    DB_NAME,
    FILTER_URL,
    BASE,
    WARM_URL,
    to_decimal,
    clean_rating,
)

# isin -> (bonds column, json key, converter)
FIELD_MAP = [
    ("bid_price", "bidPrice", to_decimal),
    ("ask_price", "offerPrice", to_decimal),
    ("bid_ytm", "bid_YTM", to_decimal),
    ("ask_ytm", "offer_YTM", to_decimal),
    ("bid_ytw", "bid_YldToWorst", to_decimal),
    ("ask_ytw", "offer_YldToWorst", to_decimal),
    ("years_to_maturity", "yearsToMaturity", to_decimal),
    ("fitch_rating", "bondFitchRating", clean_rating),
]


def ensure_history_table(conn):
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS bond_prices_history (
          id INT AUTO_INCREMENT PRIMARY KEY,
          isin VARCHAR(32),
          price_date DATE,
          bid_price DECIMAL(18,6),
          ask_price DECIMAL(18,6),
          bid_ytm DECIMAL(18,6),
          ask_ytm DECIMAL(18,6),
          bid_ytw DECIMAL(18,6),
          ask_ytw DECIMAL(18,6),
          scraped_at DATETIME,
          UNIQUE KEY uniq_isin_date (isin, price_date),
          KEY idx_isin (isin),
          KEY idx_date (price_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)
    conn.commit()
    cur.close()


def fetch_filter(client):
    """One POST to the filter endpoint -> full bondList."""
    body = {
        "orderBy": "bondIssuer",
        "order": "asc",
        "pageSize": "100",   # ignored by server; kept for parity with scraper.py
        "pageNumber": "0",
        "locale": "en-us",
    }
    data = client.post_json(
        FILTER_URL, body,
        headers={"content-type": "application/json", "origin": BASE, "referer": WARM_URL},
        label="filter",
    )
    if not data:
        raise SystemExit("filter endpoint returned no JSON")
    return data.get("bondList", []) or []


def main():
    today = dt.date.today()
    now = dt.datetime.now()

    client = Client()
    bonds = fetch_filter(client)
    print(f"  bondList={len(bonds)}")

    # Build per-bond value tuples from the payload.
    updates = []   # for bonds UPDATE
    history = []   # for bond_prices_history
    for b in bonds:
        info = b.get("bondInfo", {})
        isin = info.get("issueCode")
        if not isin:
            continue
        vals = {col: conv(info.get(jk)) for col, jk, conv in FIELD_MAP}
        updates.append((
            vals["bid_price"], vals["ask_price"],
            vals["bid_ytm"], vals["ask_ytm"],
            vals["bid_ytw"], vals["ask_ytw"],
            vals["years_to_maturity"], vals["fitch_rating"],
            now, isin,
        ))
        history.append((
            isin, today,
            vals["bid_price"], vals["ask_price"],
            vals["bid_ytm"], vals["ask_ytm"],
            vals["bid_ytw"], vals["ask_ytw"],
            now,
        ))

    conn = mysql.connector.connect(database=DB_NAME, **DB_CONFIG)
    conn.autocommit = False
    ensure_history_table(conn)
    cur = conn.cursor()

    # 3) Update the master bonds rows. Only rows whose isin already exists are
    #    touched (rowcount reflects matched rows).
    cur.executemany("""
        UPDATE bonds SET
          bid_price=%s, ask_price=%s,
          bid_ytm=%s, ask_ytm=%s,
          bid_ytw=%s, ask_ytw=%s,
          years_to_maturity=%s, fitch_rating=%s,
          price_updated_at=%s
        WHERE isin=%s
    """, updates)
    updated = cur.rowcount

    # 4) Daily snapshot. Idempotent per (isin, price_date) via upsert.
    cur.executemany("""
        INSERT INTO bond_prices_history
          (isin, price_date, bid_price, ask_price, bid_ytm, ask_ytm,
           bid_ytw, ask_ytw, scraped_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
          bid_price=VALUES(bid_price), ask_price=VALUES(ask_price),
          bid_ytm=VALUES(bid_ytm), ask_ytm=VALUES(ask_ytm),
          bid_ytw=VALUES(bid_ytw), ask_ytw=VALUES(ask_ytw),
          scraped_at=VALUES(scraped_at)
    """, history)

    conn.commit()
    print(f"  bonds updated: {updated}")
    print(f"  history rows written for {today}: {len(history)}")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
