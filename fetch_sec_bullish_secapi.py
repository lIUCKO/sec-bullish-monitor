#!/usr/bin/env python3
import os, sys, json, time
from datetime import datetime, timedelta, timezone
import requests
import pandas as pd

SEC_API_URL = os.getenv("SEC_API_URL", "").strip()
SEC_API_KEY = os.getenv("SEC_API_KEY", "").strip()
AUTH_SCHEME = (os.getenv("AUTH_SCHEME") or "bearer").lower()
LOOKBACK_HOURS = int(os.getenv("LOOKBACK_HOURS", "72"))
MAX_RESULTS = int(os.getenv("MAX_RESULTS", "2000"))
OUT_DIR = os.getenv("OUT_DIR", ".")
RSS_FILE = os.getenv("RSS_FILE", "sec-bullish.xml")

if not SEC_API_URL or not SEC_API_KEY:
    print("ERROR: SEC_API_URL i/ili SEC_API_KEY nisu postavljeni (Secrets)!", file=sys.stderr)
    sys.exit(1)

def headers():
    h = {"Content-Type": "application/json"}
    if AUTH_SCHEME == "x-api-key":
        h["x-api-key"] = SEC_API_KEY
    else:
        # default bearer
        h["Authorization"] = f"Bearer {SEC_API_KEY}"
    return h

def sec_query(payload: dict):
    r = requests.post(SEC_API_URL, headers=headers(), data=json.dumps(payload), timeout=60)
    print(f"HTTP: {r.status_code} | len(query): {payload.get('size')}")
    r.raise_for_status()
    data = r.json()
    # SEC-API vraća {"hits": {"hits": [...]}} ili {"filings": [...]}, ovisno o endpointu
    if isinstance(data, dict) and "hits" in data and "hits" in data["hits"]:
        return [h["_source"] if "_source" in h else h for h in data["hits"]["hits"]]
    if isinstance(data, dict) and "filings" in data:
        return data["filings"]
    # fallback – ako je već lista
    if isinstance(data, list):
        return data
    return []

def now_range(hours):
    return (datetime.now(timezone.utc) - timedelta(hours=hours), datetime.now(timezone.utc))

SINCE, UNTIL = now_range(LOOKBACK_HOURS)
since_str = SINCE.strftime("%Y-%m-%dT%H:%M:%SZ")
until_str = UNTIL.strftime("%Y-%m-%dT%H:%M:%SZ")

def save_outputs(name, rows):
    json_path = os.path.join(OUT_DIR, f"{name}.json")
    csv_path = os.path.join(OUT_DIR, f"{name}.csv")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    df = pd.DataFrame(rows)
    df.to_csv(csv_path, index=False)
    print(f"Saved {len(rows)} -> {os.path.basename(json_path)},  {os.path.basename(csv_path)}")
    return json_path, csv_path

# ---------- FILTER FUNKCIJE ----------

BULLISH_TERMS = [
  "guidance raised","raised guidance","increase guidance","increased guidance",
  "increased outlook","improved outlook","raise outlook","boost outlook",
  "share repurchase","buyback","repurchase program","authorization to repurchase",
  "material agreement","strategic partnership","license agreement","licensing agreement",
  "collaboration","supply agreement","commercial agreement","cooperation",
  "uplist","special dividend","initial dividend"
]

def is_8k_bullish(f):
    items = " ".join([str(i) for i in f.get("items",[])])
    text = " ".join([
        str(f.get("documentText") or ""),
        str(f.get("summary") or ""),
        str(f.get("text") or ""),
        str(f.get("documents", "")),
    ]).lower()
    hit_item = any(x in items for x in ["2.02","7.01","1.01"])
    hit_text = any(k in text for k in BULLISH_TERMS)
    return hit_item or hit_text

def is_8k_material_agreement(f):
    items = " ".join([str(i) for i in f.get("items",[])])
    text = " ".join([
        str(f.get("documentText") or ""),
        str(f.get("summary") or ""),
        str(f.get("text") or ""),
    ]).lower()
    return "1.01" in items or "material agreement" in text

def is_form4_buy(tx):
    code = (tx.get("transactionCode") or "").upper()
    sh = float(tx.get("transactionShares") or 0)
    price = float(tx.get("transactionPricePerShare") or 0)
    return code in {"P","A"} and (sh > 0 or price > 0)

def is_13_position(f):
    form = (f.get("formType") or "").upper()
    return form in {"SC 13D","SC 13D/A","SC 13G","SC 13G/A"}

def normalize_row(f):
    # pokušaj standardizirati osnovne stupce
    cik = f.get("cik") or f.get("issuerCik") or f.get("companyCik") or ""
    ticker = (f.get("ticker") or f.get("issuerTradingSymbol") or f.get("companyTicker") or "")
    company = (f.get("companyName") or f.get("issuerName") or f.get("companyNameLong") or "")
    form = f.get("formType") or f.get("form") or ""
    filed = f.get("filedAt") or f.get("filingDate") or f.get("acceptedDateTime") or ""
    url = f.get("linkToFilingDetails") or f.get("linkToFiling") or f.get("documentUrl") or ""
    return {
        "cik": cik, "ticker": ticker, "company": company,
        "form": form, "filedAt": filed, "url": url
    }

# ---------- QUERY-JI ----------

def q_form4(hours=LOOKBACK_HOURS):
    return {
      "query": {
        "query_string": {
          "query": f'formType:"4" AND filedAt:[NOW-{hours}HOURS TO NOW] AND (nonDerivativeTable.transactionTable.transaction.transactionCode:(P OR A))'
        }
      },
      "from": 0, "size": MAX_RESULTS, "sort": [{"filedAt": {"order":"desc"}}]
    }

def q_8k(hours=LOOKBACK_HOURS):
    terms = "\"buyback\" OR \"share repurchase\" OR \"guidance raised\" OR \"increased outlook\" OR \"material agreement\" OR partnership OR collaboration OR licensing"
    return {
      "query": {
        "query_string": {
          "query": f'formType:"8-K" AND filedAt:[NOW-{hours}HOURS TO NOW] AND (items:("2.02" OR "7.01" OR "1.01") OR text:({terms}))'
        }
      },
      "from": 0, "size": MAX_RESULTS, "sort": [{"filedAt": {"order":"desc"}}]
    }

def q_13(hours=LOOKBACK_HOURS):
    return {
      "query": {
        "query_string": {
          "query": f'(formType:("SC 13D" OR "SC 13D/A" OR "SC 13G" OR "SC 13G/A")) AND filedAt:[NOW-{hours}HOURS TO NOW]'
        }
      },
      "from": 0, "size": MAX_RESULTS, "sort": [{"filedAt": {"order":"desc"}}]
    }

def q_10q(hours=LOOKBACK_HOURS):
    # širi 10-Q, kasnije dodatno filtriramo po “raised”/“increase” u MD&A
    return {
      "query": {
        "query_string": {
          "query": f'formType:"10-Q" AND filedAt:[NOW-{hours}HOURS TO NOW]'
        }
      },
      "from": 0, "size": MAX_RESULTS, "sort": [{"filedAt": {"order":"desc"}}]
    }

# ---------- MAIN FETCH ----------

def fetch_form4_buys():
    raw = sec_query(q_form4())
    print(f"Fetched Form4 raw: {len(raw)}")
    rows = []
    for f in raw:
        txs = []
        # SEC-API zna imati transakcije na više mjesta – pokušaj pronaći listu
        try:
            txs = f.get("nonDerivativeTable",{}).get("transactionTable",{}).get("transaction",[])
            if isinstance(txs, dict):
                txs = [txs]
        except Exception:
            txs = []
        ok = any(is_form4_buy(tx) for tx in txs) if txs else False
        if ok:
            rows.append(normalize_row(f))
    print(f"After filter (Form4 buys): {len(rows)}")
    return rows

def fetch_8k_bullish():
    raw = sec_query(q_8k())
    print(f"Fetched 8-K raw: {len(raw)}")
    rows = [normalize_row(f) for f in raw if is_8k_bullish(f)]
    print(f"After filter (8-K bullish): {len(rows)}")
    return rows

def fetch_8k_material_agreements():
    raw = sec_query(q_8k())
    rows = [normalize_row(f) for f in raw if is_8k_material_agreement(f)]
    print(f"After filter (8-K material agreements): {len(rows)}")
    return rows

def fetch_13D_13G():
    raw = sec_query(q_13())
    print(f"Fetched 13D/13G raw: {len(raw)}")
    rows = [normalize_row(f) for f in raw if is_13_position(f)]
    print(f"After filter (13D/13G): {len(rows)}")
    return rows

def fetch_10q_bullish():
    raw = sec_query(q_10q())
    print(f"Fetched 10-Q raw: {len(raw)}")
    rows = []
    for f in raw:
        text = " ".join([
            str(f.get("documentText") or ""),
            str(f.get("summary") or ""),
            str(f.get("text") or "")
        ]).lower()
        bull = any(k in text for k in ["raised", "increase", "improve", "buyback", "repurchase"])
        if bull:
            rows.append(normalize_row(f))
    print(f"After filter (10-Q bullish): {len(rows)}")
    return rows

def build_rss(all_items, out_file):
    # minimalni RSS za feed čitače
    from xml.sax.saxutils import escape
    site = "https://iiucko.github.io/sec-bullish-monitor/"
    rss_items = []
    for it in sorted(all_items, key=lambda x: x.get("filedAt",""), reverse=True)[:500]:
        title = escape(f"{it.get('ticker') or it.get('company','?')} — {it.get('form','')}")
        link = escape(it.get("url") or site)
        pub = escape(it.get("filedAt") or "")
        desc = escape(f"{it.get('company','')} | CIK {it.get('cik','')}")
        rss_items.append(f"<item><title>{title}</title><link>{link}</link><pubDate>{pub}</pubDate><description>{desc}</description></item>")
    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
  <title>SEC Bullish Monitor — PRO</title>
  <link>{site}</link>
  <description>Automatski bullish SEC feed (Form 4 buys, 8-K bullish, 13D/G, 10-Q bullish)</description>
  {''.join(rss_items)}
</channel>
</rss>
"""
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(rss)
    print(f"RSS built -> {out_file} (items: {len(rss_items)})")

def main():
    print(f"Window: {since_str} .. {until_str}  (LOOKBACK_HOURS={LOOKBACK_HOURS})")

    f4 = fetch_form4_buys()
    save_outputs("Form4_buys", f4)

    k8 = fetch_8k_bullish()
    save_outputs("8K_bullish", k8)

    k8_ma = fetch_8k_material_agreements()
    save_outputs("8K_material_agreements", k8_ma)

    g13 = fetch_13D_13G()
    save_outputs("13D_13G", g13)

    q10 = fetch_10q_bullish()
    save_outputs("10Q_bullish", q10)

    all_items = []
    for block in (f4, k8, k8_ma, g13, q10):
        all_items.extend(block)
    build_rss(all_items, os.path.join(OUT_DIR, RSS_FILE))

if __name__ == "__main__":
    main()
