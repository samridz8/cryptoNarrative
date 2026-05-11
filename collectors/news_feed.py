"""
Crypto news RSS feed collector.

Parses RSS 2.0 / Atom feeds from CoinTelegraph and Decrypt using the
stdlib xml.etree.ElementTree — no extra dependencies.

Articles are mapped to narratives via keyword matching on the headline.
Deduplication is done via a SHA-1 hash of the title.
"""
from __future__ import annotations

import hashlib
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import httpx

from models.schemas import NewsItem

logger = logging.getLogger("narrative_radar")

_TIMEOUT = httpx.Timeout(15.0)

RSS_FEEDS: list[str] = [
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
]

# Headline keywords → narrative (checked in order; first match wins)
NEWS_NARRATIVE_KEYWORDS: dict[str, list[str]] = {
    "AI Agents": [
        "artificial intelligence", "ai agent", "chatgpt", "llm", "bittensor",
        "fetch.ai", "singularity", "neural", "inference", "deep learning",
    ],
    "DePIN": [
        "depin", "helium", "filecoin", "akash", "render", "gpu compute",
        "decentralized infrastructure", "physical network",
    ],
    "RWA": [
        "real-world asset", "rwa", "tokenized", "treasury bond",
        "ondo", "real estate", "commodit",
    ],
    "Layer2": [
        "layer 2", "l2", "arbitrum", "optimism", "zk rollup", "zkSync",
        "starknet", "polygon", "blast", "scroll", "linea",
    ],
    "DeFi": [
        "defi", "decentralized finance", "uniswap", "aave", "gmx", "yield",
        "lending protocol", "liquidity pool", "amm",
    ],
    "GameFi": [
        "play-to-earn", "p2e", "blockchain game", "metaverse", "nft game",
        "axie", "illuvium", "gods unchained",
    ],
    "Meme": [
        "meme coin", "memecoin", "dogecoin", "shib", "pepe", "bonk",
        "doge", "wif", "popcat",
    ],
    "SocialFi": [
        "socialfi", "social finance", "lens protocol", "farcaster",
        "creator economy", "friend.tech",
    ],
    "Privacy": [
        "privacy coin", "monero", "zcash", "zero-knowledge", "anonymous",
        "stealth address",
    ],
    "Solana": [
        "solana", "sol ecosystem", "raydium", "jupiter aggregator",
        "phantom wallet", "jito",
    ],
    "Base": [
        " base network", "base chain", "brett", "toshi", "aerodrome",
    ],
}


def _title_hash(title: str) -> str:
    return hashlib.sha1(title.strip().lower().encode()).hexdigest()[:16]


def _map_headline_to_narrative(title: str) -> str:
    lower = title.lower()
    for narrative, keywords in NEWS_NARRATIVE_KEYWORDS.items():
        for kw in keywords:
            if kw in lower:
                return narrative
    return ""


def _parse_feed_xml(xml_text: str, feed_url: str) -> list[NewsItem]:
    """Parse RSS 2.0 or Atom XML into NewsItem list."""
    items: list[NewsItem] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.warning(f"[News] XML parse error for {feed_url}: {exc}")
        return items

    # RSS 2.0
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    entries = root.findall(".//item") or root.findall(".//atom:entry", ns)

    for entry in entries:
        # Title
        title_el = entry.find("title")
        if title_el is None:
            title_el = entry.find("atom:title", ns)
        title = (title_el.text or "").strip() if title_el is not None else ""
        if not title:
            continue

        # Link
        link_el = entry.find("link")
        if link_el is not None:
            link = (link_el.text or link_el.get("href") or "").strip()
        else:
            link = ""

        # PubDate
        pub_el = (
            entry.find("pubDate")
            or entry.find("dc:date")
            or entry.find("atom:published", ns)
        )
        pub_date = (pub_el.text or "").strip() if pub_el is not None else ""

        items.append(NewsItem(
            title=title,
            link=link,
            pub_date=pub_date,
            narrative=_map_headline_to_narrative(title),
            title_hash=_title_hash(title),
            timestamp=datetime.now(timezone.utc),
        ))

    return items


async def fetch_news_items() -> list[NewsItem]:
    """
    Fetch all RSS feeds concurrently and return deduplicated list of NewsItem.
    """
    import asyncio

    async def _fetch_one(client: httpx.AsyncClient, url: str) -> list[NewsItem]:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            return _parse_feed_xml(resp.text, url)
        except Exception as exc:
            logger.debug(f"[News] {url}: {exc}")
            return []

    all_items: list[NewsItem] = []
    seen_hashes: set[str] = set()

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        results = await asyncio.gather(*[_fetch_one(client, url) for url in RSS_FEEDS])

    for batch in results:
        for item in batch:
            if item.title_hash not in seen_hashes:
                seen_hashes.add(item.title_hash)
                all_items.append(item)

    logger.info(f"[News] fetched {len(all_items)} unique articles across {len(RSS_FEEDS)} feeds")
    return all_items
