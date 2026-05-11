"""
GitHub activity tracker for key crypto/DePIN/AI protocol repos.

Fetches public repository stats (stars, forks, last push date) without
requiring authentication.  An optional GITHUB_TOKEN env var raises the
rate limit from 60 → 5,000 req/hr.

Fetched every GITHUB_FETCH_INTERVAL_CYCLES collection cycles (~1 hr at
default 15-min cadence).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from config import GITHUB_TOKEN
from models.schemas import GithubSignal

logger = logging.getLogger("narrative_radar")

_BASE = "https://api.github.com/repos"
_TIMEOUT = httpx.Timeout(20.0)

# CoinGecko token-id → GitHub "owner/repo"  (curated, ~12 repos)
GITHUB_REPO_MAP: dict[str, str] = {
    # AI Agents / DePIN
    "bittensor":       "opentensor/bittensor",
    "fetch-ai":        "fetchai/fetchd",
    "ocean-protocol":  "oceanprotocol/contracts",
    "render-token":    "rendernetwork/rendernetwork",
    "akash-network":   "akash-network/akash",
    "filecoin":        "filecoin-project/lotus",
    "helium":          "helium/helium-program-library",
    # Layer2
    "arbitrum":        "OffchainLabs/nitro",
    "optimism":        "ethereum-optimism/optimism",
    # DeFi
    "uniswap":         "Uniswap/v4-core",
    "aave":            "aave/aave-v3-core",
    # RWA
    "ondo-finance":    "ondoprotocol/contracts",
}


def _activity_score(stars: int, days_since_push: int) -> float:
    """
    Blended activity score (0-100).

    Recency accounts for 70% — a recently pushed but less-starred repo
    outranks a stale popular one.
    """
    # Stars — log-scale, caps at 30 pts
    import math
    star_score = min(math.log10(stars + 1) / 5 * 100, 30) if stars > 0 else 0

    # Recency (0-100)
    if   days_since_push <=  3:  recency = 100
    elif days_since_push <=  7:  recency =  80
    elif days_since_push <= 14:  recency =  60
    elif days_since_push <= 30:  recency =  40
    elif days_since_push <= 90:  recency =  20
    else:                         recency =   5

    return round(star_score * 0.3 + recency * 0.7, 2)


def _days_since(iso_str: str) -> int:
    """Parse ISO-8601 datetime string and return days since now."""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - dt
        return max(delta.days, 0)
    except Exception:
        return 999


def _build_headers() -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return headers


async def fetch_all_github_stats() -> list[GithubSignal]:
    """
    Fetch stats for all repos in GITHUB_REPO_MAP.
    Returns a list of GithubSignal objects (one per token-id).
    """
    import asyncio
    headers = _build_headers()
    signals: list[GithubSignal] = []

    async with httpx.AsyncClient(timeout=_TIMEOUT, headers=headers) as client:
        for token_id, repo in GITHUB_REPO_MAP.items():
            try:
                resp = await client.get(f"{_BASE}/{repo}")
                if resp.status_code == 403:
                    logger.warning("[GitHub] rate-limited; stopping fetch early")
                    break
                resp.raise_for_status()
                data = resp.json()

                stars           = data.get("stargazers_count", 0)
                forks           = data.get("forks_count", 0)
                days_since_push = _days_since(data.get("pushed_at", ""))
                score           = _activity_score(stars, days_since_push)

                signals.append(GithubSignal(
                    token_id=token_id,
                    repo=repo,
                    stars=stars,
                    forks=forks,
                    days_since_push=days_since_push,
                    activity_score=score,
                ))
                logger.debug(
                    f"[GitHub] {token_id}: stars={stars}, push={days_since_push}d, score={score}"
                )
                # Respect rate limit — 60 req/hr unauthenticated → ~1 req/s safe
                await asyncio.sleep(0.5)

            except httpx.HTTPStatusError as exc:
                logger.debug(f"[GitHub] {repo}: HTTP {exc.response.status_code}")
            except Exception as exc:
                logger.debug(f"[GitHub] {repo}: {exc}")

    logger.info(f"[GitHub] fetched {len(signals)} repo signals")
    return signals
