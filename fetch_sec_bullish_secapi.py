#!/usr/bin/env python3
# fetch_sec_bullish_secapi.py
# Povlači bullish filing-e preko sec-api.io:
# - Form 4 s transakcijskim kodom A ili P
# - 8-K s bullish ključnim riječima (buyback/guidance/agreement/merger/acquisition)
# Rezultat sprema u data/history.jsonl, data/bullish_latest.json i public/feed.xml

import os, re, json, datetime, html
import requests

OUT_DIR = "public"
DATA_DIR = "data"
os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

SEC_API_KEY = os.getenv("SEC_API_KEY", "").strip()
SEC_API_URL = os.getenv("SEC_API_URL", "").strip()   # npr. https://api.sec-api.io/query
AUTH_SCHEME = os.getenv("AUTH_SCHEME", "bearer").lower()  # 'bearer' ili 'x-api-key'
USER_AGENT  = os.getenv("SEC_USER_AGENT", "sec-bullish-monitor/1.0 (contact: you@example.com)")

BASE_HEADERS = {"User-Agent": USER_AGENT}

# 8-K bullish signali
KEYWORDS_8K = [
    r"buyback", r"repurchase", r"share repurchase", r"stock repurchase",
    r"raises?\s+guidance", r"guidance\s+(raise|increas)", r"outlook\s+(raise|increas)",
    r"agreement", r"definitive\s+agreement", r"strategic\s+partnership",
    r"merger", r"acquisition", r"acquire",
]

def auth_headers():
    h = dict(BASE_HEADERS)
    if SEC_API_KEY:
        if AUTH_SCHEME == "x-api-key":
            h["x-api-key"] = SEC_API_KEY
        else:
            h["Authorization"] = f"Bearer {SEC_API_KEY}"
    return h

def call_sec_api(query_json):
    if not (SEC_API_KEY and SEC_API_URL):
        raise RuntimeError("SEC_API_KEY/SEC_API_URL nisu postavljeni.")
    r = requests.post(SEC_API_URL, headers=auth_headers(), json=query_json, timeout=45)
    r.raise_for_status()
    return r.json()

def sha1_id(title, link):
    import hashlib
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

def normalize_hit(hit):
    """Normalizira različite oblike koje sec-api može vratiti."""
    form = (hit.get("formType") or hit.get("form") or "").upper()
    filed_at = hit.get("filedAt") or hit.get("filingDate") or hit.get("date") or ""
    company = hit.get("companyName") or hit.get("issuerName") or hit.get("title") or ""
    link = hit.get("filingUrl") or hit.get("link") or hit.get("htmlUrl") or hit.get("url") or ""

    tx_codes = []
    txs = hit.get("transactions") or hit.get("transactionCoding") or []
    if isinstance(txs, dict):
        txs = [txs]
    if isinstance(txs, list):
        for t in txs:
            c = (t.get("transactionCode") or t.get("code") or "").upper()
            if c:
                tx_codes.append(c)

    text_fields = " ".join([str(hit.get(k,"")) for k in ("description","text","exhibitText","documentsText","body")])

    return {
        "form": form,
        "filedAt": filed_at,
        "company": company,
        "link": link,
        "tx_codes": tx_codes,
        "text_blob": text_fields,
    }

def classify(n):
    if n["form"] == "4":
        ap = any(c in ("A","P") for c in n["tx_codes"])
        return (ap, "Form 4 with A/P" if ap else "Form 4 without A/P", [])
    if n["form"].startswith("8-K"):
        low = n["text_blob"].lower()
        for kw in KEYWORDS_8K:
            if re.search(kw, low, flags=re.I):
                return True, f"8-K keywords match ({kw})", []
        return False, "8-K no bullish keywords", []
    return False, "Other form", []

def run():
    # Uzmi zadnjih 100 filing-a (4 i 8-K), sortirano po filedAt desc
    query = {
        "query": "formType:4 OR formType:8-K",
        "from": "0",
        "size": "100",
        "sort": [{ "filedAt": { "order": "desc" } }]
    }
    data = call_sec_api(query)

    hits = data.get("filings") or data.get("data") or data.get("hits") or data.get("items") or []
    items = []
    for raw in hits:
        n = normalize_hit(raw)
        ok, reason, evidence = classify(n)
        if ok:
            title = f"{n['form']} - {n['company']}"
            rec = {
                "title": title,
                "link": n["link"],
                "updated": n["filedAt"],
                "reason": reason,
                "evidence": evidence,
                "id": sha1_id(title, n["link"])
            }
            items.append(rec)

    # dedup & history
    ids = set(); uniq = []
    for it in items:
        if it["id"] in ids: continue
        ids.add(it["id"]); uniq.append(it)

    hist_path = os.path.join(DATA_DIR, "history.jsonl")
    existing = set()
    if os.path.exists(hist_path):
        with open(hist_path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    existing.add(json.loads(line).get("id",""))
                except:
                    pass
    new_items = [x for x in uniq if x["id"] not in existing]
    if new_items:
        with open(hist_path, "a", encoding="utf-8") as f:
            for x in new_items:
                f.write(json.dumps(x, ensure_ascii=False) + "\n")

    with open(os.path.join(DATA_DIR, "bullish_latest.json"), "w", encoding="utf-8") as f:
        json.dump(uniq, f, ensure_ascii=False, indent=2)

    build_rss(uniq, os.path.join(OUT_DIR, "feed.xml"))
    print(f"Done. Bullish items: {len(uniq)}")

if __name__ == "__main__":
    run()
