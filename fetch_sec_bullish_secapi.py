#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
fetch_sec_bullish_secapi.py
Autor: ChatGPT (rev. 2025-11-03)

Kratko:
- Gađa TOČNO jedan endpoint (SEC_API_URL) i TOČNO jedan auth način (AUTH_SCHEME).
- Fail-fast: na 404/401/403 jasno prekida s objašnjenjem.
- Ne zapisuje JSON/CSV ni RSS ako nema rezultata.
- Jednostavna paginacija (podržava nextPageToken i/ili from/size heuristiku).
- Queryji: 8-K bullish (7.01/2.02 + pozitivne fraze, bez ATM), 8-K Item 1.01,
  Form 4 "P" (kupnje), 10-Q bullish fraze, 13D/13G.

ENV varijable (Secrets):
- SEC_API_URL   : točan POST endpoint iz tvog SEC-API (ili drugog) dashboarda (bez izmišljanja /query).
- SEC_API_KEY   : API ključ (string).
- AUTH_SCHEME   : 'bearer' ili 'x-api-key' ili 'raw' (točno kako zahtijeva tvoj plan).
- LOOKBACK_HOURS: koliko sati unatrag gledamo (default 72).
- MAX_PER_QUERY : max zapisa po tasku (default 300).
- PAGE_LIMIT    : max broj paginiranih “stranica” (default 5).

Output:
- data/*.json, data/*.csv (samo ako ima redaka)
- public/sec-bullish.xml (samo ako ima stavki)
"""

import os
import sys
import csv
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional, Tuple

import requests


# =========================
#  Konfiguracija iz secreta
# =========================

def getenv_strip(name: str, default: str = "") -> str:
    v = os.getenv(name, default)
    if v is None:
        v = default
    return v.strip().strip("\r\n\t ")


SEC_API_URL = getenv_strip("SEC_API_URL", "")
SEC_API_KEY = getenv_strip("SEC_API_KEY", "")
AUTH_SCHEME = getenv_strip("AUTH_SCHEME", "bearer").lower()   # 'bearer' | 'x-api-key' | 'raw'

LOOKBACK_HOURS = int(os.getenv("LOOKBACK_HOURS", "72"))       # default 72h
MAX_PER_QUERY  = int(os.getenv("MAX_PER_QUERY", "300"))       # zaštita od prevelikih dumpova
PAGE_LIMIT     = int(os.getenv("PAGE_LIMIT", "5"))            # max broj "stranica"

if not SEC_API_URL:
    print("❌ SEC_API_URL nije postavljen. Uzmi TOČAN POST endpoint iz tvog dashboarda.", file=sys.stderr)
    sys.exit(1)

if not SEC_API_KEY:
    print("❌ SEC_API_KEY nije postavljen.", file=sys.stderr)
    sys.exit(1)

if AUTH_SCHEME not in {"bearer", "x-api-key", "raw"}:
    print("❌ AUTH_SCHEME mora biti 'bearer', 'x-api-key' ili 'raw'.", file=sys.stderr)
    sys.exit(1)

# normaliziraj bazni endpoint (bez trailing '/')
SEC_API_URL = SEC_API_URL[:-1] if SEC_API_URL.endswith("/") else SEC_API_URL

print("✅ SEC_API_URL = ***")

# mapiranje output mapa
DATA_DIR = "data"
PUBLIC_DIR = "public"
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(PUBLIC_DIR, exist_ok=True)


# ========================
#  Pomoćne funkcije HTTP-a
# ========================

def _headers() -> Dict[str, str]:
    if AUTH_SCHEME == "bearer":
        return {"Authorization": f"Bearer {SEC_API_KEY}", "Content-Type": "application/json"}
    if AUTH_SCHEME == "x-api-key":
        return {"x-api-key": SEC_API_KEY, "Content-Type": "application/json"}
    # raw
    return {"Authorization": SEC_API_KEY, "Content-Type": "application/json"}


def _post_once(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Pošalji jedan POST na SEC_API_URL s ispravnim headerima. Fail-fast na klasičnim greškama."""
    print(f"POST {SEC_API_URL}  ({'Authorization' if AUTH_SCHEME in ('bearer','raw') else 'x-api-key'})")
    try:
        r = requests.post(SEC_API_URL, headers=_headers(), json=payload, timeout=60)
    except requests.RequestException as e:
        raise RuntimeError(f"❌ Mrežna greška: {e}")

    if r.status_code == 404:
        raise RuntimeError("❌ 404 Not Found: Krivi endpoint path. Uzmi točan URL iz dashboarda (ne dodavati '/query' ako nije eksplicitno navedeno).")
    if r.status_code in (401, 403):
        raise RuntimeError("❌ Auth greška (401/403): Krivi AUTH_SCHEME ili ključ. Koristi točno 'bearer' ili 'x-api-key' prema tvom planu.")

    r.raise_for_status()
    try:
        return r.json()
    except Exception:
        # fallback: plain text
        return {"raw": r.text}


# ======================
#  Query (upiti / filteri)
# ======================

def _gte_timestamp(hours_back: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_back)).strftime("%Y-%m-%dT%H:%M:%SZ")


def q_8k_bullish(hours: int) -> str:
    pos = [
        "raises guidance", "guidance raised", "reaffirms guidance",
        "share repurchase", "buyback", "dividend increase",
        "contract award", "wins contract", "strategic partnership",
        "FDA approval", "breakthrough therapy", "uplisting", "reinstates dividend"
    ]
    neg = ["ATM", "at-the-market", "shelf registration", "warrant"]
    pos_q = " OR ".join([f'text:"{t}"' for t in pos])
    neg_q = " OR ".join([f'text:"{t}"' for t in neg])
    return (
        f'formType:"8-K" AND filedAt:[now-{hours}h TO now] AND ( {pos_q} ) '
        f'AND NOT ( {neg_q} ) AND ( itemNumbers:"7.01" OR itemNumbers:"2.02" )'
    )


def q_8k_material_agreements(hours: int) -> str:
    return (
        f'formType:"8-K" AND filedAt:[now-{hours}h TO now] AND '
        f'( text:"Item 1.01" OR text:"material definitive agreement" ) '
        f'AND NOT ( text:"ATM" OR text:"at-the-market" )'
    )


def q_form4_buys(hours: int) -> str:
    return (
        f'formType:"4" AND filedAt:[now-{hours}h TO now] AND ('
        f'  transactionCode:"P" OR '
        f'  data.insiderTransactions.transactionCode:"P" OR '
        f'  nonDerivativeTable.transactionCode:"P" OR '
        f'  derivativeTable.transactionCode:"P" '
        f')'
    )


def q_10q_bullish(hours: int) -> str:
    terms = [
        "raises guidance", "increase guidance", "improved liquidity",
        "cash flow improved", "gross margin improved", "profitability improved",
        "record revenue", "strong backlog"
    ]
    t_q = " OR ".join([f'text:"{t}"' for t in terms])
    return f'formType:"10-Q" AND filedAt:[now-{hours}h TO now] AND ( {t_q} )'


def q_13d_13g(hours: int) -> str:
    return f'(formType:"SC 13D" OR formType:"SC 13G") AND filedAt:[now-{hours}h TO now]'


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
    for key in ("filings", "data", "results", "items"):
        if key in resp and isinstance(resp[key], list):
            return resp[key]
    # fallback – jedan objekt
    return [resp] if isinstance(resp, dict) else []


def save_json_csv(name: str, rows: List[Dict[str, Any]]) -> None:
    # JSON
    jpath = os.path.join(DATA_DIR, f"{name}.json")
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)

    # CSV
    cpath = os.path.join(DATA_DIR, f"{name}.csv")
    fields = ["ticker", "companyName", "formType", "filedAt", "filingDate", "link"]
    with open(cpath, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(fields + ["extra"])
        for r in rows:
            extra = {}
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
        "<title>SEC Bullish Monitor</title>",
        "<description>Automatski SEC bullish feed</description>",
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
#  Paginacija i orkestracija
# =========================

def _payload_base(query: str, size: int, frm: int = 0, next_token: Optional[str] = None) -> Dict[str, Any]:
    """
    Baza payloada: string search + from/size + sort po filedAt desc.
    Ako backend koristi nextPageToken, dodajemo ga u payload ako je zadan.
    """
    p = {
        "q": query,
        "query": query,          # kompatibilnost
        "from": frm,
        "size": size,
        "sort": [{"filedAt": "desc"}],
    }
    if next_token:
        p["nextPageToken"] = next_token
    return p


def _page_extract_next(resp: Dict[str, Any], frm: int, size: int) -> Tuple[Optional[str], int, bool]:
    """
    Pokušaj izvući pokazivač na sljedeću stranicu.
    Vraća: (nextPageToken | None, next_from, has_more_by_from)
    - Ako postoji 'nextPageToken' u responsu, koristi njega.
    - Inače koristi from/size heuristiku: ako 'total' postoji i frm+size < total → ima još.
    """
    token = resp.get("nextPageToken") or resp.get("next") or None
    has_more_by_from = False
    total = resp.get("total") or resp.get("hits", {}).get("total") if isinstance(resp.get("hits"), dict) else None
    if token:
        return token, frm, False
    if isinstance(total, int) and frm + size < total:
        has_more_by_from = True
    return None, frm + size, has_more_by_from


def run_query_to_files(name: str, query: str, max_items: int = MAX_PER_QUERY) -> int:
    """
    Izvrši query s jednostavnom paginacijom do max_items ili PAGE_LIMIT.
    Zapiši JSON/CSV samo ako ima redaka. Vrati broj zapisa.
    """
    collected: List[Dict[str, Any]] = []
    page = 0
    frm = 0
    next_token: Optional[str] = None
    size = min(100, max(20, max_items // max(PAGE_LIMIT, 1)))  # razuman size

    while page < PAGE_LIMIT and len(collected) < max_items:
        payload = _payload_base(query, size, frm, next_token)
        resp = _post_once(payload)
        rows = _extract_rows(resp)
        if not rows:
            print(f"⚠️ Stranica {page+1}: 0 rezultata.")
            break
        # deduplikacija po accessionNo+filedAt+link ako postoje
        before = len(collected)
        seen = {(r.get("accessionNo"), r.get("filedAt"), r.get("link")) for r in collected}
        for r in rows:
            key = (r.get("accessionNo"), r.get("filedAt"), r.get("link"))
            if key not in seen:
                collected.append(r)
        added = len(collected) - before
        print(f"➕ Dodano {added} (ukupno {len(collected)}) na '{name}'")

        next_token, next_from, has_more_by_from = _page_extract_next(resp, frm, size)
        page += 1
        if next_token:
            # ostavi frm isti, koristi token
            continue
        if has_more_by_from:
            frm = next_from
            continue
        # nema više
        break

    if not collected:
        print(f"📭 {name}: nema rezultata → preskačem zapis.")
        return 0

    save_json_csv(name, collected)
    print(f"✅ {name}: zapisano {len(collected)} redaka → data/{name}.json, data/{name}.csv")
    return len(collected)


# =========================
#  Glavni tok
# =========================

def main() -> None:
    start = datetime.now(timezone.utc)
    print(f"Starting @ {start.strftime('%Y-%m-%dT%H:%M:%SZ')}  | lookback {LOOKBACK_HOURS}h")

    tasks: List[Tuple[str, str]] = [
        ("8K_bullish",              q_8k_bullish(LOOKBACK_HOURS)),
        ("8K_material_agreements",  q_8k_material_agreements(LOOKBACK_HOURS)),
        ("Form4_buys",              q_form4_buys(LOOKBACK_HOURS)),
        ("10Q_bullish",             q_10q_bullish(LOOKBACK_HOURS)),
        ("13D_13G",                 q_13d_13g(LOOKBACK_HOURS)),
    ]

    total_items = 0
    rss_items: List[Dict[str, str]] = []

    for name, query in tasks:
        try:
            cnt = run_query_to_files(name, query, max_items=MAX_PER_QUERY)
            total_items += cnt
            if cnt > 0:
                jpath = os.path.join(DATA_DIR, f"{name}.json")
                with open(jpath, "r", encoding="utf-8") as f:
                    rows = json.load(f)
                # RSS: ograniči broj stavki po kategoriji (npr. 30)
                for r in rows[:30]:
                    rss_items.append({
                        "title": f"{_safe_get(r,'ticker')} {_safe_get(r,'formType')} – {name}",
                        "link": _safe_get(r, "link") or _safe_get(r, "url") or "",
                        "pubDate": _safe_get(r, "filedAt") or _safe_get(r, "filingDate") or start.strftime("%a, %d %b %Y %H:%M:%S %z"),
                        "description": _safe_get(r, "companyName") or "",
                    })
        except Exception as e:
            print(f"❌ {name} failed: {e}", file=sys.stderr)

    # RSS out – samo ako ima ičega
    rss_path = os.path.join(PUBLIC_DIR, "sec-bullish.xml")
    if rss_items:
        build_rss(rss_items, rss_path)
        print(f"📡 RSS feed saved → {os.path.basename(rss_path)}")
    else:
        print("📭 RSS preskočen (nema stavki).")

    end = datetime.now(timezone.utc)
    print(f"✅ Done @ {end.strftime('%Y-%m-%dT%H:%M:%SZ')} | total items: {total_items}")


if __name__ == "__main__":
    main()
