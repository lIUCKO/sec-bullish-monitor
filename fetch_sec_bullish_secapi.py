#!/usr/bin/env python3
"""
SEC Bullish Monitor — sec-api.io
- Povlači Form 4 (A/P) i 8-K s bullish signalima (buyback/guidance/agreement/merger/acquisition)
- Sprema: data/history.jsonl, data/bullish_latest.json, public/feed.xml

ENV:
  SEC_API_KEY      (obavezno)
  SEC_API_URL      (opcionalno) npr. https://api.sec-api.io  ili  https://api.sec-api.io/query
  AUTH_SCHEME      (opcionalno) 'x-api-key' (default) ili 'Bearer'
  SEC_USER_AGENT   (opcionalno)
"""

import os, re, json, datetime, html, hashlib, sys, textwrap
import requests

# ---------- postavke i mape ----------
OUT_DIR = "public"
DATA_DIR = "data"
os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

SEC_API_KEY = (os.getenv("SEC_API_KEY") or "").strip()
if not SEC_API_KEY:
    print("ERROR: SEC_API_KEY nije postavljen.", file=sys.stderr)
    sys.exit(2)

# default na root endpoint (jer neki računi NEMAJU /query)
SEC_API_URL = (os.getenv("SEC_API_URL") or "https://api.sec-api.io").strip().rstrip("/")
AUTH_SCHEME  = (os.getenv("AUTH_SCHEME") or "x-api-key").strip()  # default: x-api-key
USER_AGENT   = os.getenv("SEC_USER_AGENT") or "sec-bullish-monitor/1.0 (+contact: you@example.com)"

BASE_HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}

KEYWORDS_8K = [
    r"buyback", r"repurchase", r"share repurchase", r"stock repurchase",
    r"raises?\s+guidance", r"guidance\s+(raise|increas)", r"outlook\s+(raise|increas)",
    r"agreement", r"definitive\s+agreement", r"strategic\s+partnership",
    r"merger", r"acquisition", r"acquire",
]

# ---------- util ----------

def auth_headers():
    h = dict(BASE_HEADERS)
    if AUTH_SCHEME.lower() == "x-api-key":
        h["x-api-key"] = SEC_API_KEY
    else:
        h["Authorization"] = f"{AUTH_SCHEME} {SEC_API_KEY}"
    return h

def try_post(url: str, payload: dict):
    """Pokušaj POST i vrati (ok, response or text snippet, status_code)."""
    try:
        r = requests.post(url, headers=auth_headers(), json=payload, timeout=60)
    except requests.RequestException as e:
        return False, f"Request error: {e}", None
    if 200 <= r.status_code < 300:
        try:
            return True, r.json(), r.status_code
        except Exception:
            return False, f"Invalid JSON: {r.text[:500]}", r.status_code
    else:
        return False, f"HTTP {r.status_code}: {r.text[:500]}", r.status_code

def call_sec_api(payload: dict):
    """
    Robustan poziv s fallback-om:
      1) SEC_API_URL (kakav je postavljen)
      2) SEC_API_URL + '/query'   (ako prvo ne radi)
    """
    candidates = [SEC_API_URL]
    if not SEC_API_URL.endswith("/query"):
        candidates.append(SEC_API_URL + "/query")

    last_err = ""
    for u in candidates:
        print(f"[sec-api] Try URL: {u}")
        ok, resp, code = try_post(u, payload)
        if ok:
            return resp
        # ako je baš 404/405/400 i “Cannot POST /query”, probaj idući kandidat
        last_err = f"{resp}"
        print(f"[sec-api] Fail at {u} -> {last_err}")

    raise RuntimeError(f"SEC API call failed. Last error: {last_err}")

def sha1_id(title, link):
    return hashlib.sha1((title + "|" + link).encode()).hexdigest()

def build_rss(items, out_path):
    now = datetime.datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S +0000")
    xml = ['<?xml version="1.0" encoding="UTF-8"?>', '<rss version="2.0"><channel>']
    xml += [
        "<title>SEC Bullish Monitor (sec-api)</title>",
        "<description>Form 4 A/P + 8-K bullish keywords</description>",
        f"<lastBuildDate>{now}</lastBuildDate>",
        "<link>https://www.sec.gov/</link>",
    ]
    for it in items[:120]:
        title = html.escape(it["title"])
        link  = html.escape(it["link"])
        pub   = html.escape(it.get("updated",""))
        desc  = html.escape(json.dumps({"reason": it["reason"], "evidence": it.get("evidence",[])}, ensure_ascii=False))
        xml.append(f"<item><title>{title}</title><link>{link}</link><pubDate>{pub}</pubDate><description>{desc}</description></item>")
    xml.append("</channel></rss>")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(xml))

def normalize_hit(hit: dict):
    # toleriraj različite formate odgovora
    h = hit or {}
    if "filing" in h and isinstance(h["filing"], dict):
        h = h["filing"]

    form = (h.get("formType") or h.get("form") or "").upper()
    filed_at = h.get("filedAt") or h.get("filingDate") or h.get("date") or ""
    company = h.get("companyName") or h.get("issuerName") or h.get("title") or ""
    link = h.get("filingUrl") or h.get("link") or h.get("htmlUrl") or h.get("url") or ""
    tx_codes = []
    txs = h.get("transactions") or h.get("transactionCoding") or []
    if isinstance(txs, dict): txs = [txs]
    if isinstance(txs, list):
        for t in txs:
            c = (t.get("transactionCode") or t.get("code") or "").upper()
            if c: tx_codes.append(c)
    text_fields = " ".join(str(h.get(k,"")) for k in ("description","text","exhibitText","documentsText","body"))
    return {"form": form, "filedAt": filed_at, "company": company, "link": link, "tx_codes": tx_codes, "text_blob": text_fields}

def is_bullish(n):
    if n["form"] == "4":
        ap = any(c in ("A","P") for c in n["tx_codes"])
        return ap, "Form 4 with A/P" if ap else "Form 4 without A/P", []
    if n["form"].startswith("8-K"):
        low = n["text_blob"].lower()
        for kw in KEYWORDS_8K:
            if re.search(kw, low, flags=re.I):
                return True, f"8-K keywords match ({kw})", []
        return False, "8-K no bullish keywords", []
    return False, "Other form", []

# ---------- glavni tok ----------

def run():
    # Minimalni query kompatibilan s većinom planova sec-api.io
    query = {
        "query": "formType:4 OR formType:8-K",
        "from": "0",
        "size": "100",
        "sort": [{ "filedAt": { "order": "desc" } }]
    }
    print("[sec-api] Query payload:", json.dumps(query)[:400])

    data = call_sec_api(query)

    # Normaliziraj listu pogodaka iz raznih formata
    hits = (
        data.get("filings")
        or data.get("data")
        or data.get("hits")
        or data.get("items")
        or []
    )
    if isinstance(hits, dict) and "hits" in hits:
        hits = hits["hits"]

    items = []
    for raw in hits:
        n = normalize_hit(raw)
        ok, reason, evidence = is_bullish(n)
        if ok:
            title = f"{n['form']} - {n['company']}".strip(" -")
            rec = {
                "title": title,
                "link": n["link"],
                "updated": n["filedAt"],
                "reason": reason,
                "evidence": evidence,
                "id": sha1_id(title, n["link"] or title),
            }
            items.append(rec)

    # deduplikacija
    seen, uniq = set(), []
    for it in items:
        if it["id"] in seen: 
            continue
        seen.add(it["id"]); uniq.append(it)

    # povijest
    hist = os.path.join(DATA_DIR, "history.jsonl")
    existing = set()
    if os.path.exists(hist):
        with open(hist, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    existing.add(json.loads(line).get("id",""))
                except:
                    pass

    new_items = [x for x in uniq if x["id"] not in existing]
    if new_items:
        with open(hist, "a", encoding="utf-8") as f:
            for x in new_items:
                f.write(json.dumps(x, ensure_ascii=False) + "\n")

    with open(os.path.join(DATA_DIR, "bullish_latest.json"), "w", encoding="utf-8") as f:
        json.dump(uniq, f, ensure_ascii=False, indent=2)

    build_rss(uniq, os.path.join(OUT_DIR, "feed.xml"))
    print(f"Done. Bullish items: {len(uniq)}")

if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        # korisna poruka u logu bez curenja ključa
        print("ERROR:", e)
        sys.exit(1)
