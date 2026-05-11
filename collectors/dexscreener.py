"""
DexScreener collector — free public API, no key required.
Sources used:
  • /token-boosts/top/v1       → top-boosted token addresses
  • /token-profiles/latest/v1  → recently listed token profiles
  • /latest/dex/tokens/{addr}  → pair/market data for resolved addresses
"""
import httpx

from models.schemas import TokenSnapshot
from utils.logger import logger

_BASE = "https://api.dexscreener.com"
_HEADERS = {"Accept": "application/json"}
_TIMEOUT = 30
_MIN_LIQUIDITY_USD = 10_000  # ignore pairs too thin to be reliable


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

async def fetch_top_boosts() -> list[TokenSnapshot]:
    """Top boosted tokens — strong social-narrative signal."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_HEADERS) as client:
            resp = await client.get(f"{_BASE}/token-boosts/top/v1")
            resp.raise_for_status()
            boosts = resp.json()

        if not isinstance(boosts, list) or not boosts:
            return []

        chain_addrs: dict[str, list[str]] = {}
        for b in boosts[:50]:
            chain = b.get("chainId", "")
            addr = b.get("tokenAddress", "")
            if chain and addr:
                chain_addrs.setdefault(chain, []).append(addr)

        snapshots = await _resolve_pairs(chain_addrs, source="dexscreener_boosts")
        logger.info(f"DexScreener boosts  → {len(snapshots)} tokens")
        return snapshots

    except Exception as exc:
        logger.error(f"DexScreener boosts error: {exc}")
        return []


async def fetch_latest_profiles() -> list[TokenSnapshot]:
    """Recently created token profiles — early-discovery signal."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_HEADERS) as client:
            resp = await client.get(f"{_BASE}/token-profiles/latest/v1")
            resp.raise_for_status()
            profiles = resp.json()

        if not isinstance(profiles, list) or not profiles:
            return []

        chain_addrs: dict[str, list[str]] = {}
        for p in profiles[:40]:
            chain = p.get("chainId", "")
            addr = p.get("tokenAddress", "")
            if chain and addr:
                chain_addrs.setdefault(chain, []).append(addr)

        snapshots = await _resolve_pairs(chain_addrs, source="dexscreener_profiles")
        logger.info(f"DexScreener profiles→ {len(snapshots)} tokens")
        return snapshots

    except Exception as exc:
        logger.error(f"DexScreener profiles error: {exc}")
        return []


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

async def _resolve_pairs(
    chain_addrs: dict[str, list[str]],
    source: str,
) -> list[TokenSnapshot]:
    """
    Fetch DexScreener pair data for a map of {chain: [addresses]}.
    Returns one snapshot per token (highest-liquidity pair wins).
    """
    snapshots: list[TokenSnapshot] = []

    async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_HEADERS) as client:
        for chain_id, addresses in list(chain_addrs.items())[:6]:  # cap chains
            for i in range(0, len(addresses), 30):  # API allows ~30 per call
                batch = addresses[i : i + 30]
                addr_str = ",".join(batch)
                try:
                    resp = await client.get(
                        f"{_BASE}/latest/dex/tokens/{addr_str}"
                    )
                    resp.raise_for_status()
                    data = resp.json()
                except Exception as exc:
                    logger.warning(f"DexScreener pair fetch ({chain_id}): {exc}")
                    continue

                # Keep best pair (highest liquidity) per base token address
                best: dict[str, tuple] = {}
                for pair in data.get("pairs") or []:
                    liq = float((pair.get("liquidity") or {}).get("usd") or 0)
                    base_addr = (
                        (pair.get("baseToken") or {}).get("address") or ""
                    ).lower()
                    if not base_addr:
                        continue
                    if base_addr not in best or liq > best[base_addr][1]:
                        best[base_addr] = (pair, liq)

                for addr, (pair, liq_usd) in best.items():
                    if liq_usd < _MIN_LIQUIDITY_USD:
                        continue

                    base = pair.get("baseToken") or {}
                    pc = pair.get("priceChange") or {}
                    vol = pair.get("volume") or {}

                    snapshots.append(
                        TokenSnapshot(
                            token_id=f"dex_{chain_id}_{addr[:10]}",
                            symbol=(base.get("symbol") or "").upper(),
                            name=base.get("name", ""),
                            price_usd=float(pair.get("priceUsd") or 0),
                            price_change_24h=float(pc.get("h24") or 0),
                            price_change_1h=float(pc.get("h1") or 0),
                            market_cap=float(
                                pair.get("marketCap") or pair.get("fdv") or 0
                            ),
                            volume_24h=float(vol.get("h24") or 0),
                            liquidity=liq_usd,
                            chain=chain_id,
                            source=source,
                        )
                    )

    return snapshots
