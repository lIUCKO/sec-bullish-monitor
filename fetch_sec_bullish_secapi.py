#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import csv
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple

import requests


# =========================
#  Konfiguracija iz secreta
# =========================

def getenv_strip(name: str, default: str = "") -> str:
    v = os.getenv(name, default)
    if v is None:
        v = default
    # ukloni whitespace i kontrole (npr. slučajni '\n' iz paste-a)
    return v.strip().strip("\r\n\t ")


SEC_API_URL = getenv_strip("SEC_API_URL", "")
SEC_API_KEY = getenv_strip("SEC_API_KEY", "")
AUTH_SCHEME = getenv_strip("AUTH_SCHEME", "bearer").lower()  # 'bearer' ili 'raw'
LOOKBACK_HOURS = int(os.getenv("LOOKBACK_HOURS", "168"))     # default 7 dana

# normaliziraj bazni endpoint (bez trailing '/')
if SEC_API_URL.endswith("/"):
    SEC_API_URL = SEC_API_URL[:-1]

if not SEC_API_URL:
    print("❌ SEC_API_URL nije postavljen (Actions → Secrets).", file=sys.stderr)
    sys.exit(1)

if not SEC_API_KEY:
    print("❌ SEC_API_KEY nije postavljen (Actions → Secrets).", file=sys.stderr)
    sys.exit(1)

print(f"✅ SEC_API_URL = ***")

# mapiranje output mapa
DATA_DIR = "data"
PUBLIC_DIR = "public"
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(PUBLIC_DIR, exist_ok=True)


# ========================
#  Pomoćne funkcije HTTP-a
# ========================

def _build_headers(mode: str) -> Dict[str, str]:
    """
    mode:
      - 'auth-bearer' : Authorization: Bearer <key>  (ako AUTH_SCHEME != 'raw')
      - 'auth-raw'    : Authorization: <key>         (ako AUTH_SCHEME == 'raw')
      - 'x-api-key'   : x-api-key: <key>
    """
    if mode == "auth-bearer":
        if AUTH_SCHEME == "raw":
            # Ako je izričito raw, nemoj Bearer – koristi raw Authorization
            return {
                "Authorization": SEC_API_KEY,
                "Content-Type": "application/json",
            }
        return {
            "Authorization": f"Bearer {SEC_API_KEY}",
            "Content-Type": "application/json",
        }
    elif mode == "auth-raw":
        return {
            "Authorization": SEC_API_KEY,
            "Content-Type": "application/json",
        }
    elif mode == "x-api-key":
        return {
            "x-api-key": SEC_API_KEY,
            "Content-Type": "application/json",
        }
    else:
        # default na Authorization raw
        return {
            "Authorization": SEC_API_KEY,
            "Content-Type": "application/json",
        }


def _attempts(base: str) -> List[Tuple[str, str]]:
    """
    Posloženi pokušaji: prvo base s Authorization (najčešće radi),
    zatim /query varijante i x-api-key fallback.
    """
    return [
        (f"{base}/query", "auth-bearer"),
        (f"{base}/query", "x-api-key"),
        (base,           "auth-bearer"),
        (base,           "auth-raw"),
        (f"{base}/query", "auth-raw"),
        (base,           "x-api-key"),
    ]


def post_query(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Šalje payload na endpoint. Prolazi kroz nekoliko kombinacija URL/headera
    dok ne dobije 200 i JSON.
    """
    # payload s obje ključne riječi ('q' i 'query') – veća kompatibilnost
    if "q" not in payload and "query" in payload:
        payload["q"] = payload["query"]
    if "query" not in payload and "q" in payload:
        payload["query"] = payload["q"]

    for url, mode in _attempts(SEC_API_URL):
        headers = _build_headers(mode)
        target = url.replace(SEC_API_KEY, "***")
        try:
            print(f"POST {url if '***' in target else target}  ({'Authorization' if 'Authorization' in headers else 'x-api-key'} header)")
            r = requests.post(url, headers=headers, json=payload, timeout=60)
            if r.status_code == 200:
                try:
                    return r.json()
                except Exception:
                    # Ako nije JSON, probaj tekst pa wrap
                    return {"raw": r.text}
            elif r.status_code in (400, 403, 404):
                # ove greške koristimo kao signal za iduću kombinaciju
                print(f"→ HTTP {r.status_code} @ {target}; pokušavam iduću kombinaciju…")
                continue
            else:
                # neočekivani status – digni grešku
                r.raise_for_status()
        except requests.RequestException as e:
            # mrežni problemi – probaj iduće
            print(f"⚠️  {e}; pokušavam iduću kombinaciju…")
            continue

    raise RuntimeError("Nije uspjelo poslati upit na niti jednu kombinaciju endpointa/headera.")


# ======================
#  Query (upiti / filteri)
# ======================

def q_8k_bullish(h: int) -> str:
    pos = [
        "raises guidance", "guidance raised", "reaffirms guidance",
        "share repurchase", "buyback", "dividend increase",
        "contract award", "wins contract", "strategic partnership",
        "FDA approval", "breakthrough therapy", "uplisting", "reinstates dividend"
    ]
    neg = ["ATM", "at-the-market", "S-1", "warrant", "shelf registration"]

    pos_q = " OR ".join([f'text:"{t}"' for t in pos])
    neg_q = " OR ".join([f'text:"{t}"' for t in neg])

    return (
        f'formType:"8-K" AND filedAt:[now-{h}h TO now] AND ( {pos_q} ) '
        f'AND NOT ( {neg_q} )'
    )


def q_8k_material_agreements(h: int) -> str:
    return (
        f'formType:"8-K" AND filedAt:[now-{h}h TO now] AND '
        f'( text:"Item 1.01" OR text:"material definitive agreement" ) '
        f'AND NOT ( text:"ATM" OR text:"at-the-market" )'
    )


def q_form4_buys(h: int) -> str:
    # pokrij razne lokacije transactionCode “P”
    return (
        f'formType:"4" AND filedAt:[now-{h}h TO now] AND ('
        f'  transactionCode:"P" OR '
        f'  data.insiderTransactions.transactionCode:"P" OR '
        f'  nonDerivativeTable.transactionCode:"P" OR '
        f'  derivativeTable.transactionCode:"P" '
        f')'
    )


def q_10q_bullish(h: int) -> str:
    terms = [
        "raises guidance", "increase guidance", "improved liquidity",
        "cash flow improved", "gross margin improved", "profitability improved",
        "record revenue", "strong backlog"
    ]
    t_q = " OR ".join([f'text:"{t}"' for t in terms])
    return f'formType:"10-Q" AND filedAt:[now-{h}h TO now] AND ( {t_q} )'


def q_13d_13g(h: int) -> str:
    return f'(formType:"SC 13D" OR formType:"SC 13G") AND filedAt:[now-{h}h TO now]'


# =========================
#  Spremanje JSON i CSV dat.
# =========================

def _safe_get(d: Dict[str, Any], k: str, default: Any = "") -> Any:
    v = d.get(k, default)
    if v is None:
        return default
    return v


def _extract_rows(resp: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Vrati listu filing objekata iz responsa. Pokrivamo nekoliko uobičajenih oblika.
    """
    if not resp:
        return []
    if isinstance(resp, list):
        return resp
    if "filings" in resp and isinstance(resp["filings"], list):
        return resp["filings"]
    if "data" in resp and isinstance(resp["data"], list):
        return resp["data"]
    if "results" in resp and isinstance(resp["results"], list):
        return resp["results"]
    # fallback – ako je jedan objekt
    return [resp] if isinstance(resp, dict) else []


def save_json_csv(name: str, rows: List[Dict[str, Any]]) -> None:
    # JSON
    jpath = os.path.join(DATA_DIR, f"{name}.json")
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)

    # CSV – uzmi nekoliko standardnih polja; ostalo flatten u json string ako treba
    cpath = os.path.join(DATA_DIR, f"{name}.csv")
    fields = ["ticker", "companyName", "formType", "filedAt", "filingDate", "link"]
    with open(cpath, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(fields + ["extra"])
        for r in rows:
            extra = {}
            # pokupi nešto korisno ako polja fale
            for k in ("cik", "accessionNo", "acceptedAt", "periodOfReport"):
                if k in r:
                    extra[k] = r[k]
            w.writerow([
                _safe_get(r, "ticker"),
                _safe_get(r, "companyName"),
                _safe_get(r, "formType"),
                _safe_get(r, "filedAt") or _safe_get(r, "filingDate"),
                _safe_get(r, "filingDate") or _safe_get(r, "filedAt"),
                _safe_get(r, "link") or _safe_get(r, "url"),
                json.dumps(extra, ensure_ascii=False),
            ])


# ===============
#  RSS generacija
# ===============

def build_rss(items: List[Dict[str, str]], out_path: str) -> None:
    now = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z")
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0"><channel>',
        f"<title>SEC Bullish Monitor</title>",
        f"<description>Automatski SEC bullish feed</description>",
        f"<link>https://{os.getenv('GITHUB_REPOSITORY','').lower()}</link>",
        f"<lastBuildDate>{now}</lastBuildDate>",
    ]
    for it in items:
        title = it.get("title", "SEC item")
        link = it.get("link", "")
        pub = it.get("pubDate", now)
        desc = it.get("description", "")
        lines += [
            "<item>",
            f"<title><![CDATA[{title}]]></title>",
            f"<link>{link}</link>",
            f"<pubDate>{pub}</pubDate>",
            f"<description><![CDATA[{desc}]]></description>",
            "</item>",
        ]
    lines.append("</channel></rss>")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# =========================
#  Orkestracija i pokretanje
# =========================

def run_query_to_files(name: str, query: str, size: int = 120) -> int:
    payload = {
        "q": query,
        "query": query,      # radi kompatibilnosti
        "from": 0,
        "size": size,
        "sort": [{"filedAt": "desc"}],  # ako servis prihvaća sort
    }
    resp = post_query(payload)
    rows = _extract_rows(resp)
    save_json_csv(name, rows)
    print(f"✅ Saved {len(rows)} → {name}.json / .csv")
    return len(rows)


def main() -> None:
    start = datetime.now(timezone.utc)
    print(f"Starting @ {start.strftime('%Y-%m-%dT%H:%M:%SZ')}  | lookback {LOOKBACK_HOURS}h")

    total_items = 0
    rss_items: List[Dict[str, str]] = []

    tasks = [
        ("8K_bullish",              q_8k_bullish(LOOKBACK_HOURS)),
        ("8K_material_agreements",  q_8k_material_agreements(LOOKBACK_HOURS)),
        ("Form4_buys",              q_form4_buys(LOOKBACK_HOURS)),
        ("10Q_bullish",             q_10q_bullish(LOOKBACK_HOURS)),
        ("13D_13G",                 q_13d_13g(LOOKBACK_HOURS)),
    ]

    for name, q in tasks:
        try:
            cnt = run_query_to_files(name, q, size=120)
            total_items += cnt
            # ubaci u RSS  (samo najbitnije polje: link + naslov)
            jpath = os.path.join(DATA_DIR, f"{name}.json")
            with open(jpath, "r", encoding="utf-8") as f:
                rows = json.load(f)
            for r in rows[:30]:  # ograniči koliko ide u feed po kategoriji
                rss_items.append({
                    "title": f"{_safe_get(r,'ticker')} {_safe_get(r,'formType')} – {name}",
                    "link": _safe_get(r, "link") or _safe_get(r, "url") or "",
                    "pubDate": _safe_get(r, "filedAt") or _safe_get(r, "filingDate") or start.strftime("%a, %d %b %Y %H:%M:%S %z"),
                    "description": _safe_get(r, "companyName") or "",
                })
        except Exception as e:
            print(f"❌ {name} failed: {e}", file=sys.stderr)

    # RSS out
    rss_path = os.path.join(PUBLIC_DIR, "sec-bullish.xml")
    build_rss(rss_items, rss_path)
    print(f"📡 RSS feed saved → {os.path.basename(rss_path)}")

    end = datetime.now(timezone.utc)
    print(f"✅ Done @ {end.strftime('%Y-%m-%dT%H:%M:%SZ')} | total items: {total_items}")


if __name__ == "__main__":
    main()
