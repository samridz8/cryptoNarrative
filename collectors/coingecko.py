"""
CoinGecko collector — uses the free public API (no key required).
Rate limit: ~30 calls/min on the free tier.
This module makes at most 2 calls per collection cycle.
"""
import httpx

from models.schemas import TokenSnapshot
from utils.logger import logger

_BASE = "https://api.coingecko.com/api/v3"
_HEADERS = {"Accept": "application/json"}
_TIMEOUT = 30


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

async def fetch_trending() -> list[TokenSnapshot]:
    """Fetch the CoinGecko trending coins list (top ~15)."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_HEADERS) as client:
            resp = await client.get(f"{_BASE}/search/trending")
            resp.raise_for_status()
            data = resp.json()

        snapshots: list[TokenSnapshot] = []
        for item in data.get("coins", []):
            coin = item.get("item", {})
            coin_data = coin.get("data", {})

            price_change_raw = coin_data.get("price_change_percentage_24h", {})
            price_change_24h = (
                price_change_raw.get("usd", 0.0)
                if isinstance(price_change_raw, dict)
                else float(price_change_raw or 0)
            )

            snapshots.append(
                TokenSnapshot(
                    token_id=coin.get("id", ""),
                    symbol=(coin.get("symbol") or "").upper(),
                    name=coin.get("name", ""),
                    price_usd=_parse_price(coin_data.get("price", 0)),
                    price_change_24h=price_change_24h,
                    price_change_1h=0.0,
                    market_cap=_parse_value(coin_data.get("market_cap", 0)),
                    volume_24h=_parse_value(coin_data.get("total_volume", 0)),
                    liquidity=0.0,
                    chain="multiple",
                    source="coingecko_trending",
                )
            )

        logger.info(f"CoinGecko trending  → {len(snapshots)} coins")
        return snapshots

    except Exception as exc:
        logger.error(f"CoinGecko trending error: {exc}")
        return []


async def fetch_markets(top_n: int = 200) -> list[TokenSnapshot]:
    """Fetch top coins ordered by 24-h volume with full market data."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_HEADERS) as client:
            resp = await client.get(
                f"{_BASE}/coins/markets",
                params={
                    "vs_currency": "usd",
                    "order": "volume_desc",
                    "per_page": min(top_n, 250),
                    "page": 1,
                    "sparkline": "false",
                    "price_change_percentage": "1h,24h",
                },
            )
            resp.raise_for_status()
            coins = resp.json()

        snapshots = [
            TokenSnapshot(
                token_id=c.get("id", ""),
                symbol=(c.get("symbol") or "").upper(),
                name=c.get("name", ""),
                price_usd=float(c.get("current_price") or 0),
                price_change_24h=float(c.get("price_change_percentage_24h") or 0),
                price_change_1h=float(
                    c.get("price_change_percentage_1h_in_currency") or 0
                ),
                market_cap=float(c.get("market_cap") or 0),
                volume_24h=float(c.get("total_volume") or 0),
                liquidity=0.0,
                chain="multiple",
                source="coingecko_markets",
            )
            for c in coins
        ]
        logger.info(f"CoinGecko markets   → {len(snapshots)} coins")
        return snapshots

    except Exception as exc:
        logger.error(f"CoinGecko markets error: {exc}")
        return []


# ---------------------------------------------------------------------------
# Private utilities
# ---------------------------------------------------------------------------

def _parse_price(raw) -> float:
    """Parse '$0.001234' or a number to float."""
    if isinstance(raw, (int, float)):
        return float(raw)
    try:
        return float(str(raw).replace("$", "").replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


def _parse_value(raw) -> float:
    """Parse '$1.2B', '$500M', '$3K', or a number to float."""
    if isinstance(raw, (int, float)):
        return float(raw)
    try:
        s = str(raw).replace("$", "").replace(",", "").strip()
        for suffix, mult in (("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3)):
            if s.upper().endswith(suffix):
                return float(s[:-1]) * mult
        return float(s)
    except (ValueError, TypeError):
        return 0.0
