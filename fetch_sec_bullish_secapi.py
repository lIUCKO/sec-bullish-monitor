import os, requests, csv, json
from datetime import datetime, timedelta, timezone

SEC_API_KEY = os.getenv("SEC_API_KEY")
SEC_API_URL = os.getenv("SEC_API_URL", "https://api.sec-api.io/query")
HEADERS = {"Authorization": f"Bearer {SEC_API_KEY}"}

LOOKBACK_HOURS = int(os.getenv("LOOKBACK_HOURS", "168"))   # 7 dana
MAX_RESULTS = int(os.getenv("MAX_RESULTS", "2000"))

def fetch(query):
    r = requests.post(SEC_API_URL, headers=HEADERS, json=query, timeout=60)
    r.raise_for_status()
    data = r.json().get("filings", [])
    print(f"HTTP {r.status_code} | len(query): {len(data)}")
    return data

def save(rows, basename):
    if not rows:
        print(f"⚠️ No results -> {basename}.csv/json empty")
        return
    keys = sorted(rows[0].keys())
    with open(f"{basename}.json","w",encoding="utf-8") as j:
        json.dump(rows, j, ensure_ascii=False, indent=2)
    with open(f"{basename}.csv","w",newline="",encoding="utf-8") as c:
        w = csv.DictWriter(c, fieldnames=keys)
        w.writeheader(); w.writerows(rows)
    print(f"Saved {len(rows)} → {basename}.json, {basename}.csv")

def normalize(f):
    return {
        "ticker": f.get("ticker",""),
        "company": f.get("companyName",""),
        "form": f.get("formType",""),
        "filedAt": f.get("filedAt",""),
        "url": f.get("linkToFilingDetails",""),
        "cik": f.get("cik","")
    }

def q_8k(hours=LOOKBACK_HOURS):
    return {
      "query":{"query_string":{"query":f'formType:"8-K" AND filedAt:[NOW-{hours}HOURS TO NOW] AND items:("2.02" OR "7.01" OR "1.01" OR "5.02" OR "8.01")'}},
      "from":0,"size":MAX_RESULTS,"sort":[{"filedAt":{"order":"desc"}}]
    }

def q_13d13g(hours=LOOKBACK_HOURS):
    return {
      "query":{"query_string":{"query":f'formType:("SC 13D" OR "SC 13D/A" OR "SC 13G" OR "SC 13G/A") AND filedAt:[NOW-{hours}HOURS TO NOW]'}},
      "from":0,"size":MAX_RESULTS,"sort":[{"filedAt":{"order":"desc"}}]
    }

def q_form4(hours=LOOKBACK_HOURS):
    return {
      "query":{"query_string":{"query":f'formType:"4" AND filedAt:[NOW-{hours}HOURS TO NOW]'}},
      "from":0,"size":MAX_RESULTS,"sort":[{"filedAt":{"order":"desc"}}]
    }

def q_10q(hours=LOOKBACK_HOURS):
    return {
      "query":{"query_string":{"query":f'formType:"10-Q" AND filedAt:[NOW-{hours}HOURS TO NOW]'}},
      "from":0,"size":MAX_RESULTS,"sort":[{"filedAt":{"order":"desc"}}]
    }

def is_form4_buy(f):
    for tx in f.get("reportingOwners",[]):
        if isinstance(tx,dict):
            code = tx.get("transactionCode","").strip()
            sh = float(tx.get("transactionShares",0) or 0)
            price = float(tx.get("transactionPricePerShare",0) or 0)
            if code in {"P","A"} and (sh>0 or price>0):
                return True
    return False

def fetch_and_save():
    out = []
    data = fetch(q_8k()); save([normalize(x) for x in data], "8K_bullish"); out+=data
    data = fetch(q_13d13g()); save([normalize(x) for x in data], "13D_13G"); out+=data
    data = fetch(q_form4()); data=[x for x in data if is_form4_buy(x)]
    save([normalize(x) for x in data], "Form4_buys"); out+=data
    data = fetch(q_10q()); save([normalize(x) for x in data], "10Q_bullish"); out+=data
    print(f"✅ Total fetched/normalized: {len(out)} filings")

if __name__=="__main__":
    fetch_and_save()
