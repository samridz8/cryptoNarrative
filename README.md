# Narrative Radar

Local crypto narrative and momentum detection engine.  
Identifies emerging small-cap narrative rotations early — **for personal use only**.

> Not a trading bot. Not an AI chatbot. Not a SaaS product.

---

## What it does

| Step | Description |
|------|-------------|
| **Collect** | Fetches trending coins from CoinGecko and DexScreener every 15 min |
| **Categorise** | Assigns each token to a narrative via keyword matching (AI Agents, DePIN, RWA, …) |
| **Score** | Computes a 0-100 momentum score per token using volume acceleration, price velocity, trend persistence and liquidity health |
| **Detect** | Groups tokens by narrative; fires an alert when multiple related tokens move together |
| **Display** | Shows everything on a minimal local web dashboard at `http://localhost:8000` |

---

## Quick Start

### 1. Install Python 3.11+

Verify with `python --version`.

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. (Optional) Configure

```bash
cp .env.example .env
# Edit .env — defaults work fine out of the box
```

### 4. Run

```bash
python main.py
```

Open **http://localhost:8000** in a browser.  
The first collection cycle runs immediately on startup; subsequent cycles run every 15 minutes.

---

## Project Structure

```
crypto-narrative/
├── api/              FastAPI route handlers (/api/*)
├── collectors/       CoinGecko + DexScreener fetchers
├── dashboard/        FastAPI app, lifespan, HTML template
│   └── templates/
├── models/           Pydantic schemas
├── scoring/          Momentum scoring + narrative categorisation
├── storage/          SQLite CRUD (data/narrative_radar.db)
├── utils/            Logger + terminal alert printer
├── config.py         Reads .env; central constants
├── scheduler.py      Collection & analysis pipeline
├── main.py           Entry point (uvicorn)
├── requirements.txt
└── .env.example
```

---

## Momentum Score (0 – 100)

| Component | Max pts | Formula |
|-----------|---------|---------|
| Volume acceleration | 25 | log₂(current\_vol / avg\_hist\_vol + 1) × 7 |
| Price velocity | 25 | weighted 24h + 1h price change |
| Trend persistence | 25 | consecutive positive 24h periods × 5 |
| Liquidity health | 25 | liquidity / volume ratio × 10 |
| Ecosystem clustering | +10 | bonus when 3+ tokens in same narrative trend together |

---

## Narratives Tracked

`AI Agents` · `DePIN` · `RWA` · `GameFi` · `DeFi` · `Meme` · `Layer2` · `SocialFi` · `Privacy` · `Solana` · `Base`

Add or edit keywords in `scoring/narratives.py → NARRATIVE_KEYWORDS`.

---

## Alert Example (terminal)

```
══════════════════════════════════════════════════════
[HIGH SIGNAL]
Narrative : AI Agents
Confidence: 78
Tokens    :
  • VIRTUAL
  • AIOZ
  • FET
Reason    :
  3 related tokens trending simultaneously + high avg momentum (62/100)
══════════════════════════════════════════════════════
```

---

## Data Sources

| Source | Endpoint | Free? |
|--------|----------|-------|
| CoinGecko | `/search/trending`, `/coins/markets` | ✅ No key |
| DexScreener | `/token-boosts/top/v1`, `/token-profiles/latest/v1` | ✅ No key |

---

## Configuration (`.env`)

| Variable | Default | Description |
|----------|---------|-------------|
| `COLLECTION_INTERVAL_MINUTES` | `15` | How often to collect data |
| `ALERT_CONFIDENCE_THRESHOLD` | `60` | Minimum confidence to fire an alert |
| `TOP_MARKETS_COUNT` | `200` | Coins fetched from CoinGecko markets |
| `PORT` | `8000` | Dashboard port |

---

## Notes

- SQLite database: `data/narrative_radar.db` (auto-created)
- No authentication, no cloud, no microservices
- CoinGecko free tier: ~30 req/min — this tool makes ≤ 2 calls per cycle, well within limits
- DexScreener: no published rate limit; batching is used to stay conservative
