"""
Token momentum scoring.

Score (0-100) = volume_acceleration(0-25)
              + price_velocity(0-25)
              + trend_persistence(0-25)
              + liquidity_health(0-25)
              × multi_signal_multiplier (0.6 – 1.0)
              + ecosystem_cluster_bonus (+0-10)

Multi-signal confirmation prevents any single metric from dominating:
  1 active signal  → ×0.60
  2 active signals → ×0.80
  3 active signals → ×0.92
  4 active signals → ×1.00

Persistence score (0-100) is computed separately and stored on the snapshot
so the dashboard can display it without affecting the main score.
"""
import math
from collections import Counter
from typing import Any

from config import PERSISTENCE_HISTORY_SLOTS, PERSISTENCE_MIN_HISTORY, PERSISTENCE_MOMENTUM_THRESHOLD
from models.schemas import TokenSnapshot

# Thresholds for "this signal is meaningfully active"
_VA_ACTIVE = 10.0   # volume acceleration
_PV_ACTIVE = 8.0    # price velocity (upward)
_TP_ACTIVE = 10.0   # trend persistence
_LH_ACTIVE = 8.0    # liquidity health

_CONFIRMATION_MULTIPLIERS = {0: 0.40, 1: 0.60, 2: 0.80, 3: 0.92, 4: 1.00}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def score_snapshots(
    snapshots: list[TokenSnapshot],
    history_map: dict[str, list[dict[str, Any]]],
) -> list[TokenSnapshot]:
    """Score all snapshots, apply cluster bonus, compute persistence, sort."""
    for snap in snapshots:
        history = history_map.get(snap.token_id, [])
        snap.momentum_score = _compute_score(snap, history)
        snap.persistence_score = _compute_persistence(history)

    # Ecosystem clustering bonus — narrative with 3+ tokens gets a small lift
    narrative_counts = Counter(s.narrative for s in snapshots if s.narrative)
    for snap in snapshots:
        if snap.narrative:
            count = narrative_counts[snap.narrative]
            if count >= 3:
                bonus = min((count - 1) * 1.5, 10)  # softer than before
                snap.momentum_score = round(min(snap.momentum_score + bonus, 100), 2)

    return sorted(snapshots, key=lambda x: x.momentum_score, reverse=True)


# ---------------------------------------------------------------------------
# Core score formula
# ---------------------------------------------------------------------------

def _compute_score(snap: TokenSnapshot, history: list[dict[str, Any]]) -> float:
    va = _volume_acceleration(snap.volume_24h, history)
    pv = _price_velocity(snap.price_change_24h, snap.price_change_1h)
    tp = _trend_persistence(history)
    lh = _liquidity_health(snap.liquidity, snap.volume_24h)

    base = va + pv + tp + lh  # 0-100 raw

    # Multi-signal confirmation: penalise single-metric spikes
    active = sum([va >= _VA_ACTIVE, pv >= _PV_ACTIVE, tp >= _TP_ACTIVE, lh >= _LH_ACTIVE])
    multiplier = _CONFIRMATION_MULTIPLIERS.get(active, 1.0)

    return round(min(max(base * multiplier, 0), 100), 2)


# ---------------------------------------------------------------------------
# Component scorers (each 0-25 pts)
# ---------------------------------------------------------------------------

def _volume_acceleration(current_vol: float, history: list[dict]) -> float:
    """
    0-25 pts.  How much current volume exceeds historical average.
    Log scale prevents a single 100× pump from dominating.
    Capped at 22 (not 25) to leave headroom for multi-signal confirmation.
    """
    if not history or current_vol <= 0:
        return 4.0  # neutral baseline

    prev_vols = [h["volume_24h"] for h in history if (h.get("volume_24h") or 0) > 0]
    if not prev_vols:
        return 4.0

    avg = sum(prev_vols) / len(prev_vols)
    if avg <= 0:
        return 4.0

    ratio = current_vol / avg
    # ratio=1 → 7.0, ratio=3 → 14.0, ratio=7 → 19.3, ratio=15 → 22.0
    return round(min(math.log2(ratio + 1) * 7, 22), 2)


def _price_velocity(change_24h: float, change_1h: float) -> float:
    """
    0-25 pts.  Rewards sustained upward moves; punishes violent pumps.
    Daily component capped at 50% (not 500%+) to avoid pump inflation.
    Hourly gives a small acceleration bonus.
    """
    # Cap at 50% daily to reflect realistic sustained uptrends
    capped_daily = min(max(change_24h, 0), 50)
    daily = capped_daily / 50 * 18          # 50% move → 18 pts

    capped_hourly = min(max(change_1h, 0), 10)
    hourly = capped_hourly / 10 * 7         # 10% 1h move → 7 pts

    return round(min(daily + hourly, 25), 2)


def _trend_persistence(history: list[dict]) -> float:
    """
    0-25 pts.  Rewards tokens that have stayed positive across multiple cycles.

    Two components:
    • streak:    consecutive most-recent periods with positive 24h change (0-15)
    • regularity: fraction of all history periods that were positive     (0-10)
    """
    if not history:
        return 2.0

    # streak — most-recent consecutive positives
    streak = 0
    for h in history:
        if (h.get("price_change_24h") or 0) > 0:
            streak += 1
        else:
            break

    streak_pts = min(streak * 3, 15)

    # regularity
    positive_count = sum(1 for h in history if (h.get("price_change_24h") or 0) > 0)
    regularity_pts = (positive_count / len(history)) * 10

    return round(min(streak_pts + regularity_pts, 25), 2)


def _liquidity_health(liquidity: float, volume: float) -> float:
    """
    0-25 pts.  Rewards tokens with healthy liquidity relative to their volume.
    Tokens with unknown liquidity (CoinGecko) receive a neutral 6 pts.
    """
    if liquidity <= 0:
        return 6.0  # CoinGecko — liquidity unavailable; neutral score

    if volume <= 0:
        return 0.0

    ratio = liquidity / (volume + 1)
    # ratio=0.5 → 5, ratio=1 → 10, ratio=2 → 20, ratio=2.5+ → 25
    return round(min(ratio * 10, 25), 2)


# ---------------------------------------------------------------------------
# Persistence score (separate; stored on snapshot for dashboard display)
# ---------------------------------------------------------------------------

def _compute_persistence(history: list[dict]) -> float:
    """
    0-100.  What fraction of recent collection cycles had meaningful momentum.
    Decays quickly if the token goes cold.
    """
    if not history:
        return 0.0

    slots = min(len(history), PERSISTENCE_HISTORY_SLOTS)
    recent = history[:slots]

    # Require a minimum number of historical data points
    if len(recent) < PERSISTENCE_MIN_HISTORY:
        return 0.0

    threshold = PERSISTENCE_MOMENTUM_THRESHOLD
    hot_flags = [(h.get("momentum_score") or 0) >= threshold for h in recent]

    # Hard decay: most-recent cycle is cold → cap at 50
    most_recent_cold = not hot_flags[0]

    # Exponential decay weights: slot 0 (newest) → 0.5^0=1.0, slot 1 → 0.5, …
    weights = [0.5 ** i for i in range(slots)]
    weighted = sum(w for w, hot in zip(weights, hot_flags) if hot)
    max_weighted = sum(weights)

    raw = (weighted / max_weighted) * 100 if max_weighted > 0 else 0

    # Slot-ratio damper: smooth buildup so early cycles don't jump to 100.
    # 1 slot → ×0.125, 4 slots → ×0.50, 8 slots (full history) → ×1.00
    slot_ratio = slots / PERSISTENCE_HISTORY_SLOTS
    raw *= slot_ratio

    # Zigzag penalty: each hot/cold alternation costs 4 pts
    alternations = sum(
        1 for i in range(len(hot_flags) - 1)
        if hot_flags[i] != hot_flags[i + 1]
    )
    raw = max(raw - alternations * 4, 0)

    # Cap if most-recent cycle is cold
    if most_recent_cold:
        raw = min(raw, 50)

    return round(min(raw, 100), 2)

