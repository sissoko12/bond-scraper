#!/usr/bin/env python3
"""
Generate a standalone, self-contained BondSupermart Intelligence dashboard.

Reads the bondsupermart MySQL database and writes a single HTML file
(bondsupermart_dashboard.html) with everything embedded: a top nav with live
search, country tabs, a filter bar, filter-reactive overview cards, six
interactive Chart.js charts, a client-side bond screener (sort / pagination /
rating badges) and a per-bond detail panel with price & yield history pulled
from bond_chart.

Light theme. One file, no backend -- Chart.js is the only external dependency
(loaded from a CDN), so the page needs internet access to render charts but no
server to run.

    python generate_dashboard.py
"""

import datetime as dt
import json
import sys

try:
    import mysql.connector
except ImportError:
    sys.exit("mysql-connector-python is required: pip install mysql-connector-python")

DB_CONFIG = {
    "host": "localhost",
    "port": 3306,
    "user": "root",
    "password": "qaz123wsx",
    "database": "bondsupermart",
}

OUT_FILE = "bondsupermart_dashboard.html"

MAX_POINTS = 80
EPOCH = dt.date(1970, 1, 1).toordinal()
DATE_MIN = dt.date(2000, 1, 1)
DATE_MAX = dt.date(2027, 1, 1)


def f(v):
    return float(v) if v is not None else None


def iso(d):
    return d.isoformat() if d is not None else None


def fetch_bonds(cur, live_set):
    cols = [
        "isin", "cusip", "bond_issuer", "guarantor", "bond_currency",
        "coupon_rate", "coupon_type", "coupon_frequency", "maturity_date",
        "years_to_maturity", "issue_date", "announcement_date", "next_call_date",
        "seniority", "bond_type", "bond_sector", "bond_sub_sector",
        "fitch_rating", "sp_rating_finra", "moodys_rating_finra",
        "bid_price", "ask_price", "bid_ytm", "ask_ytm", "bid_ytw", "ask_ytw",
        "issue_price", "issue_yield", "total_issue_size", "min_investment",
        "exchange_listed", "shariah_compliant", "ticker", "country",
    ]
    cur.execute("SELECT " + ", ".join(cols) + " FROM bonds")
    rows = cur.fetchall()
    bonds = []
    for r in rows:
        d = dict(zip(cols, r))
        bonds.append({
            "isin": d["isin"],
            "cusip": d["cusip"],
            "issuer": d["bond_issuer"],
            "guarantor": d["guarantor"],
            "ccy": d["bond_currency"],
            "coupon": f(d["coupon_rate"]),
            "couponType": d["coupon_type"],
            "couponFreq": d["coupon_frequency"],
            "maturity": iso(d["maturity_date"]),
            "years": f(d["years_to_maturity"]),
            "issueDate": iso(d["issue_date"]),
            "announce": iso(d["announcement_date"]),
            "nextCall": iso(d["next_call_date"]),
            "seniority": d["seniority"],
            "type": d["bond_type"],
            "sector": d["bond_sector"],
            "subSector": d["bond_sub_sector"],
            "fitch": d["fitch_rating"],
            "sp": d["sp_rating_finra"],
            "moody": d["moodys_rating_finra"],
            "bid": f(d["bid_price"]),
            "ask": f(d["ask_price"]),
            "bidYtm": f(d["bid_ytm"]),
            "askYtm": f(d["ask_ytm"]),
            "bidYtw": f(d["bid_ytw"]),
            "askYtw": f(d["ask_ytw"]),
            "issuePrice": f(d["issue_price"]),
            "issueYield": f(d["issue_yield"]),
            "issueSize": f(d["total_issue_size"]),
            "minInvest": f(d["min_investment"]),
            "exchange": d["exchange_listed"],
            "shariah": d["shariah_compliant"],
            "ticker": d["ticker"],
            "country": d["country"],
            "live": 1 if d["isin"] in live_set else 0,
        })
    return bonds


def downsample(points):
    n = len(points)
    if n == 0:
        return []
    if n > MAX_POINTS:
        idx = [round(i * (n - 1) / (MAX_POINTS - 1)) for i in range(MAX_POINTS)]
        seen, picked = set(), []
        for i in idx:
            if i not in seen:
                seen.add(i)
                picked.append(points[i])
        points = picked
    out = []
    for d, bp, ap, by, ay in points:
        out.append([d.toordinal() - EPOCH, bp, ap, by, ay])
    return out


def fetch_charts(cur):
    cur.execute("""
        SELECT isin, chart_date, bid_price, ask_price,
               bid_yield_to_worst, ask_yield_to_worst
        FROM bond_chart
        WHERE chart_date >= %s AND chart_date < %s
        ORDER BY isin, chart_date
    """, (DATE_MIN, DATE_MAX))

    charts = {}
    cur_isin = None
    buf = []

    def flush():
        if cur_isin is not None and buf:
            ds = downsample(buf)
            if ds:
                charts[cur_isin] = ds

    while True:
        rows = cur.fetchmany(20000)
        if not rows:
            break
        for isin, cdate, bp, ap, by, ay in rows:
            if isin != cur_isin:
                flush()
                cur_isin = isin
                buf = []
            bp, ap, by, ay = f(bp), f(ap), f(by), f(ay)
            if bp is None and ap is None and by is None and ay is None:
                continue
            buf.append((
                cdate,
                round(bp, 3) if bp is not None else None,
                round(ap, 3) if ap is not None else None,
                round(by, 4) if by is not None else None,
                round(ay, 4) if ay is not None else None,
            ))
    flush()
    return charts


def main():
    conn = mysql.connector.connect(**DB_CONFIG)
    cur = conn.cursor()

    cur.execute("SELECT DISTINCT isin FROM bond_prices")
    live_set = {r[0] for r in cur.fetchall()}

    cur.execute("SELECT MAX(price_updated_at) FROM bonds")
    last_upd = cur.fetchone()[0]
    last_upd = last_upd.strftime("%Y-%m-%d %H:%M") if last_upd else "n/a"

    print(f"  live-priced ISINs: {len(live_set)}")
    bonds = fetch_bonds(cur, live_set)
    print(f"  bonds: {len(bonds)}")
    charts = fetch_charts(cur)
    print(f"  ISINs with embedded history: {len(charts)}")

    cur.close()
    conn.close()

    bonds_json = json.dumps(bonds, ensure_ascii=True, separators=(",", ":")).replace("</", "<\\/")
    charts_json = json.dumps(charts, ensure_ascii=True, separators=(",", ":")).replace("</", "<\\/")

    html = (HTML_TEMPLATE
            .replace("%%GENERATED_AT%%", dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            .replace("%%LAST_UPDATED%%", last_upd)
            .replace("%%LIVE_COUNT%%", str(len(live_set)))
            .replace("/*BONDS*/null", bonds_json)
            .replace("/*CHARTS*/null", charts_json))

    with open(OUT_FILE, "w", encoding="utf-8") as fh:
        fh.write(html)

    import os
    size_mb = os.path.getsize(OUT_FILE) / (1024 * 1024)
    print(f"  wrote {OUT_FILE} ({size_mb:.1f} MB)")
    print(os.path.abspath(OUT_FILE))


# --------------------------------------------------------------------------- #
# HTML / CSS / JS template
# --------------------------------------------------------------------------- #
HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BondSupermart Intelligence</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
:root{
  --bg:#ffffff; --panel:#f5f7fa; --panel2:#ffffff; --line:#e2e8f0; --line2:#eef2f7;
  --txt:#1a2340; --mut:#64748b; --accent:#3b6fd4; --accent2:#2563eb;
  --hover:#f1f5f9; --head:#f8fafc;
  --dgreen:#15803d; --dgreenbg:#d9f0e1; --green:#16a34a; --greenbg:#e8f6ec;
  --lgreen:#65a30d; --lgreenbg:#eef7df; --orange:#ea8a0c; --orangebg:#fdf2e0;
  --red:#dc2626; --redbg:#fdeaea; --gray:#94a3b8; --graybg:#eef1f5; --graytx:#64748b;
  --shadow:0 1px 3px rgba(20,40,80,.06),0 1px 2px rgba(20,40,80,.04);
}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{background:var(--bg); color:var(--txt);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  font-size:13px; line-height:1.4;}
.num{font-family:"SF Mono",SFMono-Regular,Menlo,Consolas,monospace; text-align:right}

/* nav */
nav{display:flex; align-items:center; gap:18px; padding:12px 22px; border-bottom:1px solid var(--line);
  background:var(--panel2); position:sticky; top:0; z-index:30; box-shadow:var(--shadow)}
nav .brand{font-size:17px; font-weight:700; white-space:nowrap}
nav .brand .acc{color:var(--accent)}
nav .si{position:relative; flex:1; max-width:640px; margin:0 auto}
nav .si svg{position:absolute; left:12px; top:50%; transform:translateY(-50%); opacity:.5}
nav .si input{width:100%; background:var(--panel2); color:var(--txt); border:1px solid var(--line);
  border-radius:9px; padding:10px 14px 10px 36px; font-size:14px; outline:none}
nav .si input:focus{border-color:var(--accent); box-shadow:0 0 0 3px rgba(59,111,212,.12)}
nav .stats{display:flex; gap:18px; white-space:nowrap}
nav .stats .s .n{font-weight:700; font-family:"SF Mono",Menlo,monospace; font-size:15px}
nav .stats .s .l{color:var(--mut); font-size:10px; text-transform:uppercase; letter-spacing:.5px}

.wrap{padding:14px 22px; max-width:1760px; margin:0 auto}
h2.sec{font-size:11px; text-transform:uppercase; letter-spacing:1.3px; color:var(--mut); margin:22px 0 10px; font-weight:700}

.tabs{display:flex; gap:6px; flex-wrap:wrap; margin:12px 0}
.tab{padding:7px 13px; border:1px solid var(--line); background:var(--panel2); color:var(--mut);
  border-radius:8px; cursor:pointer; font-weight:600; font-size:12px; user-select:none}
.tab:hover{border-color:var(--accent); color:var(--accent)}
.tab.on{background:var(--accent); border-color:var(--accent); color:#fff}

.filters{display:flex; gap:12px 14px; flex-wrap:wrap; align-items:flex-end;
  background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:12px 14px; margin-bottom:14px}
.fg{display:flex; flex-direction:column; gap:4px}
.fg label{font-size:10px; text-transform:uppercase; letter-spacing:.6px; color:var(--mut); font-weight:700}
.fg select{background:var(--panel2); color:var(--txt); border:1px solid var(--line); border-radius:7px;
  padding:7px 9px; font-size:13px; outline:none; min-width:120px}
.fg select:focus{border-color:var(--accent)}
.slider{display:flex; flex-direction:column; gap:3px; min-width:190px}
.slider .rng{display:flex; align-items:center; gap:6px}
.slider input[type=range]{width:74px; accent-color:var(--accent)}
.slider .v{font-family:"SF Mono",Menlo,monospace; font-size:11px; min-width:34px; text-align:center}
.tg{display:flex; align-items:center; gap:7px; font-size:12px; font-weight:600; cursor:pointer; padding-bottom:6px}
.tg input{width:16px; height:16px; accent-color:var(--accent); cursor:pointer}
.reset{margin-left:auto; align-self:center; background:var(--panel2); color:var(--accent2);
  border:1px solid var(--accent); border-radius:8px; padding:8px 14px; cursor:pointer; font-weight:700; font-size:12px}
.reset:hover{background:var(--accent); color:#fff}

.cards{display:grid; grid-template-columns:repeat(6,1fr); gap:12px}
.card{background:var(--panel2); border:1px solid var(--line); border-radius:10px; padding:13px 15px;
  position:relative; overflow:hidden; box-shadow:var(--shadow)}
.card::before{content:""; position:absolute; left:0; top:0; bottom:0; width:3px; background:var(--accent)}
.card .lbl{color:var(--mut); font-size:11px; text-transform:uppercase; letter-spacing:.5px; font-weight:600}
.card .val{font-size:24px; font-weight:700; margin-top:5px; font-family:"SF Mono",Menlo,monospace}

.charts{display:grid; grid-template-columns:repeat(4,1fr); gap:12px}
.chartbox{background:var(--panel2); border:1px solid var(--line); border-radius:10px; padding:12px 14px; box-shadow:var(--shadow)}
.chartbox h3{margin:0 0 4px; font-size:12px; font-weight:700}
.chartbox .hint{font-size:10px; color:var(--mut); margin-bottom:6px; min-height:13px}
.chartbox .cv{position:relative; height:200px}
.chartbox.wide{grid-column:span 2}
.chartbox.wide .cv{height:250px}

.tablehdr{display:flex; align-items:center; gap:12px; margin-bottom:8px}
.tablehdr .cnt{font-weight:700}
.tablewrap{border:1px solid var(--line); border-radius:10px; overflow:auto; background:var(--panel2); box-shadow:var(--shadow)}
table{border-collapse:collapse; width:100%; font-size:12px}
thead th{position:sticky; top:0; background:var(--head); z-index:5; text-align:left; padding:10px;
  border-bottom:1px solid var(--line); white-space:nowrap; cursor:pointer; color:var(--mut); font-weight:700; user-select:none}
thead th:hover{color:var(--accent)}
thead th .ar{font-size:10px; color:var(--accent)}
tbody td{padding:8px 10px; border-bottom:1px solid var(--line2); white-space:nowrap}
tbody tr{cursor:pointer}
tbody tr:hover{background:var(--hover)}
tbody tr.sel{background:#e7eefb; outline:1px solid var(--accent)}
td.num{font-family:"SF Mono",Menlo,monospace}
tr.row-aaa td:first-child,tr.row-a td:first-child,tr.row-bbb td:first-child{box-shadow:inset 3px 0 0 var(--green)}
tr.row-bb td:first-child{box-shadow:inset 3px 0 0 var(--orange)}
tr.row-b td:first-child{box-shadow:inset 3px 0 0 var(--red)}
tr.row-nr td:first-child{box-shadow:inset 3px 0 0 var(--gray)}
.badge{display:inline-block; padding:2px 7px; border-radius:6px; font-size:11px; font-weight:700; font-family:"SF Mono",Menlo,monospace}
.b-aaa{color:#fff; background:var(--dgreen)}
.b-a{color:var(--dgreen); background:var(--greenbg)}
.b-bbb{color:var(--lgreen); background:var(--lgreenbg)}
.b-bb{color:#b45309; background:var(--orangebg)}
.b-b{color:#b91c1c; background:var(--redbg)}
.b-nr{color:var(--graytx); background:var(--graybg)}
.live{color:var(--green); font-weight:700}

/* search autocomplete */
.acdd{position:absolute; top:100%; left:0; right:0; margin-top:6px; background:#fff;
  border:1px solid var(--line); border-radius:10px; box-shadow:0 10px 30px rgba(20,40,80,.18);
  max-height:62vh; overflow-y:auto; overflow-x:hidden; z-index:60; display:none}
.acsec{font-size:10px; text-transform:uppercase; letter-spacing:.7px; color:var(--mut); font-weight:700;
  padding:9px 14px 5px; background:var(--head)}
.acgrp{border-bottom:1px solid var(--line)}
.acgh{display:flex; justify-content:space-between; align-items:center; gap:10px; padding:10px 14px;
  cursor:pointer; background:var(--panel)}
.acgh:hover{background:#e7eefb}
.acco{font-weight:700; font-size:13px}
.actk{color:var(--accent); font-weight:700}
.accnt{color:var(--mut); font-size:11px; white-space:nowrap; font-weight:600}
.acrow{display:flex; align-items:center; gap:10px 12px; flex-wrap:wrap; padding:7px 14px 7px 22px;
  cursor:pointer; font-size:12px; border-top:1px solid var(--line2)}
.acrow:hover{background:var(--hover)}
.acisin{font-family:"SF Mono",Menlo,monospace; font-weight:600; min-width:118px}
.acc1{font-family:"SF Mono",Menlo,monospace; color:var(--mut); white-space:nowrap}
.acbd{margin-left:auto; display:flex; gap:5px}
.acempty{padding:16px; color:var(--mut); text-align:center}
.acmore{padding:8px 14px; color:var(--mut); font-size:11px; text-align:center; background:var(--head)}
.acdd mark{background:#fde68a; color:inherit; border-radius:2px; padding:0 1px}

.pager{display:flex; align-items:center; gap:6px; padding:10px; flex-wrap:wrap; justify-content:center}
.pager button{background:var(--panel2); color:var(--txt); border:1px solid var(--line); border-radius:7px;
  padding:6px 11px; cursor:pointer; font-size:12px; font-weight:600}
.pager button:hover:not(:disabled){border-color:var(--accent); color:var(--accent)}
.pager button.on{background:var(--accent); border-color:var(--accent); color:#fff}
.pager button:disabled{opacity:.4; cursor:default}
.pager .info{color:var(--mut); margin:0 8px; font-size:12px}

#overlay{position:fixed; inset:0; background:rgba(20,30,60,.25); display:none; z-index:40}
#panel{position:fixed; top:0; right:0; height:100%; width:540px; max-width:95vw; background:var(--panel2);
  border-left:1px solid var(--line); z-index:50; transform:translateX(100%); transition:transform .22s ease;
  display:flex; flex-direction:column; box-shadow:-8px 0 24px rgba(20,40,80,.12)}
#panel.open{transform:none}
#panel .ph{display:flex; align-items:flex-start; gap:10px; padding:16px 18px; border-bottom:1px solid var(--line); background:var(--head)}
#panel .ph .t{flex:1; min-width:0}
#panel .ph .t .iss{font-size:15px; font-weight:700; word-wrap:break-word}
#panel .ph .t .sub{color:var(--mut); font-size:12px; margin-top:3px}
#panel .x{cursor:pointer; color:var(--mut); font-size:24px; line-height:1; padding:0 4px}
#panel .x:hover{color:var(--txt)}
#panel .body{overflow:auto; padding:16px 18px}
.rbadges{display:flex; gap:8px; margin-bottom:14px}
.rbadges .rb{flex:1; background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:9px; text-align:center}
.rbadges .rb .l{font-size:10px; color:var(--mut); text-transform:uppercase; letter-spacing:.5px; margin-bottom:5px}
.stats2{display:grid; grid-template-columns:repeat(3,1fr); gap:10px; margin-bottom:14px}
.stat{background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:10px 12px}
.stat .l{color:var(--mut); font-size:10px; text-transform:uppercase; letter-spacing:.5px; font-weight:600}
.stat .v{font-size:18px; font-weight:700; margin-top:4px; font-family:"SF Mono",Menlo,monospace}
.dctrl{display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin-bottom:8px}
.toggle{display:inline-flex; border:1px solid var(--line); border-radius:8px; overflow:hidden}
.toggle button{background:var(--panel); color:var(--mut); border:0; padding:6px 15px; cursor:pointer; font-size:12px; font-weight:700}
.toggle button.on{background:var(--accent); color:#fff}
.ranges{display:flex; gap:5px; flex-wrap:wrap}
.ranges button{background:var(--panel); border:1px solid var(--line); color:var(--mut); border-radius:6px;
  padding:5px 10px; cursor:pointer; font-size:11px; font-weight:700}
.ranges button.on{background:var(--accent); border-color:var(--accent); color:#fff}
#detchartwrap{position:relative; height:230px; background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:8px; margin-bottom:14px}
#nohist{color:var(--mut); padding:24px 0; text-align:center; display:none}
.kv{display:grid; grid-template-columns:1fr 1fr; gap:0 18px}
.kv .row{display:flex; justify-content:space-between; gap:10px; border-bottom:1px solid var(--line2); padding:6px 0}
.kv .row .k{color:var(--mut)}
.kv .row .v{font-family:"SF Mono",Menlo,monospace; text-align:right}
.kv h4{grid-column:1/-1; margin:12px 0 2px; font-size:11px; text-transform:uppercase; letter-spacing:.7px; color:var(--accent2)}

@media(max-width:1300px){.cards{grid-template-columns:repeat(3,1fr)} .charts{grid-template-columns:repeat(2,1fr)}}
@media(max-width:760px){nav{flex-wrap:wrap} .cards{grid-template-columns:repeat(2,1fr)} .charts{grid-template-columns:1fr} .chartbox.wide{grid-column:span 1} .stats2{grid-template-columns:1fr}}
</style>
</head>
<body>
<nav>
  <div class="brand">Bond<span class="acc">Supermart</span> Intelligence</div>
  <div class="si">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#1a2340" stroke-width="2"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
    <input type="text" id="q" placeholder="Search by issuer, ticker, or ISIN..." autocomplete="off">
    <div id="acdd" class="acdd"></div>
  </div>
  <div class="stats">
    <div class="s"><div class="n" id="navTotal"></div><div class="l">Total Bonds</div></div>
    <div class="s"><div class="n">%%LAST_UPDATED%%</div><div class="l">Last Updated</div></div>
  </div>
</nav>

<div class="wrap">

  <div class="tabs" id="tabs"></div>

  <div class="filters">
    <div class="fg"><label>Currency</label><select id="fCcy"><option value="">All</option></select></div>
    <div class="fg"><label>Sector</label><select id="fSec"><option value="">All</option></select></div>
    <div class="fg"><label>Bond Type</label><select id="fType"><option value="">All</option></select></div>
    <div class="fg"><label>Fitch Rating</label><select id="fFitch"><option value="">All</option></select></div>
    <div class="fg"><label>S&amp;P Rating</label><select id="fSP"><option value="">All</option></select></div>
    <div class="slider"><label>Years to Maturity</label>
      <div class="rng"><input type="range" id="matMin"><span class="v" id="matMinV"></span>
        <span style="color:var(--mut)">&ndash;</span><span class="v" id="matMaxV"></span><input type="range" id="matMax"></div></div>
    <div class="slider"><label>Bid YTM (%)</label>
      <div class="rng"><input type="range" id="yMin"><span class="v" id="yMinV"></span>
        <span style="color:var(--mut)">&ndash;</span><span class="v" id="yMaxV"></span><input type="range" id="yMax"></div></div>
    <label class="tg"><input type="checkbox" id="igOnly">Investment grade only</label>
    <label class="tg"><input type="checkbox" id="shOnly">Shariah compliant</label>
    <button class="reset" id="reset">Reset all filters</button>
  </div>

  <h2 class="sec">Overview</h2>
  <div class="cards" id="cards"></div>

  <h2 class="sec">Analytics &mdash; click a slice or bar to filter</h2>
  <div class="charts">
    <div class="chartbox"><h3>Bonds by Currency</h3><div class="hint">click to filter</div><div class="cv"><canvas id="cCcy"></canvas></div></div>
    <div class="chartbox"><h3>Bonds by Sector</h3><div class="hint">click to filter</div><div class="cv"><canvas id="cSec"></canvas></div></div>
    <div class="chartbox"><h3>Bonds by Fitch Rating</h3><div class="hint">click to filter</div><div class="cv"><canvas id="cFitch"></canvas></div></div>
    <div class="chartbox"><h3>Average Yield by Currency</h3><div class="hint">&nbsp;</div><div class="cv"><canvas id="cAvgCcy"></canvas></div></div>
    <div class="chartbox wide"><h3>Yield (Bid YTM) vs Years to Maturity &mdash; by Sector</h3><div class="hint">hover for details</div><div class="cv"><canvas id="cScatter"></canvas></div></div>
    <div class="chartbox wide"><h3>Coupon Rate Distribution</h3><div class="hint">&nbsp;</div><div class="cv"><canvas id="cCoupon"></canvas></div></div>
  </div>

  <h2 class="sec">Bond Screener</h2>
  <div class="tablehdr"><span class="cnt" id="tcount"></span></div>
  <div class="tablewrap">
    <table id="tbl"><thead><tr id="thr"></tr></thead><tbody id="tb"></tbody></table>
    <div class="pager" id="pager"></div>
  </div>

</div>

<div id="overlay"></div>
<aside id="panel">
  <div class="ph">
    <div class="t"><div class="iss" id="pIss"></div><div class="sub" id="pSub"></div></div>
    <div class="x" id="pClose">&times;</div>
  </div>
  <div class="body">
    <div class="rbadges">
      <div class="rb"><div class="l">Fitch</div><div id="rbF"></div></div>
      <div class="rb"><div class="l">S&amp;P</div><div id="rbS"></div></div>
      <div class="rb"><div class="l">Moody's</div><div id="rbM"></div></div>
    </div>
    <div class="stats2">
      <div class="stat"><div class="l">Current Price (bid)</div><div class="v" id="sPrice"></div></div>
      <div class="stat"><div class="l">Current Yield (YTW)</div><div class="v" id="sYield"></div></div>
      <div class="stat"><div class="l">Bid/Ask Spread</div><div class="v" id="sSpread"></div></div>
    </div>
    <div class="dctrl">
      <div class="toggle"><button id="tgPrice" class="on">Price</button><button id="tgYield">Yield</button></div>
      <div class="ranges" id="ranges">
        <button data-r="30">1M</button><button data-r="91">3M</button><button data-r="182">6M</button>
        <button data-r="365">1Y</button><button data-r="1095">3Y</button><button data-r="0" class="on">All</button>
      </div>
    </div>
    <div id="detchartwrap"><canvas id="detchart"></canvas></div>
    <div id="nohist">No historical chart data for this bond.</div>
    <div class="kv" id="pKv"></div>
  </div>
</aside>

<script>
const BONDS = /*BONDS*/null;
const CHARTS = /*CHARTS*/null;
const LIVE_COUNT = %%LIVE_COUNT%%;

/* ---------- rating logic ---------- */
const SP_SCALE = ["AAA","AA+","AA","AA-","A+","A","A-","BBB+","BBB","BBB-",
  "BB+","BB","BB-","B+","B","B-","CCC+","CCC","CCC-","CC","C","D"];
const MD_SCALE = ["Aaa","Aa1","Aa2","Aa3","A1","A2","A3","Baa1","Baa2","Baa3",
  "Ba1","Ba2","Ba3","B1","B2","B3","Caa1","Caa2","Caa3","Ca","C"];
function cleanSP(r){ if(!r) return null; let s=(""+r).trim().replace(/sf$/i,"").replace(/u$/i,""); return SP_SCALE.includes(s)?s:null; }
function cleanMD(r){ if(!r) return null; let s=(""+r).trim(); return MD_SCALE.includes(s)?s:null; }
// fine-grained badge bucket for color
function badgeClass(v){
  const s=cleanSP(v);
  if(s){ if(s.startsWith("AAA")||s.startsWith("AA")) return "aaa"; if(s.startsWith("A")) return "a";
    if(s.startsWith("BBB")) return "bbb"; if(s.startsWith("BB")) return "bb"; return "b"; }
  const m=cleanMD(v);
  if(m){ if(m==="Aaa"||m.startsWith("Aa")) return "aaa"; if(/^A[123]$/.test(m)) return "a";
    if(m.startsWith("Baa")) return "bbb"; if(m.startsWith("Ba")) return "bb"; return "b"; }
  return "nr";
}
const IG_BUCKETS=new Set(["aaa","a","bbb"]);
function rowBucket(b){ for(const v of [b.sp,b.fitch,b.moody]){ const c=badgeClass(v); if(c!=="nr") return c; } return "nr"; }
function isIG(b){ return IG_BUCKETS.has(rowBucket(b)); }
// grade buckets used by the Fitch/S&P dropdowns + Fitch chart ("AAA..D" or "NR")
function fitchGrade(b){ return cleanSP(b.fitch)||"NR"; }
function spGrade(b){ return cleanSP(b.sp)||"NR"; }

/* ---------- helpers ---------- */
const $=id=>document.getElementById(id);
function num(x,d=2){ return (x===null||x===undefined||isNaN(x))?"&mdash;":Number(x).toLocaleString("en-US",{minimumFractionDigits:d,maximumFractionDigits:d}); }
function plain(x){ return (x===null||x===undefined||x==="")?"&mdash;":x; }
function money(x){ if(x===null||x===undefined) return "&mdash;"; const a=Math.abs(x);
  if(a>=1e9) return (x/1e9).toFixed(2)+"B"; if(a>=1e6) return (x/1e6).toFixed(2)+"M"; if(a>=1e3) return (x/1e3).toFixed(1)+"K"; return ""+x; }
function isoFromStored(v){ return new Date(v*86400000).toISOString().slice(0,10); }
function badge(v){ return `<span class="badge b-${badgeClass(v)}">${plain(v)}</span>`; }
function avg(arr){ return arr.length? arr.reduce((s,x)=>s+x,0)/arr.length : null; }

/* ---------- state ---------- */
const byIsin={}; BONDS.forEach(b=>byIsin[b.isin]=b);
const NAMED=new Set(["United States","Eurobond","Malaysia","Hong Kong","Australia","Singapore","France","United Kingdom","Thailand"]);
const F={q:"",country:"",ccy:"",sec:"",type:"",fitch:"",sp:"",matMin:0,matMax:0,yMin:0,yMax:0,ig:false,sh:false,tickerExact:"",issuerExact:""};
let view=[...BONDS], sortKey="issuer", sortDir=1, page=1;
const PAGE_SIZE=50;
const charts={};

const yrsVals=BONDS.map(b=>b.years).filter(v=>v!=null);
const ytmVals=BONDS.map(b=>b.bidYtm).filter(v=>v!=null&&v>=-20&&v<=60);
const MAT_LO=0, MAT_HI=Math.min(100,Math.ceil(Math.max(...yrsVals)));
const Y_LO=Math.max(-10,Math.floor(Math.min(...ytmVals))), Y_HI=Math.min(40,Math.ceil(Math.max(...ytmVals)));

/* ---------- overview cards (recomputed from view) ---------- */
function buildCards(){
  const yields=view.map(b=>b.bidYtm).filter(v=>v!=null&&v>=-5&&v<=30);
  const yrs=view.map(b=>b.years).filter(v=>v!=null);
  const cps=view.map(b=>b.coupon).filter(v=>v!=null);
  const sprd=view.map(b=>(b.ask!=null&&b.bid!=null)?b.ask-b.bid:null).filter(v=>v!=null);
  const igPct=view.length? 100*view.filter(isIG).length/view.length : 0;
  const cards=[
    ["Bonds Shown", view.length.toLocaleString()],
    ["Avg Bid Yield", yields.length?avg(yields).toFixed(2)+"%":"&mdash;"],
    ["Avg Years to Mat", yrs.length?avg(yrs).toFixed(1):"&mdash;"],
    ["Avg Coupon Rate", cps.length?avg(cps).toFixed(2)+"%":"&mdash;"],
    ["Investment Grade", view.length?igPct.toFixed(0)+"%":"&mdash;"],
    ["Avg Bid-Ask Spread", sprd.length?avg(sprd).toFixed(3):"&mdash;"],
  ];
  $("cards").innerHTML=cards.map(c=>`<div class="card"><div class="lbl">${c[0]}</div><div class="val">${c[1]}</div></div>`).join("");
}

/* ---------- charts ---------- */
Chart.defaults.color="#64748b";
Chart.defaults.font.family="-apple-system,Segoe UI,Roboto,sans-serif";
Chart.defaults.borderColor="#e2e8f0";
const PALETTE=["#3b6fd4","#5aa6ff","#16a34a","#ea8a0c","#dc2626","#9333ea","#0891b2",
  "#db2777","#65a30d","#ea580c","#0d9488","#7c3aed","#e11d48","#4f46e5"];
function destroyCharts(){ Object.values(charts).forEach(c=>c&&c.destroy()); }
function countBy(arr,key){ const m={}; arr.forEach(b=>{const k=b[key]; if(k==null||k==="")return; m[k]=(m[k]||0)+1;}); return Object.entries(m).sort((a,b)=>b[1]-a[1]); }
function mkDonut(id,key,onClick){
  const e=countBy(view,key);
  charts[id]=new Chart($(id),{type:"doughnut",
    data:{labels:e.map(x=>x[0]),datasets:[{data:e.map(x=>x[1]),backgroundColor:e.map((_,i)=>PALETTE[i%PALETTE.length]),borderColor:"#fff",borderWidth:1.5}]},
    options:{maintainAspectRatio:false,cutout:"58%",plugins:{legend:{position:"right",labels:{boxWidth:10,font:{size:10}}}},
      onClick:(ev,el)=>{ if(el.length) onClick(e[el[0].index][0]); }}});
}
function mkFitchBar(){
  const m={}; view.forEach(b=>{ const g=fitchGrade(b); m[g]=(m[g]||0)+1; });
  const labels=SP_SCALE.filter(s=>m[s]); if(m["NR"]) labels.push("NR");
  const colors=labels.map(s=>{ const c=badgeClass(s); return {aaa:"#15803d",a:"#16a34a",bbb:"#65a30d",bb:"#ea8a0c",b:"#dc2626",nr:"#94a3b8"}[c]; });
  charts.cFitch=new Chart($("cFitch"),{type:"bar",
    data:{labels,datasets:[{data:labels.map(s=>m[s]),backgroundColor:colors,borderWidth:0}]},
    options:{maintainAspectRatio:false,plugins:{legend:{display:false}},
      scales:{x:{grid:{display:false}},y:{beginAtZero:true,grid:{color:"#eef2f7"}}},
      onClick:(ev,el)=>{ if(el.length){ $("fFitch").value=labels[el[0].index]; F.fitch=labels[el[0].index]; apply(); } }}});
}
function mkScatter(){
  const sectors=[...new Set(view.map(b=>b.sector).filter(Boolean))];
  const ds=sectors.map((sec,i)=>({label:sec,
    data:view.filter(b=>b.sector===sec&&b.years!=null&&b.bidYtm!=null&&b.years>=0&&b.years<=50&&b.bidYtm>=-5&&b.bidYtm<=25)
      .map(b=>({x:b.years,y:b.bidYtm,isin:b.isin,iss:b.issuer})),
    backgroundColor:PALETTE[i%PALETTE.length],pointRadius:2.5,pointHoverRadius:5}));
  charts.cScatter=new Chart($("cScatter"),{type:"scatter",data:{datasets:ds},
    options:{maintainAspectRatio:false,
      plugins:{legend:{position:"right",labels:{boxWidth:9,font:{size:10}}},
        tooltip:{callbacks:{label:c=>`${c.raw.iss}: ${c.raw.y.toFixed(2)}% @ ${c.raw.x.toFixed(1)}y`}}},
      scales:{x:{title:{display:true,text:"Years to Maturity"},grid:{color:"#eef2f7"}},
              y:{title:{display:true,text:"Bid YTM (%)"},grid:{color:"#eef2f7"}}},
      onClick:(ev,el)=>{ if(el.length){const d=ds[el[0].datasetIndex].data[el[0].index]; openDetail(d.isin);} }}});
}
function mkAvgCcy(){
  const sum={},n={};
  view.forEach(b=>{const k=b.ccy; if(k==null||b.bidYtm==null||b.bidYtm<-5||b.bidYtm>30)return; sum[k]=(sum[k]||0)+b.bidYtm; n[k]=(n[k]||0)+1;});
  const labels=Object.keys(sum).sort((a,b)=>(sum[b]/n[b])-(sum[a]/n[a]));
  charts.cAvgCcy=new Chart($("cAvgCcy"),{type:"bar",
    data:{labels,datasets:[{data:labels.map(k=>sum[k]/n[k]),backgroundColor:labels.map((_,i)=>PALETTE[i%PALETTE.length]),borderWidth:0}]},
    options:{maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{callbacks:{label:i=>"Avg YTM "+i.raw.toFixed(2)+"%"}}},
      scales:{x:{grid:{display:false}},y:{beginAtZero:true,grid:{color:"#eef2f7"},title:{display:true,text:"Avg YTM (%)"}}}}});
}
function mkCoupon(){
  const buckets=["0-1","1-2","2-3","3-4","4-5","5-6","6-7","7-8","8-9","9-10","10+"];
  const cnt=new Array(buckets.length).fill(0);
  view.forEach(b=>{ if(b.coupon==null)return; let i=Math.floor(b.coupon); if(i>10)i=10; if(i<0)i=0; cnt[i]++; });
  charts.cCoupon=new Chart($("cCoupon"),{type:"bar",
    data:{labels:buckets,datasets:[{data:cnt,backgroundColor:"#3b6fd4",borderWidth:0}]},
    options:{maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{callbacks:{title:i=>"Coupon "+buckets[i[0].dataIndex]+"%"}}},
      scales:{x:{grid:{display:false},title:{display:true,text:"Coupon rate (%)"}},y:{beginAtZero:true,grid:{color:"#eef2f7"}}}}});
}
function buildCharts(){
  destroyCharts();
  mkDonut("cCcy","ccy",v=>{ $("fCcy").value=v; F.ccy=v; apply(); });
  mkDonut("cSec","sector",v=>{ $("fSec").value=v; F.sec=v; apply(); });
  mkFitchBar(); mkScatter(); mkAvgCcy(); mkCoupon();
}

/* ---------- table ---------- */
const COLS=[
  {k:"issuer",t:"Bond Issuer"},{k:"ticker",t:"Ticker"},{k:"isin",t:"ISIN"},
  {k:"country",t:"Country"},{k:"ccy",t:"Ccy"},
  {k:"coupon",t:"Coupon",n:1,fmt:v=>num(v,3)},{k:"maturity",t:"Maturity"},
  {k:"years",t:"Yrs",n:1,fmt:v=>num(v,1)},{k:"sector",t:"Sector"},
  {k:"fitch",t:"Fitch",bdg:1},{k:"sp",t:"S&P",bdg:1},{k:"moody",t:"Moody",bdg:1},
  {k:"bid",t:"Bid",n:1,fmt:v=>num(v,3)},{k:"ask",t:"Ask",n:1,fmt:v=>num(v,3)},
  {k:"bidYtm",t:"Bid YTM",n:1,fmt:v=>num(v,3)},
  {k:"spread",t:"Spread",n:1,fmt:v=>num(v,3)},{k:"issueSize",t:"Issue Size",n:1,fmt:v=>money(v)},
];
function spreadOf(b){ return (b.ask!=null&&b.bid!=null)?b.ask-b.bid:null; }
function buildHead(){
  $("thr").innerHTML=COLS.map(c=>`<th data-k="${c.k}" class="${c.n?'num':''}">${c.t} <span class="ar" data-k="${c.k}"></span></th>`).join("");
  document.querySelectorAll("#thr th").forEach(th=>th.onclick=()=>{
    const k=th.dataset.k; if(sortKey===k) sortDir*=-1; else {sortKey=k; sortDir=1;} page=1; render();
  });
}
function fillSelect(id,vals){ $(id).insertAdjacentHTML("beforeend", vals.map(v=>`<option>${v}</option>`).join("")); }
function gradeOptions(fn){
  const present=new Set(BONDS.map(fn));
  const opts=SP_SCALE.filter(s=>present.has(s)); if(present.has("NR")) opts.push("NR"); return opts;
}

function apply(){
  F.q=$("q").value.trim().toLowerCase();
  view=BONDS.filter(b=>{
    if(F.tickerExact){ if(b.ticker!==F.tickerExact) return false; }
    else if(F.issuerExact){ if(b.issuer!==F.issuerExact) return false; }
    else if(F.q){ const hay=((b.issuer||"")+" "+(b.ticker||"")+" "+(b.isin||"")).toLowerCase(); if(!hay.includes(F.q)) return false; }
    if(F.country){ if(F.country==="Other"){ if(NAMED.has(b.country)) return false; } else if(b.country!==F.country) return false; }
    if(F.ccy && b.ccy!==F.ccy) return false;
    if(F.sec && b.sector!==F.sec) return false;
    if(F.type && b.type!==F.type) return false;
    if(F.fitch && fitchGrade(b)!==F.fitch) return false;
    if(F.sp && spGrade(b)!==F.sp) return false;
    if(F.ig && !isIG(b)) return false;
    if(F.sh && b.shariah!=="Y") return false;
    if(b.years!=null){ if(b.years<F.matMin||b.years>F.matMax) return false; } else if(F.matMin>MAT_LO) return false;
    if(b.bidYtm!=null){ if(b.bidYtm<F.yMin||b.bidYtm>F.yMax) return false; } else if(F.yMin>Y_LO) return false;
    return true;
  });
  page=1; buildCards(); render(); buildCharts();
  $("tcount").textContent=`${view.length.toLocaleString()} of ${BONDS.length.toLocaleString()} bonds`;
}
function cellVal(b,k){ return k==="spread"?spreadOf(b):b[k]; }
function render(){
  view.sort((a,b)=>{ let x=cellVal(a,sortKey),y=cellVal(b,sortKey);
    const xn=(x==null),yn=(y==null); if(xn&&yn)return 0; if(xn)return 1; if(yn)return -1;
    if(typeof x==="number"&&typeof y==="number") return (x-y)*sortDir;
    return (""+x).localeCompare(""+y)*sortDir; });
  document.querySelectorAll("#thr .ar").forEach(a=>a.textContent=a.dataset.k===sortKey?(sortDir>0?"▲":"▼"):"");
  const pages=Math.max(1,Math.ceil(view.length/PAGE_SIZE)); if(page>pages)page=pages;
  const start=(page-1)*PAGE_SIZE, slice=view.slice(start,start+PAGE_SIZE);
  const rows=slice.map(b=>{
    let c="";
    for(const col of COLS){
      let v=(col.k==="spread")?spreadOf(b):b[col.k];
      if(col.bdg) v=badge(v); else if(col.fmt) v=col.fmt(v); else v=plain(v);
      c+=`<td class="${col.n?'num':''}">${v}</td>`;
    }
    return `<tr class="row-${rowBucket(b)}" data-isin="${b.isin}">${c}</tr>`;
  });
  $("tb").innerHTML=rows.join("");
  document.querySelectorAll("#tb tr").forEach(tr=>tr.onclick=()=>openDetail(tr.dataset.isin,tr));
  renderPager(pages,start,slice.length);
}
function renderPager(pages,start,shown){
  const info=view.length?`${start+1}–${start+shown} of ${view.length.toLocaleString()}`:"0 results";
  let h=`<button id="pPrev" ${page<=1?"disabled":""}>&lsaquo; Prev</button>`;
  const win=[1]; for(let p=page-2;p<=page+2;p++) if(p>1&&p<pages) win.push(p); if(pages>1) win.push(pages);
  let prev=0;
  for(const p of [...new Set(win)].sort((a,b)=>a-b)){ if(p-prev>1) h+=`<span class="info">&hellip;</span>`; h+=`<button class="pg ${p===page?'on':''}" data-p="${p}">${p}</button>`; prev=p; }
  h+=`<button id="pNext" ${page>=pages?"disabled":""}>Next &rsaquo;</button><span class="info">${info}</span>`;
  $("pager").innerHTML=h;
  $("pPrev")&&($("pPrev").onclick=()=>{if(page>1){page--;render();}});
  $("pNext")&&($("pNext").onclick=()=>{if(page<pages){page++;render();}});
  document.querySelectorAll(".pg").forEach(b=>b.onclick=()=>{page=+b.dataset.p;render();});
}

/* ---------- tabs ---------- */
const TABS=["All","United States","Eurobond","Malaysia","Hong Kong","Australia","Singapore","France","United Kingdom","Thailand","Other"];
function buildTabs(){ $("tabs").innerHTML=TABS.map(t=>`<div class="tab ${t==='All'?'on':''}" data-c="${t==='All'?'':t}">${t}</div>`).join("");
  document.querySelectorAll(".tab").forEach(t=>t.onclick=()=>setTab(t.dataset.c)); }
function setTab(country){ F.country=country||""; document.querySelectorAll(".tab").forEach(t=>t.classList.toggle("on",t.dataset.c===F.country)); apply(); }

/* ---------- detail panel ---------- */
let detChart=null,detISIN=null,detMode="price",detRange=0;
function openDetail(isin,tr){
  const b=byIsin[isin]; if(!b) return; detISIN=isin;
  document.querySelectorAll("#tb tr.sel").forEach(t=>t.classList.remove("sel"));
  if(tr) tr.classList.add("sel"); else { const m=document.querySelector(`#tb tr[data-isin="${isin}"]`); if(m) m.classList.add("sel"); }
  $("pIss").innerHTML=(b.issuer||"&mdash;")+(b.live?` <span class="live" title="live exchange price">&#9679; live</span>`:"");
  $("pSub").textContent=`${b.ticker?b.ticker+" · ":""}${b.isin} · ${b.country||""} · ${b.ccy||""} · ${b.sector||""}`;
  $("rbF").innerHTML=badge(b.fitch); $("rbS").innerHTML=badge(b.sp); $("rbM").innerHTML=badge(b.moody);
  const spread=spreadOf(b);
  $("sPrice").innerHTML=num(b.bid,3);
  $("sYield").innerHTML=(b.bidYtw!=null?num(b.bidYtw,3):num(b.bidYtm,3))+"%";
  $("sSpread").innerHTML=num(spread,3);
  $("pKv").innerHTML=detailKv(b);
  drawDet();
  $("overlay").style.display="block"; $("panel").classList.add("open");
}
function detailKv(b){
  const r=(k,v)=>`<div class="row"><span class="k">${k}</span><span class="v">${v}</span></div>`;
  return `<h4>Profile</h4>`
    +r("Country",plain(b.country))+r("Ticker",plain(b.ticker))+r("Sector",plain(b.sector))
    +r("Type",plain(b.type))+r("Seniority",plain(b.seniority))+r("Shariah",plain(b.shariah))
    +`<h4>Terms</h4>`
    +r("Coupon",b.coupon!=null?num(b.coupon,3)+"% "+(b.couponType||""):"&mdash;")+r("Frequency",plain(b.couponFreq))
    +r("Maturity",plain(b.maturity))+r("Years to Mat",b.years!=null?num(b.years,2):"&mdash;")
    +r("Next Call",plain(b.nextCall))+r("Issue Date",plain(b.issueDate))
    +r("Issue Price",num(b.issuePrice,3))+r("Issue Yield",b.issueYield!=null?num(b.issueYield,3)+"%":"&mdash;")
    +`<h4>Size &amp; Identity</h4>`
    +r("Issue Size",money(b.issueSize))+r("Min Invest",money(b.minInvest))
    +r("Currency",plain(b.ccy))+r("Exchange",plain(b.exchange))
    +r("CUSIP",plain(b.cusip))+r("Guarantor",plain(b.guarantor))
    +`<h4>Live Pricing</h4>`
    +r("Bid / Ask",num(b.bid,3)+" / "+num(b.ask,3))
    +r("Bid/Ask YTM",num(b.bidYtm,3)+" / "+num(b.askYtm,3))
    +r("Bid/Ask YTW",num(b.bidYtw,3)+" / "+num(b.askYtw,3));
}
function slicedHistory(){
  let h=CHARTS[detISIN]||[];
  if(detRange>0&&h.length){ const cutoff=h[h.length-1][0]-detRange; h=h.filter(p=>p[0]>=cutoff); }
  return h;
}
function drawDet(){
  if(detChart){ detChart.destroy(); detChart=null; }
  const h=slicedHistory();
  if(!h.length){ $("nohist").style.display="block"; $("detchartwrap").style.display="none"; return; }
  $("nohist").style.display="none"; $("detchartwrap").style.display="block";
  const labels=h.map(p=>isoFromStored(p[0]));
  let d1,d2,l1,l2;
  if(detMode==="price"){ d1=h.map(p=>p[1]); d2=h.map(p=>p[2]); l1="Bid Price"; l2="Ask Price"; }
  else { d1=h.map(p=>p[3]); d2=h.map(p=>p[4]); l1="Bid YTW"; l2="Ask YTW"; }
  detChart=new Chart($("detchart"),{type:"line",
    data:{labels,datasets:[
      {label:l1,data:d1,borderColor:"#3b6fd4",backgroundColor:"rgba(59,111,212,.10)",borderWidth:1.5,pointRadius:0,tension:.15,fill:true,spanGaps:true},
      {label:l2,data:d2,borderColor:"#ea8a0c",backgroundColor:"transparent",borderWidth:1.5,pointRadius:0,tension:.15,spanGaps:true}]},
    options:{maintainAspectRatio:false,interaction:{mode:"index",intersect:false},
      plugins:{legend:{labels:{boxWidth:12,font:{size:11}}}},
      scales:{x:{ticks:{maxTicksLimit:6,font:{size:10}},grid:{display:false}},y:{grid:{color:"#eef2f7"},ticks:{font:{size:10}}}}}});
}
function closeDetail(){ $("overlay").style.display="none"; $("panel").classList.remove("open");
  document.querySelectorAll("#tb tr.sel").forEach(t=>t.classList.remove("sel")); }

/* ---------- search autocomplete ---------- */
const AC_MAX_GROUPS=10;
function esc(s){ return (""+s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }
function escRe(s){ return s.replace(/[.*+?^${}()|[\]\\]/g,"\\$&"); }
function hl(text,q){ const t=esc(text==null?"":text); const eq=esc(q); if(!eq) return t;
  return t.replace(new RegExp("("+escRe(eq)+")","ig"),"<mark>$1</mark>"); }
function matMY(isoDate){ if(!isoDate) return "—"; const p=(""+isoDate).split("-"); return p.length>=2? p[1]+"/"+p[0] : isoDate; }
function tickerCompany(bonds){ const m={}; bonds.forEach(b=>{ if(b.issuer) m[b.issuer]=(m[b.issuer]||0)+1; });
  let best="—",bc=-1; for(const k in m){ if(m[k]>bc){bc=m[k];best=k;} } return best; }
function buildGroups(q){
  const ql=q.toLowerCase(), shown=new Set();
  const tmap={};
  BONDS.forEach(b=>{ if(b.ticker && b.ticker.toLowerCase().includes(ql)){ (tmap[b.ticker]=tmap[b.ticker]||[]).push(b); } });
  let tg=Object.keys(tmap).map(tk=>({ticker:tk,issuer:tickerCompany(tmap[tk]),bonds:tmap[tk]}));
  tg.sort((a,b)=>((a.ticker.toLowerCase().startsWith(ql)?0:1)-(b.ticker.toLowerCase().startsWith(ql)?0:1))||(b.bonds.length-a.bonds.length));
  const tTrunc=tg.length>AC_MAX_GROUPS; tg=tg.slice(0,AC_MAX_GROUPS);
  tg.forEach(g=>g.bonds.forEach(b=>shown.add(b.isin)));
  const imap={};
  BONDS.forEach(b=>{ if(b.issuer && b.issuer.toLowerCase().includes(ql) && !shown.has(b.isin)){ (imap[b.issuer]=imap[b.issuer]||[]).push(b); } });
  let ig=Object.keys(imap).map(iss=>({ticker:(imap[iss].find(x=>x.ticker)||{}).ticker||"",issuer:iss,bonds:imap[iss]}));
  ig.sort((a,b)=>((a.issuer.toLowerCase().startsWith(ql)?0:1)-(b.issuer.toLowerCase().startsWith(ql)?0:1))||(b.bonds.length-a.bonds.length));
  const iTrunc=ig.length>AC_MAX_GROUPS; ig=ig.slice(0,AC_MAX_GROUPS);
  return {tg,ig,trunc:tTrunc||iTrunc};
}
function groupHtml(g,q){
  const head=`<div class="acgh" data-tk="${g.ticker?esc(g.ticker):''}" data-iss="${esc(g.issuer)}">`
    +`<span class="acco">${hl(g.issuer,q)}${g.ticker?` <span class="actk">(${hl(g.ticker,q)})</span>`:""}</span>`
    +`<span class="accnt">${g.bonds.length} bond${g.bonds.length>1?'s':''}</span></div>`;
  const rows=g.bonds.map(b=>`<div class="acrow" data-isin="${b.isin}">`
    +`<span class="acisin">${hl(b.isin,q)}</span>`
    +`<span class="acc1">${b.coupon!=null?num(b.coupon,3)+'%':'—'}</span>`
    +`<span class="acc1">Matures ${matMY(b.maturity)}</span>`
    +`<span class="acc1">Bid YTM ${b.bidYtm!=null?num(b.bidYtm,2)+'%':'—'}</span>`
    +`<span class="acc1">Bid ${b.bid!=null?num(b.bid,2):'—'}</span>`
    +`<span class="acbd">${badge(b.fitch)}${badge(b.sp)}</span></div>`).join("");
  return `<div class="acgrp">${head}${rows}</div>`;
}
function renderAC(){
  const q=$("q").value.trim();
  if(q.length<2){ hideAC(); return; }
  const {tg,ig,trunc}=buildGroups(q);
  if(!tg.length && !ig.length){ $("acdd").innerHTML=`<div class="acempty">No matches for "${esc(q)}"</div>`; $("acdd").style.display="block"; return; }
  let h="";
  if(tg.length){ h+=`<div class="acsec">Ticker matches</div>`+tg.map(g=>groupHtml(g,q)).join(""); }
  if(ig.length){ h+=`<div class="acsec">Issuer name matches</div>`+ig.map(g=>groupHtml(g,q)).join(""); }
  if(trunc) h+=`<div class="acmore">More results — keep typing to narrow down</div>`;
  $("acdd").innerHTML=h; $("acdd").style.display="block";
  $("acdd").querySelectorAll(".acrow").forEach(r=>r.onclick=e=>{ e.stopPropagation(); selectBond(r.dataset.isin); });
  $("acdd").querySelectorAll(".acgh").forEach(g=>g.onclick=e=>{ e.stopPropagation(); selectCompany(g.dataset.tk,g.dataset.iss); });
}
function hideAC(){ $("acdd").style.display="none"; }
function selectBond(isin){ hideAC(); openDetail(isin); }
function selectCompany(tk,iss){
  if(tk){ F.tickerExact=tk; F.issuerExact=""; $("q").value=tk; }
  else { F.issuerExact=iss; F.tickerExact=""; $("q").value=iss; }
  hideAC(); apply();
}

/* ---------- sliders ---------- */
function initSliders(){
  [["matMin",MAT_LO,MAT_HI,MAT_LO,1],["matMax",MAT_LO,MAT_HI,MAT_HI,1],
   ["yMin",Y_LO,Y_HI,Y_LO,0.5],["yMax",Y_LO,Y_HI,Y_HI,0.5]].forEach(([id,lo,hi,val,st])=>{
    const el=$(id); el.min=lo; el.max=hi; el.step=st; el.value=val; });
  F.matMin=MAT_LO; F.matMax=MAT_HI; F.yMin=Y_LO; F.yMax=Y_HI; syncSliderLabels();
  ["matMin","matMax","yMin","yMax"].forEach(id=>$(id).addEventListener("input",()=>{
    let mn=+$("matMin").value, mx=+$("matMax").value; if(mn>mx){ if(id==="matMin")$("matMax").value=mn; else $("matMin").value=mx; }
    let yn=+$("yMin").value, yx=+$("yMax").value; if(yn>yx){ if(id==="yMin")$("yMax").value=yn; else $("yMin").value=yx; }
    F.matMin=+$("matMin").value; F.matMax=+$("matMax").value; F.yMin=+$("yMin").value; F.yMax=+$("yMax").value;
    syncSliderLabels(); apply();
  }));
}
function syncSliderLabels(){
  $("matMinV").textContent=$("matMin").value; $("matMaxV").textContent=$("matMax").value;
  $("yMinV").textContent=(+$("yMin").value).toFixed(1); $("yMaxV").textContent=(+$("yMax").value).toFixed(1);
}

/* ---------- reset ---------- */
function resetAll(){
  hideAC();
  $("q").value=""; ["fCcy","fSec","fType","fFitch","fSP"].forEach(id=>$(id).value="");
  Object.assign(F,{q:"",country:"",ccy:"",sec:"",type:"",fitch:"",sp:"",ig:false,sh:false,tickerExact:"",issuerExact:""});
  $("igOnly").checked=false; $("shOnly").checked=false;
  $("matMin").value=MAT_LO; $("matMax").value=MAT_HI; $("yMin").value=Y_LO; $("yMax").value=Y_HI;
  F.matMin=MAT_LO; F.matMax=MAT_HI; F.yMin=Y_LO; F.yMax=Y_HI; syncSliderLabels();
  document.querySelectorAll(".tab").forEach(t=>t.classList.toggle("on",t.dataset.c===""));
  sortKey="issuer"; sortDir=1; apply();
}

/* ---------- init ---------- */
$("navTotal").textContent=BONDS.length.toLocaleString();
buildHead(); buildTabs(); initSliders();
fillSelect("fCcy",[...new Set(BONDS.map(b=>b.ccy).filter(Boolean))].sort());
fillSelect("fSec",[...new Set(BONDS.map(b=>b.sector).filter(Boolean))].sort());
fillSelect("fType",[...new Set(BONDS.map(b=>b.type).filter(Boolean))].sort());
fillSelect("fFitch",gradeOptions(fitchGrade));
fillSelect("fSP",gradeOptions(spGrade));

let qTimer; $("q").addEventListener("input",()=>{ F.tickerExact=""; F.issuerExact=""; renderAC(); clearTimeout(qTimer); qTimer=setTimeout(apply,180); });
$("q").addEventListener("focus",renderAC);
document.addEventListener("click",e=>{ if(!e.target.closest(".si")) hideAC(); });
$("fCcy").addEventListener("change",e=>{F.ccy=e.target.value;apply();});
$("fSec").addEventListener("change",e=>{F.sec=e.target.value;apply();});
$("fType").addEventListener("change",e=>{F.type=e.target.value;apply();});
$("fFitch").addEventListener("change",e=>{F.fitch=e.target.value;apply();});
$("fSP").addEventListener("change",e=>{F.sp=e.target.value;apply();});
$("igOnly").addEventListener("change",e=>{F.ig=e.target.checked;apply();});
$("shOnly").addEventListener("change",e=>{F.sh=e.target.checked;apply();});
$("reset").onclick=resetAll;
$("pClose").onclick=closeDetail; $("overlay").onclick=closeDetail;
$("tgPrice").onclick=()=>{detMode="price";$("tgPrice").classList.add("on");$("tgYield").classList.remove("on");drawDet();};
$("tgYield").onclick=()=>{detMode="yield";$("tgYield").classList.add("on");$("tgPrice").classList.remove("on");drawDet();};
document.querySelectorAll("#ranges button").forEach(b=>b.onclick=()=>{
  detRange=+b.dataset.r; document.querySelectorAll("#ranges button").forEach(x=>x.classList.remove("on")); b.classList.add("on"); drawDet(); });
document.addEventListener("keydown",e=>{if(e.key==="Escape"){hideAC();closeDetail();}});
apply();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
