#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SEC Bullish Monitor (SEC-API)
Autor: GPT-5

Opis:
 - Povezuje se na SEC API endpoint (https://api.sec-api.io/query)
 - Povlači bullish kategorije (8-K bullish, 8-K agreements, Form4 buys, 10-Q bullish, 13D/13G)
 - Sprema rezultate u JSON + CSV
 - Generira RSS feed (sec-bullish.xml) + kopiju u public/feed.xml ako postoji

ENV VARS (GitHub Secrets):
  SEC_API_URL  = https://api.sec-api.io
  SEC_API_KEY  = <tvoj ključ>
  LOOKBACK_HOURS = 168
"""

import os
import csv
import json
import time
import datetime as dt
import requests
from typing import Dict, Any, List

# --- CONFIG / ENV ---
SEC_API_URL = (os.getenv("SEC_API_URL", "https://api.sec-api.io") or "").strip()
SEC_API_URL = SEC_API_URL.replace("\r", "").replace("\n", "").rstrip("/")

SEC_API_KEY = (os.getenv("SEC_API_KEY", "") or "").strip()
LOOKBACK_HOURS = int(os.getenv("LOOKBACK_HOURS", "168"))

if not SEC_API_KEY:
    raise SystemExit("❌ ERROR: SEC_API_KEY nije postavljen u GitHub Secrets.")

HEADERS = {
    "Authorization": SEC_API_KEY,
    "Content-Type": "application/json"
}

# --- HELPER FUNKCIJE ---
def now_utc_iso():
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

def date_range_lucene(hours: int) -> str:
    return f"[now-{hours}h TO now]"

def save_json(path: str, data: Any):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def save_csv(path: str, rows: List[Dict[str, Any]], field_order: List[str] = None):
    if not rows:
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(["empty"])
        return
    keys = set()
    for r in rows:
        keys.update(r.keys())
    ordered = field_order or sorted(keys)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=ordered)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in ordered})

def normalize_filing(f: Dict[str, Any]) -> Dict[str, Any]:
    link = f.get("linkToFiling") or f.get("linkToHtml") or f.get("linkToFilingDetails") or ""
    filed_at = f.get("filedAt") or f.get("filedOn") or ""
    company = f.get("companyName") or f.get("company", {}).get("name") or ""
    ticker = f.get("ticker") or f.get("company", {}).get("ticker") or ""
    form = f.get("formType") or f.get("form") or ""
    cik = f.get("cik") or f.get("company", {}).get("cik") or ""
    title = f.get("documentTitle") or f.get("title") or f.get("description") or ""
    return {
        "formType": form,
        "filedAt": filed_at,
        "companyName": company,
        "ticker": ticker,
        "title": title,
        "link": link,
        "cik": cik
    }

# --- NOVI endpoint handler ---
def post_query(payload: Dict[str, Any]) -> Dict[str, Any]:
    # ako u secretu već postoji /query, ne dodaj ponovo
    endpoint = SEC_API_URL if SEC_API_URL.endswith("/query") else f"{SEC_API_URL}/query"
    print(f"POST {endpoint}  (Authorization header)")
    r = requests.post(endpoint, headers=HEADERS, json=payload, timeout=60)
    r.raise_for_status()
    return r.json()

def run_query_to_files(name: str, lucene_query: str, size: int = 100) -> List[Dict[str, Any]]:
    payload = {
        "query": {"query_string": {"query": lucene_query}},
        "from": 0,
        "size": size,
        "sort": [{"filedAt": {"order": "desc"}}]
    }
    data = post_query(payload)
    filings = data.get("filings") or data.get("data") or data.get("results") or []
    rows = [normalize_filing(f) for f in filings]
    save_json(f"{name}.json", rows)
    save_csv(f"{name}.csv", rows, field_order=["formType", "filedAt", "companyName", "ticker", "title", "link", "cik"])
    print(f"✅ Saved {len(rows)} → {name}.json / .csv")
    return rows

# --- QUERY DEFINICIJE ---
def q_8k_bullish(h: int) -> str:
    terms = [
        "guidance raised", "raises guidance", "increase guidance",
        "share repurchase", "buyback", "dividend increase",
        "contract award", "wins contract", "strategic partnership",
        "FDA approval", "breakthrough", "uplisting", "reinstates dividend"
    ]
    expr = "(" + " OR ".join([f"\"{t}\"" for t in terms]) + ")"
    return f'formType:"8-K" AND filedAt:{date_range_lucene(h)} AND {expr} NOT ("ATM" OR "at-the-market" OR "warrant" OR "S-1")'

def q_8k_material_agreements(h: int) -> str:
    return f'formType:"8-K" AND filedAt:{date_range_lucene(h)} AND (Item 1.01 OR "material definitive agreement") NOT ("ATM" OR "at-the-market")'

def q_form4_buys(h: int) -> str:
    return f'formType:"4" AND filedAt:{date_range_lucene(h)} AND transactionCode:"P"'

def q_10q_bullish(h: int) -> str:
    return f'formType:"10-Q" AND filedAt:{date_range_lucene(h)} AND ("raises guidance" OR "increase production" OR "improved liquidity" OR "improved gross margin")'

def q_13d_13g(h: int) -> str:
    return f'(formType:"SC 13D" OR formType:"SC 13G") AND filedAt:{date_range_lucene(h)}'

# --- RSS ---
def build_rss(rows: List[Dict[str, Any]]) -> str:
    def esc(s: str): return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    def sort_key(r): 
        try: return time.mktime(dt.datetime.strptime(r["filedAt"].replace("Z",""), "%Y-%m-%dT%H:%M:%S").timetuple())
        except: return 0
    items = sorted(rows, key=sort_key, reverse=True)
    rss = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0"><channel>',
        '<title>SEC Bullish Monitor</title>',
        '<description>8-K bullish / agreements / insider buys / 10-Q / 13D-G</description>',
        '<link>https://liucko.github.io/sec-bullish-monitor/</link>',
        f'<lastBuildDate>{dt.datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")}</lastBuildDate>'
    ]
    for r in items[:300]:
        rss.append("<item>")
        title = f'{r["formType"]} | {r["companyName"]} {("(" + r["ticker"] + ")") if r["ticker"] else ""}'
        rss.append(f"<title>{esc(title)}</title>")
        rss.append(f"<link>{esc(r['link'])}</link>")
        rss.append(f"<description>{esc(r.get('title',''))}</description>")
        rss.append(f"<pubDate>{esc(r.get('filedAt',''))}</pubDate>")
        rss.append("</item>")
    rss.append("</channel></rss>")
    return "\n".join(rss)

def write_rss(xml: str):
    with open("sec-bullish.xml", "w", encoding="utf-8") as f:
        f.write(xml)
    if os.path.isdir("public"):
        with open("public/feed.xml", "w", encoding="utf-8") as f:
            f.write(xml)
    print("📡 RSS feed saved → sec-bullish.xml")

# --- MAIN ---
def main():
    print(f"Starting @ {now_utc_iso()}  | lookback {LOOKBACK_HOURS}h")
    all_rows = []
    all_rows += run_query_to_files("8K_bullish", q_8k_bullish(LOOKBACK_HOURS), size=100)
    all_rows += run_query_to_files("8K_material_agreements", q_8k_material_agreements(LOOKBACK_HOURS), size=100)
    all_rows += run_query_to_files("Form4_buys", q_form4_buys(LOOKBACK_HOURS), size=150)
    all_rows += run_query_to_files("10Q_bullish", q_10q_bullish(LOOKBACK_HOURS), size=80)
    all_rows += run_query_to_files("13D_13G", q_13d_13g(LOOKBACK_HOURS), size=80)
    rss = build_rss(all_rows)
    write_rss(rss)
    print(f"✅ Done @ {now_utc_iso()} | total items: {len(all_rows)}")

if __name__ == "__main__":
    main()
