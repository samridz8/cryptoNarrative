"""
Narrative keyword categorisation and cluster scoring.

Philosophy: no ML, no LLM — just fast keyword matching + configurable weights.

Confidence formula:
  raw  = token_count_bonus(0-30) + momentum_contrib(0-50) + price_contrib(0-20)
  + persistence_boost (0-15)   ← lifted if narrative has been consistently active
  × narrative_weight            ← AI/DePIN boosted; Meme downweighted
  capped at 95

Cluster detection:
  ≥3 tokens moving together in the same narrative is a strong signal.
  Each additional confirming token adds to token_count_bonus.
"""
from collections import defaultdict
from typing import Any

from config import DEFAULT_NARRATIVE_WEIGHT, NARRATIVE_WEIGHTS
from models.schemas import NarrativeScore, TokenSnapshot

# ---------------------------------------------------------------------------
# Narrative keyword map
# ---------------------------------------------------------------------------

NARRATIVE_KEYWORDS: dict[str, list[str]] = {
    "AI Agents": [
        "ai", "agent", "gpt", "virtual", "autonomous", "neural", "intelligence",
        "llm", "cognitive", "sentient", "artificial", "mind", "brain", "agi",
        "deepseek", "openai", "claude", "inference", "bittensor", "fetch",
        "singularity", "ocean", "numerai",
    ],
    "DePIN": [
        "gpu", "compute", "render", "infrastructure", "depin", "physical",
        "hardware", "bandwidth", "storage", "power", "energy", "miner",
        "hotspot", "helium", "io.net", "akash", "filecoin", "arweave",
        "network3", "geodnet", "hivemapper",
    ],
    "RWA": [
        "real world", "rwa", "treasury", "bond", "property",
        "real estate", "commodity", "gold", "silver", "tokenized", "ondo",
        "centrifuge", "maple", "backed", "euler",
    ],
    "GameFi": [
        "game", "gaming", "play", "metaverse", "guild", "rpg",
        "quest", "hero", "battle", "arena", "loot", "pixel", "fighter",
        "gamer", "esport", "illuvium", "axie", "gods unchained",
    ],
    "DeFi": [
        "defi", "swap", "liquidity", "yield", "lending", "borrow", "amm",
        "dex", "vault", "stake", "farm", "pool", "perp", "perpetual",
        "options", "derivatives", "gmx", "aave", "compound", "curve",
    ],
    "Meme": [
        "meme", "dog", "cat", "pepe", "shib", "wojak", "doge",
        "inu", "elon", "pump", "chad", "frog",
        "bonk", "wif", "popcat", "floki", "babydoge",
    ],
    "Layer2": [
        "layer2", "l2", "rollup", "zk", "scaling", "bridge",
        "arbitrum", "optimism", "stark", "polygon", "blast", "scroll",
        "linea", "zksync", "base chain",
    ],
    "SocialFi": [
        "social", "creator", "fan", "content", "follow", "friend",
        "community", "media", "influencer", "stream", "lens", "farcaster",
    ],
    "Privacy": [
        "privacy", "private", "anonymous", "mixer", "stealth",
        "zero knowledge", "confidential", "shield", "incognito", "monero",
        "zcash", "tornado",
    ],
    "Solana": [
        "solana", "spl", "raydium", "serum", "jito", "drift",
        "jupiter", "marinade", "tensor", "metaplex",
    ],
    "Base": [
        "brett", "toshi", "aerodrome", "base ecosystem",
    ],
}

_UNKNOWN = "Other"

# Minimum token count before we emit a narrative score
_MIN_CLUSTER_SIZE = 2


# ---------------------------------------------------------------------------
# Per-token categorisation
# ---------------------------------------------------------------------------

def categorize_token(name: str, symbol: str, description: str = "") -> str:
    text = f"{name} {symbol} {description}".lower()
    scores: dict[str, int] = defaultdict(int)
    for narrative, keywords in NARRATIVE_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                scores[narrative] += 1
    if not scores:
        return _UNKNOWN
    return max(scores, key=lambda k: scores[k])


def categorize_snapshots(snapshots: list[TokenSnapshot]) -> list[TokenSnapshot]:
    for snap in snapshots:
        if not snap.narrative or snap.narrative == _UNKNOWN:
            snap.narrative = categorize_token(snap.name, snap.symbol)
    return snapshots


# ---------------------------------------------------------------------------
# Narrative-level cluster scoring
# ---------------------------------------------------------------------------

def compute_narrative_scores(
    snapshots: list[TokenSnapshot],
    narrative_history: dict[str, list[dict[str, Any]]] | None = None,
) -> list[NarrativeScore]:
    """
    Group tokens by narrative, score each cluster, apply weights + persistence.

    narrative_history: {narrative_name: [recent NarrativeScore rows from DB]}
    """
    if narrative_history is None:
        narrative_history = {}

    groups: dict[str, list[TokenSnapshot]] = defaultdict(list)
    for snap in snapshots:
        if snap.narrative and snap.narrative != _UNKNOWN:
            groups[snap.narrative].append(snap)

    scores: list[NarrativeScore] = []
    for narrative, tokens in groups.items():
        if len(tokens) < _MIN_CLUSTER_SIZE:
            continue

        avg_momentum = sum(t.momentum_score for t in tokens) / len(tokens)
        avg_price_change = sum(t.price_change_24h for t in tokens) / len(tokens)
        total_volume = sum(t.volume_24h for t in tokens)
        total_mcap = sum(t.market_cap for t in tokens if t.market_cap > 0)

        # Volume spike: vol-to-mcap ratio as percentage (0-100)
        vol_spike = min((total_volume / total_mcap * 100) if total_mcap > 0 else 0, 100)

        # --- Raw confidence components ---
        # Token count bonus: 3 tokens = 16pts, 5 = 24pts, 8+ = 30pts
        # Stronger cluster requirement than before (was *8 per token)
        token_bonus = min(len(tokens) * 6, 30)

        # Momentum component: scale to 0-50
        momentum_contrib = min(avg_momentum * 0.5, 50)

        # Price component: moderate sustained uptrend is rewarded, not violent pumps
        # avg_price_change is already capped via price_velocity filter upstream
        price_contrib = min(max(avg_price_change, 0), 20)

        # Cluster coherence bonus: how many tokens have BOTH rising price AND rising volume
        coherent = sum(
            1 for t in tokens
            if t.price_change_24h > 3 and t.volume_24h > 0
        )
        cluster_bonus = min(coherent * 3, 12)  # up to +12 pts for coherent cluster

        # Ecosystem coherence bonus: when ≥50% of tokens share the same chain
        from collections import Counter as _Counter
        chain_counts = _Counter(t.chain for t in tokens if t.chain and t.chain != "unknown")
        if chain_counts:
            dominant_chain, dominant_count = chain_counts.most_common(1)[0]
            if dominant_count / len(tokens) >= 0.5:
                # Scale: 50% → +2 pts, 70% → +4 pts, 100% → +6 pts
                eco_bonus = round((dominant_count / len(tokens) - 0.5) * 12, 2)  # max 6
                cluster_bonus = min(cluster_bonus + eco_bonus, 18)

        raw_confidence = token_bonus + momentum_contrib + price_contrib + cluster_bonus

        # --- Persistence boost ---
        persistence = _narrative_persistence(narrative, narrative_history)
        persistence_boost = persistence * 0.15  # 0-15 pts

        # --- Narrative weight ---
        weight = NARRATIVE_WEIGHTS.get(narrative, DEFAULT_NARRATIVE_WEIGHT)
        final_confidence = min((raw_confidence + persistence_boost) * weight, 95)

        # Skip flat / no-signal narratives
        if avg_momentum < 5 and avg_price_change < 1:
            continue

        top_symbols = [
            t.symbol
            for t in sorted(tokens, key=lambda x: x.momentum_score, reverse=True)[:8]
        ]

        scores.append(
            NarrativeScore(
                narrative=narrative,
                token_count=len(tokens),
                avg_momentum_score=round(avg_momentum, 2),
                volume_spike=round(vol_spike, 2),
                confidence=round(final_confidence, 2),
                persistence_score=round(persistence, 2),
                tokens=top_symbols,
            )
        )

    return sorted(scores, key=lambda x: x.confidence, reverse=True)


# ---------------------------------------------------------------------------
# Narrative persistence helper
# ---------------------------------------------------------------------------

def _narrative_persistence(
    narrative: str,
    history: dict[str, list[dict[str, Any]]],
) -> float:
    """
    0-100.  Average confidence of this narrative across recent cycles.
    High persistence means the narrative has been active for multiple cycles.
    """
    records = history.get(narrative, [])
    if not records:
        return 0.0

    # Weight recent records more heavily
    weighted = sum(
        (h.get("confidence") or 0) / (i + 1)
        for i, h in enumerate(records[:6])
    )
    max_possible = sum(1 / (i + 1) for i in range(min(len(records), 6))) * 95
    if max_possible <= 0:
        return 0.0

    return round(min(weighted / max_possible * 100, 100), 2)

