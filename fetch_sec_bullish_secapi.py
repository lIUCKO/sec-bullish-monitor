import os, json, csv, requests
from urllib.parse import urlparse

SEC_API_URL = (os.getenv("SEC_API_URL") or "").strip()
SEC_API_KEY = (os.getenv("SEC_API_KEY") or "").strip()
LOOKBACK_HOURS = int(os.getenv("LOOKBACK_HOURS", "168"))
MAX_RESULTS = int(os.getenv("MAX_RESULTS", "2000"))

if not SEC_API_URL:
    raise SystemExit("❌ SEC_API_URL nije postavljen.")
if not (SEC_API_URL.startswith("http://") or SEC_API_URL.startswith("https://")):
    raise SystemExit("❌ SEC_API_URL mora početi s http(s)://")
if not SEC_API_KEY:
    raise SystemExit("❌ SEC_API_KEY nije postavljen.")

def make_headers(mode):
    if mode == "bearer":
        return {"Content-Type":"application/json","Authorization":f"Bearer {SEC_API_KEY}"}
    if mode == "raw":
        return {"Content-Type":"application/json","Authorization":SEC_API_KEY}
    raise ValueError("bad mode")

def try_post(url, payload, mode):
    h = make_headers(mode)
    r = requests.post(url, headers=h, json=payload, timeout=60)
    path = urlparse(url).path or "/"
    print(f"HTTP {r.status_code} @ {urlparse(url).netloc}{path} | auth={mode}")
    if r.status_code == 404:
        return None, 404
    if r.status_code == 401 or r.status_code == 403:
        # auth problem – probat ćemo drugi header mode
        return None, r.status_code
    r.raise_for_status()
    return r.json(), r.status_code

def auto_request(base_url, payload):
    # kandidati URL-a
    if base_url.rstrip("/").endswith("/query"):
        alt_url = base_url.rstrip("/").rsplit("/query",1)[0] or base_url
    else:
        alt_url = base_url.rstrip("/") + "/query"
    url_candidates = [base_url, alt_url] if alt_url != base_url else [base_url]

    # kandidati headera
    auth_modes = ["bearer","raw"]

    last_err = None
    for u in url_candidates:
        for m in auth_modes:
            try:
                data, code = try_post(u, payload, m)
                if data is not None:
                    print(f"✅ USING url={u} auth={m}")
                    return data
                last_err = (u, m, code)
            except requests.HTTPError as e:
                last_err = (u, m, f"HTTPError {e.response.status_code}")
            except Exception as e:
                last_err = (u, m, f"{type(e).__name__}: {e}")

    raise SystemExit(f"❌ Nije uspjelo ni s jednom kombinacijom. Zadnja greška: {last_err}")

def normalize(f):
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

def q_8k(h=LOOKBACK_HOURS):
    return {
      "query":{"query_string":{"query":f'formType:"8-K" AND filedAt:[NOW-{h}HOURS TO NOW] AND items:("2.02" OR "7.01" OR "1.01" OR "5.02" OR "8.01")'}},
      "from":0,"size":MAX_RESULTS,"sort":[{"filedAt":{"order":"desc"}}]
    }

def q_13d13g(h=LOOKBACK_HOURS):
    return {
      "query":{"query_string":{"query":f'formType:("SC 13D" OR "SC 13D/A" OR "SC 13G" OR "SC 13G/A") AND filedAt:[NOW-{h}HOURS TO NOW]'}},
      "from":0,"size":MAX_RESULTS,"sort":[{"filedAt":{"order":"desc"}}]
    }

def q_form4(h=LOOKBACK_HOURS):
    return {
      "query":{"query_string":{"query":f'formType:"4" AND filedAt:[NOW-{h}HOURS TO NOW]'}},
      "from":0,"size":MAX_RESULTS,"sort":[{"filedAt":{"order":"desc"}}]
    }

def q_10q(h=LOOKBACK_HOURS):
    return {
      "query":{"query_string":{"query":f'formType:"10-Q" AND filedAt:[NOW-{h}HOURS TO NOW]'}},
      "from":0,"size":MAX_RESULTS,"sort":[{"filedAt":{"order":"desc"}}]
    }

def fetch_norm(query, name):
    data = auto_request(SEC_API_URL, query)
    # SEC-API shape
    if isinstance(data, dict) and "filings" in data:
        rows = [normalize(x) for x in data["filings"]]
    elif isinstance(data, dict) and "hits" in data and "hits" in data["hits"]:
        rows = [normalize(h.get("_source", h)) for h in data["hits"]["hits"]]
    elif isinstance(data, list):
        rows = [normalize(x) for x in data]
    else:
        rows = []
    save(rows, name)
    return len(rows)

def run():
    total = 0
    total += fetch_norm(q_8k(), "8K_bullish")
    total += fetch_norm(q_13d13g(), "13D_13G")
    total += fetch_norm(q_form4(), "Form4_buys")
    total += fetch_norm(q_10q(), "10Q_bullish")
    print(f"✅ Total normalized rows: {total}")

if __name__ == "__main__":
    run()
