#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SEC Bullish Monitor (SEC-API) — single file, end-to-end
- Auth header: Authorization: <API_KEY>  (BEZ 'Bearer ')
- Endpoint:    https://api.sec-api.io/query

ENV VARS (GitHub Secrets):
  SEC_API_URL  = https://api.sec-api.io/query
  SEC_API_KEY  = your_key_here
  LOOKBACK_HOURS = 168   (opcionalno; default 168h)

Outputs u rootu:
  8K_bullish.json / .csv
  8K_material_agreements.json / .csv
  Form4_buys.json / .csv
  10Q_bullish.json / .csv
  13D_13G.json / .csv
  sec-bullish.xml
  (kopija RSS-a i u public/feed.xml ako postoji folder public/)
"""

import os
import csv
import json
import time
import datetime as dt
from typing import List, Dict, Any

import requests

SEC_API_URL = os.getenv("SEC_API_URL", "https://api.sec-api.io/query").rstrip("/")
SEC_API_KEY = os.getenv("SEC_API_KEY", "").strip()
LOOKBACK_HOURS = int(os.getenv("LOOKBACK_HOURS", "168"))

if not SEC_API_KEY:
    raise SystemExit("SEC_API_KEY nije postavljen.")

HEADERS = {
    "Authorization": SEC_API_KEY,            # bez 'Bearer '
    "Content-Type": "application/json"
}

# ----- util -----
def now_utc_iso() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

def date_range_lucene(hours: int) -> str:
    # SEC-API podržava relativno vrijeme: now-168h TO now
    return f"[now-{hours}h TO now]"

def post_query(payload: Dict[str, Any]) -> Dict[str, Any]:
    r = requests.post(f"{SEC_API_URL}/query", headers=HEADERS, json=payload, timeout=60)
    r.raise_for_status()
    return r.json()

def save_json(path: str, data: Any):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def save_csv(path: str, rows: List[Dict[str, Any]], field_order: List[str] = None):
    if not rows:
        # kreiraj prazan CSV s minimalnim headerom
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["empty"])
        return
    # sklopi polja
    keys = set()
    for r in rows:
        keys.update(r.keys())
    if field_order:
        ordered = [k for k in field_order if k in keys] + [k for k in sorted(keys) if k not in set(field_order)]
    else:
        ordered = sorted(keys)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=ordered)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in ordered})

def normalize_filing(f: Dict[str, Any]) -> Dict[str, Any]:
    """
    Polja u SEC-API response-u znaju varirati; ovo hvata uobičajene nazive.
    """
    # linkovi
    link = f.get("linkToFiling") or f.get("linkToHtml") or f.get("linkToFilingDetails") or ""
    # datumi
    filed_at = f.get("filedAt") or f.get("filedOn") or ""
    period = f.get("periodOfReport") or ""
    # firma / ticker
    company = f.get("companyName") or f.get("company", {}).get("name") or ""
    ticker = f.get("ticker") or f.get("company", {}).get("ticker") or ""
    form = f.get("formType") or f.get("form") or ""
    cik = f.get("cik") or f.get("company", {}).get("cik") or ""
    title = f.get("documentTitle") or f.get("title") or f.get("description") or ""

    return {
        "formType": form,
        "filedAt": filed_at,
        "periodOfReport": period,
        "companyName": company,
        "ticker": ticker,
        "cik": cik,
        "title": title,
        "link": link
    }

def run_query_to_files(name: str, lucene: str, size: int = 100) -> List[Dict[str, Any]]:
    payload = {
        "query": {"query_string": {"query": lucene}},
        "from": 0,
        "size": size,
        "sort": [{"filedAt": {"order": "desc"}}]
    }
    data = post_query(payload)
    filings = data.get("filings") or data.get("data") or data.get("results") or []
    rows = [normalize_filing(f) for f in filings]

    # JSON + CSV
    save_json(f"{name}.json", rows)
    save_csv(f"{name}.csv", rows, field_order=["formType", "filedAt", "companyName", "ticker", "title", "link", "cik", "periodOfReport"])
    print(f"Saved {len(rows)} → {name}.json, {name}.csv")
    return rows

# ----- upiti (lucene) -----
def q_8k_bullish(hours: int) -> str:
    # 8-K s pozitivnim ključnim pojmovima
    terms = [
        "guidance raised", "raises guidance", "increase guidance",
        "share repurchase", "buyback", "dividend increase",
        "contract award", "wins contract", "strategic partnership",
        "FDA approval", "breakthrough", "uplisting", "reinstates dividend"
    ]
    term_expr = "(" + " OR ".join([f"\"{t}\"" for t in terms]) + ")"
    return f'formType:"8-K" AND filedAt:{date_range_lucene(hours)} AND {term_expr} NOT ("ATM" OR "at-the-market" OR "warrant" OR "S-1")'

def q_8k_material_agreements(hours: int) -> str:
    # 1.01 - Entry into a Material Definitive Agreement
    return f'formType:"8-K" AND filedAt:{date_range_lucene(hours)} AND (Item 1.01 OR "material definitive agreement") NOT ("ATM" OR "at-the-market")'

def q_form4_buys(hours: int) -> str:
    # Insider purchase (transactionCode P)
    # SEC-API parsira Form 4 transakcije pa "transactionCode:P" često radi
    return f'formType:"4" AND filedAt:{date_range_lucene(hours)} AND transactionCode:"P"'

def q_10q_bullish(hours: int) -> str:
    # 10-Q s frazama koje mogu upućivati na dobar ton
    return f'formType:"10-Q" AND filedAt:{date_range_lucene(hours)} AND ("raises guidance" OR "increase production" OR "improved liquidity" OR "improved gross margin")'

def q_13d_13g(hours: int) -> str:
    # znatna vlasnička prijava
    return f'(formType:"SC 13D" OR formType:"SC 13G") AND filedAt:{date_range_lucene(hours)}'

# ----- RSS -----
def build_rss(all_rows: List[Dict[str, Any]]) -> str:
    """
    Vrati jednostavan RSS XML string za sve zapise spojeno (po filedAt desc).
    """
    # sort by filedAt (desc)
    def ts(x):
        val = x.get("filedAt", "")
        try:
            # filedAt je ISO (npr 2025-11-03T10:02:00Z)
            return time.mktime(dt.datetime.strptime(val.replace("Z",""), "%Y-%m-%dT%H:%M:%S").timetuple())
        except Exception:
            return 0

    items = sorted(all_rows, key=ts, reverse=True)[:300]

    def esc(s: str) -> str:
        return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    now_rfc = dt.datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")

    parts = []
    parts.append('<?xml version="1.0" encoding="UTF-8"?>')
    parts.append('<rss version="2.0"><channel>')
    parts.append("<title>SEC Bullish Monitor</title>")
    parts.append("<description>8-K bullish / material agreements, Form 4 insider buys, 10-Q bullish, 13D/G</description>")
    parts.append("<link>https://liucko.github.io/sec-bullish-monitor/</link>")
    parts.append(f"<lastBuildDate>{now_rfc}</lastBuildDate>")

    for r in items:
        title = f'{r.get("formType","")} | {r.get("companyName","")} {("("+r["ticker"]+")") if r.get("ticker") else ""}'
        desc = r.get("title") or ""
        link = r.get("link") or ""
        pub = r.get("filedAt") or ""
        parts.append("<item>")
        parts.append(f"<title>{esc(title)}</title>")
        parts.append(f"<description>{esc(desc)}</description>")
        parts.append(f"<link>{esc(link)}</link>")
        parts.append(f"<guid isPermaLink='true'>{esc(link)}</guid>")
        parts.append(f"<pubDate>{esc(pub)}</pubDate>")
        parts.append("</item>")

    parts.append("</channel></rss>")
    return "\n".join(parts)

def write_rss(xml: str):
    with open("sec-bullish.xml", "w", encoding="utf-8") as f:
        f.write(xml)
    # i kopija u public/feed.xml ako postoji public/
    if os.path.isdir("public"):
        with open(os.path.join("public", "feed.xml"), "w", encoding="utf-8") as f:
            f.write(xml)
    print("Saved RSS → sec-bullish.xml", "(+ public/feed.xml)" if os.path.isdir("public") else "")

# ----- main -----
def main():
    print(f"Starting @ {now_utc_iso()}  | lookback {LOOKBACK_HOURS}h")
    print(f"POST {SEC_API_URL}/query  (Authorization header)")

    all_rows: List[Dict[str, Any]] = []

    all_rows += run_query_to_files("8K_bullish", q_8k_bullish(LOOKBACK_HOURS), size=120)
    all_rows += run_query_to_files("8K_material_agreements", q_8k_material_agreements(LOOKBACK_HOURS), size=120)
    all_rows += run_query_to_files("Form4_buys", q_form4_buys(LOOKBACK_HOURS), size=200)
    all_rows += run_query_to_files("10Q_bullish", q_10q_bullish(LOOKBACK_HOURS), size=120)
    all_rows += run_query_to_files("13D_13G", q_13d_13g(LOOKBACK_HOURS), size=120)

    rss = build_rss(all_rows)
    write_rss(rss)

    print(f"Done @ {now_utc_iso()} | total items in RSS: {len(all_rows)}")

if __name__ == "__main__":
    main()
