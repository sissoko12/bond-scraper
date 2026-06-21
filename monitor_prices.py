#!/usr/bin/env python3
"""
Measure how often BondSupermart updates its live bond prices.

Polls the bond-exchange price endpoint for 10 USD bonds every 5 minutes for
30 minutes (7 samples), records the bid/ask price + the server's own price
timestamp each time, and prints a table showing exactly when each bond's
price actually changed.

Only bonds listed on the iFAST Bond Express (bexFullLotEnabled='Y') return
live prices, so we select 10 such USD bonds from the database. (Arbitrary USD
bonds return a "SYSTEM MAINTENANCE" HTML page from this endpoint.)

Usage:
    python monitor_prices.py                 # 7 samples, 5 min apart (30 min)
    python monitor_prices.py --interval 60 --samples 5   # custom cadence
"""

import argparse
import datetime as dt
import json
import random
import time

import mysql.connector
import requests

DB = dict(host="localhost", port=3306, user="root", password="qaz123wsx",
          database="bondsupermart")
PRICE_URL = "https://www.bondsupermart.com/main/ws/v1/bond-exchange/bond/price"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36")

# Decoded price-snap field codes
PF_BID, PF_ASK = "100004", "100006"
PF_BID_Y, PF_ASK_Y = "100008", "100009"
PF_TS = "-31"   # server-side last update timestamp for the quote


def pick_usd_bonds(n=10):
    """Return up to n (isin, bondId) USD bonds that are on the exchange feed."""
    conn = mysql.connector.connect(**DB)
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT raw_json FROM bonds")
    out = []
    for r in cur.fetchall():
        try:
            info = json.loads(r["raw_json"])["filter"]["bondInfo"]
        except (ValueError, KeyError, TypeError):
            continue
        if info.get("bexFullLotEnabled") == "Y" and info.get("bondCurrencyCode") == "USD":
            out.append((info["issueCode"], info["bondId"]))
        if len(out) >= n:
            break
    conn.close()
    return out


def fetch_prices(bonds):
    """Fetch one snapshot for all bonds. Returns {isin: {...}}."""
    sym_to_isin = {f"8000.9.{bid}": isin for isin, bid in bonds}
    url = f"{PRICE_URL}?symbolList={','.join(sym_to_isin)}"
    snap = {}
    try:
        resp = requests.get(url, headers={"user-agent": UA, "referer":
                            "https://www.bondsupermart.com/bsm/bond-selector"}, timeout=30)
    except requests.RequestException as e:
        print(f"  request error: {e}")
        return snap
    if "json" not in resp.headers.get("content-type", ""):
        # A bad symbol can poison a batch -> maintenance HTML. Fall back to 1-by-1.
        for sym, isin in sym_to_isin.items():
            try:
                r = requests.get(f"{PRICE_URL}?symbolList={sym}",
                                 headers={"user-agent": UA}, timeout=30)
                if "json" in r.headers.get("content-type", ""):
                    rec = r.json().get("Data", {}).get(sym)
                    if rec:
                        snap[isin] = _extract(rec)
            except requests.RequestException:
                pass
            time.sleep(random.uniform(0.5, 1.0))
        return snap
    for sym, rec in resp.json().get("Data", {}).items():
        isin = sym_to_isin.get(sym)
        if isin and isinstance(rec, dict):
            snap[isin] = _extract(rec)
    return snap


def _extract(rec):
    def f(k):
        try:
            return float(rec.get(k))
        except (TypeError, ValueError):
            return None
    return {"bid": f(PF_BID), "ask": f(PF_ASK),
            "bid_yld": f(PF_BID_Y), "ask_yld": f(PF_ASK_Y),
            "server_ts": rec.get(PF_TS)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=300, help="seconds between samples (default 300)")
    ap.add_argument("--samples", type=int, default=7, help="number of samples (default 7 = 30 min)")
    args = ap.parse_args()

    bonds = pick_usd_bonds(10)
    print(f"Monitoring {len(bonds)} USD bonds, every {args.interval}s, {args.samples} samples "
          f"(~{args.interval*(args.samples-1)//60} min total)\n")
    for isin, bid in bonds:
        print(f"  {isin:14} symbol=8000.9.{bid}")
    print()

    # history[isin] = list of (sample_dt, snapshot)
    history = {isin: [] for isin, _ in bonds}
    sample_times = []

    for s in range(args.samples):
        now = dt.datetime.now()
        sample_times.append(now)
        snap = fetch_prices(bonds)
        got = sum(1 for v in snap.values() if v and v.get("bid") is not None)
        print(f"[sample {s+1}/{args.samples}] {now:%H:%M:%S}  got {got}/{len(bonds)} prices")
        for isin, _ in bonds:
            history[isin].append((now, snap.get(isin)))
        if s < args.samples - 1:
            time.sleep(args.interval)

    print_change_table(bonds, history, sample_times)


def print_change_table(bonds, history, sample_times):
    print("\n" + "=" * 78)
    print("PRICE-CHANGE SUMMARY")
    print("=" * 78)
    print(f"{'ISIN':14} {'samples':8} {'changes':8} {'first→last bid':22} server-ts moves")
    print("-" * 78)
    for isin, _ in bonds:
        seq = history[isin]
        bids = [(t, (v or {}).get("bid")) for t, v in seq]
        valid = [b for _, b in bids if b is not None]
        # count consecutive changes in (bid,ask) and in server timestamp
        changes, ts_moves = 0, 0
        prev = prev_ts = None
        change_times = []
        for t, v in seq:
            if not v:
                continue
            cur = (v.get("bid"), v.get("ask"), v.get("bid_yld"), v.get("ask_yld"))
            if prev is not None and cur != prev:
                changes += 1
                change_times.append(f"{t:%H:%M:%S}")
            prev = cur
            if prev_ts is not None and v.get("server_ts") != prev_ts:
                ts_moves += 1
            prev_ts = v.get("server_ts")
        span = f"{valid[0]}→{valid[-1]}" if valid else "no data"
        print(f"{isin:14} {len(valid):<8} {changes:<8} {span:22} {ts_moves}")
        if change_times:
            print(f"               changed at: {', '.join(change_times)}")

    print("\nPer-sample detail (bid price):")
    header = "ISIN".ljust(14) + "".join(f"{t:%H:%M}".rjust(9) for t in sample_times)
    print(header)
    print("-" * len(header))
    for isin, _ in bonds:
        row = isin.ljust(14)
        prev = None
        for _, v in history[isin]:
            b = (v or {}).get("bid")
            cell = "-" if b is None else f"{b:.3f}"
            mark = "*" if (prev is not None and b is not None and b != prev) else " "
            row += (cell + mark).rjust(9)
            if b is not None:
                prev = b
        print(row)
    print("\n('*' marks a sample where the bid changed vs. the previous sample)")
    print("'server-ts moves' = how many times the endpoint's own quote timestamp advanced.")


if __name__ == "__main__":
    main()
