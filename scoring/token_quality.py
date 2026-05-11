"""
Narrative-aware token quality scoring.

final_score = base_momentum_score
            × narrative_confidence_mult  (0.65 – 1.50)
            × persistence_mult           (1.00 – 1.35)
            × relative_strength_mult     (0.88 – 1.15)
            × cluster_mult               (1.00 – 1.15)
            × liquidity_quality_mult     (0.85 – 1.10)
            + github_bonus               (0 – 8 pts)

Also computes:
  • relative_strength: composite rank within narrative peer group (0-100)
    = momentum(50%) + persistence(30%) + price_direction(20%)
  • signal_explanation: deterministic, rule-based reasoning bullets (max 5)

Design principles:
  - Tokens in strong, persistent narratives clearly outrank isolated pumps
  - RS reflects multi-factor peer comparison, not just momentum
  - Every token gets ≥1 explanation bullet — empty WHY is never acceptable
  - Meme / Other narratives are penalised in final scoring
  - Never uses LLM, ML, or heuristics — pure arithmetic
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Optional

from models.schemas import GithubSignal, NarrativeScore, TokenSnapshot

# Narratives where final_score is penalised regardless of momentum
_PENALISED: dict[str, float] = {"Meme": 0.65, "Other": 0.82}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_final_scores(
    snapshots: list[TokenSnapshot],
    narrative_score_map: dict[str, NarrativeScore],
    github_map: dict[str, GithubSignal] | None = None,
) -> list[TokenSnapshot]:
    """
    Apply narrative multipliers, relative strength, and generate explanations.
    Returns snapshots sorted by final_score descending.
    """
    if github_map is None:
        github_map = {}

    # Step 1: composite relative strength within each narrative peer group
    _compute_relative_strength(snapshots)

    # Step 2: per-token final score + explanation
    for snap in snapshots:
        ns = narrative_score_map.get(snap.narrative or "Other")
        gh = github_map.get(snap.token_id)
        snap.final_score = _compute_final_score(snap, ns, gh)
        snap.signal_explanation = _generate_explanation(snap, ns, gh)

    return sorted(snapshots, key=lambda x: x.final_score, reverse=True)


# ---------------------------------------------------------------------------
# Relative strength
# ---------------------------------------------------------------------------

def _composite(t: TokenSnapshot) -> float:
    """
    Multi-factor composite score for peer-relative ranking.
      momentum  50% — multi-signal quality
      persist   30% — track record across cycles
      price_dir 20% — current directional bias (capped at ±50%)
    Result is 0-100.
    """
    price_norm = min(max(t.price_change_24h, -50.0), 50.0) / 50.0 * 50.0 + 50.0  # 0-100
    return t.momentum_score * 0.50 + t.persistence_score * 0.30 + price_norm * 0.20


def _compute_relative_strength(snapshots: list[TokenSnapshot]) -> None:
    """
    Rank each token relative to its narrative peer group using a composite score.
    Result stored as token.relative_strength (0-100).

    Single token → RS reflects absolute composite vs neutral baseline.
    Pairs (2 tokens) → winner/loser split with headroom.
    3+ tokens → z-score normalisation.
    """
    groups: dict[str, list[TokenSnapshot]] = defaultdict(list)
    for snap in snapshots:
        groups[snap.narrative or "Other"].append(snap)

    for _, tokens in groups.items():
        composites = [_composite(t) for t in tokens]

        if len(tokens) == 1:
            # Single token: RS = composite capped to [20, 80] to avoid extremes
            tokens[0].relative_strength = round(min(max(composites[0], 20.0), 80.0), 2)
            continue

        if len(tokens) == 2:
            # Two tokens: linear split around midpoint
            lo, hi = min(composites), max(composites)
            span = max(hi - lo, 1.0)
            for t, c in zip(tokens, composites):
                # Winner gets ~65-75, loser gets ~35-40
                t.relative_strength = round(35.0 + (c - lo) / span * 40.0, 2)
            continue

        # 3+ tokens: z-score mapped [−3, +3] → [0, 100]
        avg = statistics.mean(composites)
        std = max(statistics.stdev(composites), 0.5)
        for t, c in zip(tokens, composites):
            z = (c - avg) / std
            t.relative_strength = round(min(max((z + 3.0) / 6.0 * 100.0, 0.0), 100.0), 2)


# ---------------------------------------------------------------------------
# Final score formula
# ---------------------------------------------------------------------------

def _compute_final_score(
    snap: TokenSnapshot,
    ns: Optional[NarrativeScore],
    gh: Optional[GithubSignal],
) -> float:
    base = snap.momentum_score

    # ── Narrative quality multiplier ─────────────────────────────────────────
    # Range: 0.65 (Meme) … 0.82 (Other) … 0.88 (weak) … 1.50 (strong, conf=100)
    if snap.narrative in _PENALISED:
        narr_mult = _PENALISED[snap.narrative]
    elif ns and ns.confidence >= 20:
        # 20 conf → ×1.10; 50 → ×1.25; 80 → ×1.40; 100 → ×1.50
        narr_mult = 1.0 + (ns.confidence / 100) * 0.50
    else:
        narr_mult = 0.88  # unknown/weak narrative penalty

    # ── Persistence multiplier ────────────────────────────────────────────────
    # Range: 1.00 (no history) … 1.35 (full sustained)
    persist_mult = 1.0 + (snap.persistence_score / 100) * 0.35

    # ── Relative-strength multiplier ─────────────────────────────────────────
    # Range: 0.88 (RS=0) … 1.00 (RS=44) … 1.15 (RS=100)
    rs_mult = 0.88 + (snap.relative_strength / 100) * 0.27

    # ── Cluster size multiplier ───────────────────────────────────────────────
    cluster_size = ns.token_count if ns else 0
    cluster_mult = 1.0 + min(cluster_size, 10) / 10 * 0.15  # 1.00 – 1.15

    # ── Liquidity quality multiplier ─────────────────────────────────────────
    liq_mult = _liq_quality_mult(snap.liquidity, snap.market_cap)

    # ── GitHub activity bonus ─────────────────────────────────────────────────
    # Additive bonus (0–8 pts) for technically active projects
    gh_bonus = 0.0
    if gh and gh.activity_score > 0:
        gh_bonus = gh.activity_score / 100 * 8  # max +8 pts

    final = base * narr_mult * persist_mult * rs_mult * cluster_mult * liq_mult + gh_bonus
    return round(min(max(final, 0), 100), 2)


def _liq_quality_mult(liquidity: float, market_cap: float) -> float:
    """Multiplier based on liquidity-to-market-cap ratio."""
    if liquidity <= 0:
        # CoinGecko tokens — no DEX liquidity data; treat neutrally
        return 1.00
    if market_cap <= 0:
        return 0.90

    ratio = liquidity / market_cap
    if ratio >= 0.15:   return 1.10   # very deep (≥15% liq/mcap)
    elif ratio >= 0.08: return 1.05   # healthy
    elif ratio >= 0.04: return 1.00   # acceptable
    elif ratio >= 0.02: return 0.95   # thin
    else:               return 0.85   # very thin / suspicious


# ---------------------------------------------------------------------------
# Signal explanation  (deterministic, always ≥1 bullet)
# ---------------------------------------------------------------------------

def _generate_explanation(
    snap: TokenSnapshot,
    ns: Optional[NarrativeScore],
    gh: Optional[GithubSignal],
) -> list[str]:
    """Generate up to 5 concise deterministic explanation bullets."""
    reasons: list[str] = []

    # Narrative alignment
    if ns and ns.confidence >= 50:
        reasons.append(f"Strong {snap.narrative} narrative (conf {ns.confidence:.0f})")
    elif ns and ns.confidence >= 25:
        reasons.append(f"Active {snap.narrative} narrative ({ns.confidence:.0f})")
    elif snap.narrative and snap.narrative not in ("Other", "Unknown", None):
        reasons.append(f"Tagged: {snap.narrative}")

    # Relative strength within peer group
    if snap.relative_strength >= 75:
        reasons.append(f"Top-tier in {snap.narrative} (RS {snap.relative_strength:.0f}/100)")
    elif snap.relative_strength >= 55:
        reasons.append(f"Above-avg in {snap.narrative} (RS {snap.relative_strength:.0f}/100)")

    # Multi-signal momentum quality
    if snap.momentum_score >= 55:
        reasons.append("Multi-signal momentum confirmed")
    elif snap.momentum_score >= 30:
        reasons.append(f"Moderate momentum ({snap.momentum_score:.0f}/100)")

    # Persistence
    if snap.persistence_score >= 50:
        reasons.append(f"Sustained signal (persist {snap.persistence_score:.0f}/100)")
    elif snap.persistence_score >= 15:
        reasons.append(f"Persistence building ({snap.persistence_score:.0f}/100)")

    # Liquidity quality
    if snap.liquidity >= 5_000_000:
        reasons.append("Deep liquidity (≥$5M)")
    elif snap.liquidity >= 500_000:
        reasons.append("Healthy liquidity")
    elif snap.liquidity <= 0 and snap.market_cap >= 50_000_000:
        reasons.append("Established cap ($50M+)")
    elif snap.liquidity <= 0 and snap.market_cap >= 10_000_000:
        reasons.append(f"Market cap ${snap.market_cap / 1e6:.0f}M")

    # Price trend quality
    if 5 <= snap.price_change_24h <= 40:
        reasons.append(f"Clean uptrend {snap.price_change_24h:+.1f}% (24h)")
    elif snap.price_change_24h > 40:
        reasons.append(f"Explosive {snap.price_change_24h:+.1f}% — verify organic")
    elif snap.price_change_24h < -10:
        reasons.append(f"Pullback {snap.price_change_24h:.1f}% (watch reversal)")

    # GitHub activity signal
    if gh and gh.activity_score >= 60:
        reasons.append(f"Active dev ({gh.days_since_push}d since last push)")

    # Ecosystem context (shown only if no other bullet already mentions it)
    if snap.ecosystem and snap.ecosystem not in ("Unknown", "Multi-chain", ""):
        if not any(snap.ecosystem in r for r in reasons):
            reasons.append(f"Ecosystem: {snap.ecosystem}")

    # Fallback: always give at least one bullet so WHY is never blank
    if not reasons:
        if snap.momentum_score > 0:
            reasons.append(f"Score: mom {snap.momentum_score:.0f} | final {snap.final_score:.0f}")
        else:
            reasons.append("New token — signal building")

    return reasons[:5]
