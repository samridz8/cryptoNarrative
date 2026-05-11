"""
CoinPaprika metadata enrichment.

Fetches tags/categories from the free CoinPaprika API to improve narrative
categorisation, particularly for tokens with ambiguous names.

Rate limits:
  • /v1/coins  — 1 request, ~25k entries, cached in memory for the run
  • /v1/coins/{id} — 1 request per token; we enrich the top-N tokens once
    every COINPAPRIKA_FETCH_INTERVAL_CYCLES cycles (~2 hrs at 15-min default)

No API key required.  Free tier: ~25k requests/month.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import httpx

from config import COINPAPRIKA_ENRICH_TOP_N
from models.schemas import TokenSnapshot

logger = logging.getLogger("narrative_radar")

_BASE = "https://api.coinpaprika.com/v1"
_TIMEOUT = httpx.Timeout(15.0)

# CoinPaprika tag slug → our narrative label
TAG_TO_NARRATIVE: dict[str, str] = {
    "artificial-intelligence":      "AI Agents",
    "decentralized-finance-defi":   "DeFi",
    "distributed-computing":        "DePIN",
    "depin":                        "DePIN",
    "gaming":                       "GameFi",
    "layer-2":                      "Layer2",
    "real-world-assets":            "RWA",
    "privacy":                      "Privacy",
    "social-fi":                    "SocialFi",
    "memes":                        "Meme",
    "solana-ecosystem":             "Solana",
    "base-ecosystem":               "Base",
}

# Runtime cache — built once per process, keyed symbol → paprika_id
_coin_index: dict[str, str] = {}     # UPPER(symbol) → paprika_id
_index_built: bool = False


# ---------------------------------------------------------------------------
# Coin index
# ---------------------------------------------------------------------------

async def build_coin_index() -> None:
    """Fetch /v1/coins and populate the symbol→id index (1 request)."""
    global _coin_index, _index_built
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(f"{_BASE}/coins")
            resp.raise_for_status()
            coins = resp.json()
        _coin_index = {
            c["symbol"].upper(): c["id"]
            for c in coins
            if c.get("is_active") and not c.get("is_new")
        }
        _index_built = True
        logger.debug(f"[CoinPaprika] index built: {len(_coin_index)} coins")
    except Exception as exc:
        logger.warning(f"[CoinPaprika] could not build index: {exc}")


async def _ensure_index() -> None:
    global _index_built
    if not _index_built:
        await build_coin_index()


# ---------------------------------------------------------------------------
# Tag enrichment
# ---------------------------------------------------------------------------

async def _fetch_tags(paprika_id: str) -> list[str]:
    """Return list of tag slugs for a CoinPaprika coin id."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(f"{_BASE}/coins/{paprika_id}")
            resp.raise_for_status()
            data = resp.json()
        return [t.get("id", "") for t in data.get("tags", [])]
    except Exception:
        return []


def _tags_to_narrative(tags: list[str]) -> Optional[str]:
    for tag in tags:
        narrative = TAG_TO_NARRATIVE.get(tag.lower())
        if narrative:
            return narrative
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def enrich_snapshots(snapshots: list[TokenSnapshot], top_n: int = COINPAPRIKA_ENRICH_TOP_N) -> None:
    """
    In-place narrative enrichment for the top-N tokens by momentum_score.

    Only updates tokens whose narrative is "Other" or missing — respects
    keyword-matched narratives already assigned by narratives.py.
    """
    await _ensure_index()
    if not _coin_index:
        return

    # Only try to enrich tokens with weak narrative assignments
    candidates = [
        s for s in snapshots
        if not s.narrative or s.narrative == "Other"
    ][:top_n]

    # Throttle: fetch sequentially with a small sleep to be polite
    for snap in candidates:
        paprika_id = _coin_index.get(snap.symbol.upper())
        if not paprika_id:
            continue
        tags = await _fetch_tags(paprika_id)
        narrative = _tags_to_narrative(tags)
        if narrative:
            snap.narrative = narrative
            logger.debug(f"[CoinPaprika] {snap.symbol} → {narrative} (tags: {tags[:3]})")
        await asyncio.sleep(0.1)   # gentle 100ms between requests
