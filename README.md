# SEC Bullish Monitor — sec-api.io

Automatski prati **Form 4 (A/P)** i **8-K** (buyback/guidance/agreement/merger) preko **sec-api.io**, generira **RSS** u `public/feed.xml` i čuva povijest u `data/`.

## Postavke (obavezno)
Repo → Settings → Secrets and variables → Actions:
- `SEC_API_KEY` → tvoj sec-api.io ključ (NE stavljaj u kod!)
- `SEC_API_URL` → npr. `https://api.sec-api.io/query`
- (opcija) `AUTH_SCHEME` → `x-api-key` ako tvoj račun traži taj header (default je `bearer`).

## Pokretanje
1. Dodaj ove datoteke u repo (ovaj README, skriptu, workflow i prazne mape `public/` i `data/`).
2. Postavi **Secrets** (gore).
3. Otvori **Actions** → pokreni **Run workflow**.
4. RSS: `public/feed.xml` (RAW URL) zalijepi u Inoreader → Add subscription.
