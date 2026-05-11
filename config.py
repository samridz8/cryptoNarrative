import os
from dotenv import load_dotenv

load_dotenv()

# ── Scheduler ───────────────────────────────────────────────────────────────
COLLECTION_INTERVAL_MINUTES: int = int(os.getenv("COLLECTION_INTERVAL_MINUTES", "15"))
TOP_MARKETS_COUNT: int = int(os.getenv("TOP_MARKETS_COUNT", "200"))
PORT: int = int(os.getenv("PORT", "8000"))

# ── Quality filters ──────────────────────────────────────────────────────────
MIN_LIQUIDITY_USD: float = float(os.getenv("MIN_LIQUIDITY_USD", "100000"))
MIN_MARKET_CAP: float = float(os.getenv("MIN_MARKET_CAP", "1000000"))
MIN_VOLUME_24H: float = float(os.getenv("MIN_VOLUME_24H", "50000"))
MAX_PRICE_CHANGE_ABS: float = float(os.getenv("MAX_PRICE_CHANGE_ABS", "500"))

# ── Alert thresholds ─────────────────────────────────────────────────────────
ALERT_CONFIDENCE_THRESHOLD: float = float(os.getenv("ALERT_CONFIDENCE_THRESHOLD", "60"))
ALERT_MIN_TOKENS: int = 2
ALERT_MIN_MOMENTUM: float = 30.0

# ── Narrative weights (multiplier applied to raw confidence score) ────────────
# >1.0 = prioritised  |  <1.0 = downweighted
NARRATIVE_WEIGHTS: dict[str, float] = {
    "AI Agents": 1.30,
    "DePIN":     1.25,
    "RWA":       1.15,
    "Layer2":    1.10,
    "DeFi":      1.05,
    "GameFi":    1.00,
    "SocialFi":  1.00,
    "Privacy":   1.00,
    "Solana":    1.10,
    "Base":      1.10,
    "Meme":      0.50,
    "Other":     0.80,
}
DEFAULT_NARRATIVE_WEIGHT: float = 1.00

# ── Persistence ──────────────────────────────────────────────────────────────
PERSISTENCE_HISTORY_SLOTS: int = 8
PERSISTENCE_MOMENTUM_THRESHOLD: float = 35.0   # stricter: min score to count as "active"
PERSISTENCE_MIN_HISTORY: int = 1               # start scoring after first cycle

# ── GitHub activity tracker ───────────────────────────────────────────────────
GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")   # optional; raises rate limit 60→5000/hr
GITHUB_ENABLED: bool = os.getenv("GITHUB_ENABLED", "true").lower() == "true"
GITHUB_FETCH_INTERVAL_CYCLES: int = int(os.getenv("GITHUB_FETCH_INTERVAL_CYCLES", "4"))

# ── News feed ─────────────────────────────────────────────────────────────────
NEWS_ENABLED: bool = os.getenv("NEWS_ENABLED", "true").lower() == "true"

# ── CoinPaprika enrichment ────────────────────────────────────────────────────
COINPAPRIKA_ENABLED: bool = os.getenv("COINPAPRIKA_ENABLED", "true").lower() == "true"
COINPAPRIKA_ENRICH_TOP_N: int = int(os.getenv("COINPAPRIKA_ENRICH_TOP_N", "40"))
COINPAPRIKA_FETCH_INTERVAL_CYCLES: int = int(os.getenv("COINPAPRIKA_FETCH_INTERVAL_CYCLES", "8"))

