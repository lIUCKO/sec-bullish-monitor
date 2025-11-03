import os, json, csv, requests
from urllib.parse import urlparse
from datetime import datetime, timedelta, timezone

SEC_API_URL = (os.getenv("SEC_API_URL") or "").strip()
SEC_API_KEY = (os.getenv("SEC_API_KEY") or "").strip()
LOOKBACK_HOURS = int(os.getenv("LOOKBACK_HOURS", "168"))
MAX_RESULTS = int(os.getenv("MAX_RESULTS", "2000"))

# ---- sanity checks ----
if not SEC_API_URL:
    raise SystemExit("❌ SEC_API_URL nije postavljen (Settings → Secrets → Actions).")
if not (SEC_API_URL.startswith("http://") or SEC_API_URL.startswith("https://")):
    raise SystemExit("❌ SEC_API_URL mora početi s http(s)://")
if not SEC_API_KEY:
    raise SystemExit("❌ SEC_API_KEY nije postavljen.")

def hdr():
    # Support potvrdio: Authorization header treba koristiti
    return {"Content-Type": "application/json", "Authorization": f"Bearer {SEC_API_KEY}"}

def post_with_auto_fix(url, payload):
    """
    Pokuša POST na dani URL.
    Ako dobije 404, automatski proba alternativu: dodati/ukloniti '/query'.
    Vraća (response_json, final_url)
    """
    def do_post(u):
        r = requests.post(u, headers=hdr(), json=payload, timeout=60)
        print(f"HTTP {r.status_code} @ {urlparse(u).netloc}{urlparse(u).path or '/'}")
        if r.status_code == 404:
            return None, 404
        r.raise_for_status()
        return r.json(), r.status_code

    # 1) pokušaj originalni
    data, code = do_post(url)
    if code != 404:
        return data, url

    # 2) ako je 404, probaj alternaciju s /query (dodaj ili ukloni)
    if url.rstrip("/").endswith("/query"):
        alt = url.rstrip("/").rsplit("/query", 1)[0] or url  # makni /query
    else:
        alt = url.rstrip("/") + "/query"                     # dodaj /query

    print(f"🔁 404 fallback: pokušavam {alt}")
    data, code = do_post(alt)
    if code == 404:
        raise SystemExit("❌ 404 na obje varijante URL-a (s /query i bez). Provjeri točan endpoint u SEC-API dashboardu.")
    return data, alt

def sec_fetch(query):
    data, final_url = post_with_auto_fix(SEC_API_URL, query)
    # SEC-API vraća različite oblike: {"filings":[...]} ili {"hits":{"hits":[...]}}
    if isinstance(data, dict):
        if "filings" in data:
            return data["filings"]
        if "hits" in data and "hits" in data["hits"]:
            return [h.get("_source", h) for h in data["hits"]["hits"]]
    if isinstance(data, list):
        return data
    return []

def save(rows, basename):
    if not rows:
        print(f"⚠️ 0 results -> {basename}.csv/json")
        open(f"{basename}.json","w").write("[]")
        open(f"{basename}.csv","w").write("")
        return
    keys = sorted(rows[0].keys())
    with open(f"{basename}.json","w",encoding="utf-8") as j:
        json.dump(rows, j, ensure_ascii=False, indent=2)
    with open(f"{basename}.csv","w",newline="",encoding="utf-8") as c:
        w = csv.DictWriter(c, fieldnames=keys); w.writeheader(); w.writerows(rows)
    print(f"Saved {len(rows)} -> {basename}.json, {basename}.csv")

def norm(f):
    def g(*ks):
        for k in ks:
            v = f.get(k)
            if v: return v
        return ""
    return {
        "ticker": g("ticker","issuerTradingSymbol","companyTicker"),
        "company": g("companyName","issuerName","companyNameLong"),
        "form": g("formType","form"),
        "filedAt": g("filedAt","filingDate","acceptedDateTime"),
        "url": g("linkToFilingDetails","linkToFiling","documentUrl"),
        "cik": g("cik","issuerCik","companyCik"),
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
      "from":0,"size":MAX_RESULTS,"sor_
