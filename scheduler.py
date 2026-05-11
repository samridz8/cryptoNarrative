"""
Collection & analysis pipeline.

Called by APScheduler every N minutes and once on startup.
Pipeline:
  1. Fetch  →  2. Deduplicate  →  3. Quality filter  →  4. Categorise
  →  5. (CoinPaprika enrichment, every N cycles)
  →  6. Score (momentum + persistence)
  →  7. Narrative scoring
  →  8. Final scoring (narrative-aware, relative strength, explanations)
  →  9. Persist  →  10. Alerts  →  11. Log
  Optional async: GitHub stats (every N cycles), News (every cycle)
"""
import asyncio
from collections import Counter
from typing import Any

from collectors.coingecko import fetch_markets, fetch_trending
from collectors.dexscreener import fetch_latest_profiles, fetch_top_boosts
from config import (
    ALERT_CONFIDENCE_THRESHOLD,
    ALERT_MIN_MOMENTUM,
    ALERT_MIN_TOKENS,
    COINPAPRIKA_ENABLED,
    COINPAPRIKA_FETCH_INTERVAL_CYCLES,
    GITHUB_ENABLED,
    GITHUB_FETCH_INTERVAL_CYCLES,
    MAX_PRICE_CHANGE_ABS,
    MIN_LIQUIDITY_USD,
    MIN_MARKET_CAP,
    MIN_VOLUME_24H,
    NEWS_ENABLED,
)
from models.schemas import Alert, GithubSignal, NarrativeScore, TokenSnapshot
from scoring.momentum import score_snapshots
from scoring.narratives import categorize_snapshots, compute_narrative_scores
from scoring.token_quality import compute_final_scores
from storage import database as db
from utils.logger import BOLD, CYAN, DIM, GREEN, RED, RESET, YELLOW, logger, print_alert

# Global cycle counter — incremented at the start of each collection run
_cycle_count: int = 0


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

async def collect_and_analyze() -> None:
    global _cycle_count
    _cycle_count += 1
    _section(f"COLLECTION CYCLE #{_cycle_count}")

    # 1. Fetch core data sources
    snapshots = await _collect_all()
    if not snapshots:
        logger.warning("No data collected — all sources failed or returned empty.")
        return

    # 2. Deduplicate
    snapshots = _deduplicate(snapshots)
    logger.info(f"Unique tokens after dedup : {len(snapshots)}")

    # 3. Quality filter
    snapshots, filter_stats = _quality_filter(snapshots)
    if not snapshots:
        logger.warning("All tokens filtered out — thresholds may be too strict.")
        return

    # 4. Keyword categorisation
    snapshots = categorize_snapshots(snapshots)

    # 4b. Ecosystem detection — symbol-based + chain-based (fast, every cycle)
    _assign_ecosystems(snapshots)

    # 5. CoinPaprika enrichment (only every N cycles to respect rate limits)
    if COINPAPRIKA_ENABLED and _cycle_count % COINPAPRIKA_FETCH_INTERVAL_CYCLES == 0:
        try:
            from collectors.coinpaprika import enrich_snapshots
            await enrich_snapshots(snapshots)
            logger.info("[CoinPaprika] enrichment applied")
        except Exception as exc:
            logger.warning(f"[CoinPaprika] enrichment failed: {exc}")

    # 6. Load per-token history and score momentum + persistence
    history_map = _load_history(snapshots)
    snapshots = score_snapshots(snapshots, history_map)

    # 7. Narrative cluster scoring (with persistence from DB)
    narrative_history = db.get_narrative_score_history(limit_per_narrative=6)
    narrative_scores = compute_narrative_scores(snapshots, narrative_history)
    narrative_score_map = {ns.narrative: ns for ns in narrative_scores}

    # 8. Load latest GitHub signals from DB (populated separately)
    gh_rows = db.get_latest_github_signals()
    github_map: dict[str, GithubSignal] = {}
    for row in gh_rows:
        github_map[row["token_id"]] = GithubSignal(**{
            k: v for k, v in row.items()
            if k in GithubSignal.model_fields
        })

    # 9. Final narrative-aware scoring + relative strength + explanations
    snapshots = compute_final_scores(snapshots, narrative_score_map, github_map)

    # 10. Persist snapshots + narrative scores + trend history
    db.save_snapshots(snapshots)
    logger.info(f"Saved {len(snapshots)} quality snapshots.")
    for ns in narrative_scores:
        db.save_narrative_score(ns)
    db.save_narrative_trends(narrative_scores)

    # 11. Alerts
    _check_alerts(narrative_scores)

    # 12. Async extras — GitHub stats and news (fire-and-save)
    asyncio.create_task(_run_github_fetch())
    if NEWS_ENABLED:
        asyncio.create_task(_run_news_fetch())

    # 13. Summary
    _print_summary(snapshots, narrative_scores, filter_stats)
    _section_end()


# ---------------------------------------------------------------------------
# Async side-tasks
# ---------------------------------------------------------------------------

async def _run_github_fetch() -> None:
    """Fetch GitHub stats every GITHUB_FETCH_INTERVAL_CYCLES cycles."""
    if not GITHUB_ENABLED:
        return
    if _cycle_count % GITHUB_FETCH_INTERVAL_CYCLES != 0:
        return
    try:
        from collectors.github_tracker import fetch_all_github_stats
        signals = await fetch_all_github_stats()
        if signals:
            db.save_github_signals(signals)
            logger.info(f"[GitHub] saved {len(signals)} signals")
    except Exception as exc:
        logger.warning(f"[GitHub] fetch failed: {exc}")


async def _run_news_fetch() -> None:
    """Fetch news every cycle and persist new articles."""
    try:
        from collectors.news_feed import fetch_news_items
        items = await fetch_news_items()
        if items:
            db.save_news_items(items)
            # Brief narrative count summary
            counts = db.get_news_narrative_counts(hours=3)
            if counts:
                top = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:4]
                logger.info("[News] recent: " + ", ".join(f"{n}={c}" for n, c in top))
    except Exception as exc:
        logger.warning(f"[News] fetch failed: {exc}")


# ---------------------------------------------------------------------------
# Ecosystem assignment
# ---------------------------------------------------------------------------

def _assign_ecosystems(snapshots: list[TokenSnapshot]) -> None:
    """
    Set snap.ecosystem for each token.
    Uses symbol lookup first (most precise), then chain field.
    Only overrides tokens still showing 'Unknown'.
    """
    from utils.ecosystems import detect_ecosystem
    for snap in snapshots:
        if snap.ecosystem in ("Unknown", "", None):
            snap.ecosystem = detect_ecosystem(
                chain=snap.chain or "",
                symbol=snap.symbol or "",
            )


# ---------------------------------------------------------------------------
# Quality filter
# ---------------------------------------------------------------------------

def _quality_filter(
    snapshots: list[TokenSnapshot],
) -> tuple[list[TokenSnapshot], dict[str, int]]:
    kept: list[TokenSnapshot] = []
    stats: dict[str, int] = {
        "micro_cap":          0,
        "low_volume":         0,
        "thin_liquidity":     0,
        "extreme_volatility": 0,
    }
    suspicious: list[str] = []

    for snap in snapshots:
        ok, reason, category = _passes_quality(snap)
        if ok:
            kept.append(snap)
        else:
            stats[category] = stats.get(category, 0) + 1
            if category == "extreme_volatility":
                suspicious.append(f"{snap.symbol} ({snap.price_change_24h:+.0f}%)")

    total_removed = sum(stats.values())
    if total_removed:
        logger.info(
            f"{DIM}Filtered out {total_removed} tokens:{RESET}"
            f"  micro-cap={stats['micro_cap']}"
            f"  low-vol={stats['low_volume']}"
            f"  thin-liq={stats['thin_liquidity']}"
            f"  suspicious={stats['extreme_volatility']}"
        )
    if suspicious:
        logger.warning(
            f"{YELLOW}Suspicious volatility ignored:{RESET} "
            + ", ".join(suspicious[:8])
        )

    logger.info(f"Quality tokens passing filters: {len(kept)}")
    return kept, stats


def _passes_quality(snap: TokenSnapshot) -> tuple[bool, str, str]:
    if abs(snap.price_change_24h) > MAX_PRICE_CHANGE_ABS:
        return False, f"volatility {snap.price_change_24h:+.0f}%", "extreme_volatility"
    if snap.market_cap > 0 and snap.market_cap < MIN_MARKET_CAP:
        return False, f"mcap ${snap.market_cap:,.0f}", "micro_cap"
    if snap.volume_24h > 0 and snap.volume_24h < MIN_VOLUME_24H:
        return False, f"vol ${snap.volume_24h:,.0f}", "low_volume"
    if snap.liquidity > 0 and snap.liquidity < MIN_LIQUIDITY_USD:
        return False, f"liq ${snap.liquidity:,.0f}", "thin_liquidity"
    return True, "", ""


# ---------------------------------------------------------------------------
# Pipeline helpers
# ---------------------------------------------------------------------------

async def _collect_all() -> list[TokenSnapshot]:
    results = await asyncio.gather(
        fetch_trending(),
        fetch_markets(),
        fetch_top_boosts(),
        fetch_latest_profiles(),
        return_exceptions=True,
    )
    all_snaps: list[TokenSnapshot] = []
    for r in results:
        if isinstance(r, Exception):
            logger.error(f"Collector raised: {r}")
        elif isinstance(r, list):
            all_snaps.extend(r)
    return all_snaps


def _deduplicate(snapshots: list[TokenSnapshot]) -> list[TokenSnapshot]:
    priority = {
        "coingecko_markets":    0,
        "coingecko_trending":   1,
        "dexscreener_boosts":   2,
        "dexscreener_profiles": 3,
    }
    best: dict[str, TokenSnapshot] = {}
    for snap in snapshots:
        if not snap.token_id:
            continue
        existing = best.get(snap.token_id)
        if existing is None:
            best[snap.token_id] = snap
        elif priority.get(snap.source, 99) < priority.get(existing.source, 99):
            best[snap.token_id] = snap
    return list(best.values())


def _load_history(snapshots: list[TokenSnapshot]) -> dict[str, list[dict[str, Any]]]:
    return {
        snap.token_id: db.get_snapshots_for_token(snap.token_id, limit=8)
        for snap in snapshots
    }


# ---------------------------------------------------------------------------
# Alert detection
# ---------------------------------------------------------------------------

def _check_alerts(narrative_scores: list[NarrativeScore]) -> None:
    for ns in narrative_scores:
        if (
            ns.confidence >= ALERT_CONFIDENCE_THRESHOLD
            and ns.token_count >= ALERT_MIN_TOKENS
            and ns.avg_momentum_score >= ALERT_MIN_MOMENTUM
        ):
            reason = _build_reason(ns)
            alert = Alert(
                narrative=ns.narrative,
                confidence=ns.confidence,
                tokens=ns.tokens,
                reason=reason,
            )
            db.save_alert(alert)
            print_alert(ns.narrative, ns.confidence, ns.tokens, reason)


def _build_reason(ns: NarrativeScore) -> str:
    parts: list[str] = []
    if ns.token_count >= 3:
        parts.append(f"{ns.token_count} related tokens trending simultaneously")
    if ns.avg_momentum_score >= 50:
        parts.append(f"high avg momentum ({ns.avg_momentum_score:.0f}/100)")
    if ns.volume_spike >= 20:
        parts.append(f"volume spike ({ns.volume_spike:.1f}% vol/mcap)")
    if ns.persistence_score >= 50:
        parts.append(f"persistent signal ({ns.persistence_score:.0f}/100)")
    return " + ".join(parts) if parts else "Emerging narrative signal detected"


# ---------------------------------------------------------------------------
# Terminal summary
# ---------------------------------------------------------------------------

def _print_summary(
    snapshots: list[TokenSnapshot],
    narrative_scores: list[NarrativeScore],
    filter_stats: dict[str, int],
) -> None:
    high_conf = [ns for ns in narrative_scores if ns.confidence >= ALERT_CONFIDENCE_THRESHOLD]
    if high_conf:
        print(f"\n{GREEN}{BOLD}  ▸ TOP HIGH-CONFIDENCE SIGNALS{RESET}")
        for ns in high_conf[:5]:
            persist_str = f"  persist={ns.persistence_score:.0f}" if ns.persistence_score > 0 else ""
            print(
                f"  {CYAN}{BOLD}{ns.narrative:<16}{RESET}"
                f"  conf={GREEN}{ns.confidence:.0f}{RESET}"
                f"  tokens={ns.token_count}"
                f"  avg_score={ns.avg_momentum_score:.1f}"
                f"{persist_str}"
                f"  [{', '.join(ns.tokens[:4])}]"
            )
    else:
        logger.info("No high-confidence narratives this cycle.")

    if narrative_scores:
        logger.info(f"Narrative scores this cycle ({len(narrative_scores)}):")
        for ns in narrative_scores[:8]:
            logger.info(
                f"  {ns.narrative:<16}"
                f"  conf={ns.confidence:5.1f}"
                f"  tokens={ns.token_count}"
                f"  avg_score={ns.avg_momentum_score:.1f}"
                f"  persist={ns.persistence_score:.0f}"
            )

    top_tokens = [s for s in snapshots if s.final_score >= 35][:8]
    if top_tokens:
        logger.info("Top quality tokens by final_score:")
        for t in top_tokens:
            liq_str = f"  liq=${t.liquidity/1e6:.1f}M" if t.liquidity > 0 else ""
            logger.info(
                f"  {t.symbol:<10}"
                f"  final={t.final_score:5.1f}"
                f"  mom={t.momentum_score:4.1f}"
                f"  persist={t.persistence_score:4.0f}"
                f"  rs={t.relative_strength:4.0f}"
                f"  24h={t.price_change_24h:+.1f}%"
                f"  [{t.narrative or 'Other'}]"
                f"{liq_str}"
            )


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _section(title: str) -> None:
    print(f"\n{DIM}{'─' * 8}{RESET} {BOLD}{title}{RESET} {DIM}{'─' * (46 - len(title))}{RESET}")


def _section_end() -> None:
    print(f"{DIM}{'─' * 56}{RESET}\n")

import asyncio
from collections import Counter
from typing import Any

from collectors.coingecko import fetch_markets, fetch_trending
from collectors.dexscreener import fetch_latest_profiles, fetch_top_boosts
from config import (
    ALERT_CONFIDENCE_THRESHOLD,
    ALERT_MIN_MOMENTUM,
    ALERT_MIN_TOKENS,
    MAX_PRICE_CHANGE_ABS,
    MIN_LIQUIDITY_USD,
    MIN_MARKET_CAP,
    MIN_VOLUME_24H,
)
from models.schemas import Alert, NarrativeScore, TokenSnapshot
from scoring.momentum import score_snapshots
from scoring.narratives import categorize_snapshots, compute_narrative_scores
from storage import database as db
from utils.logger import BOLD, CYAN, DIM, GREEN, RED, RESET, YELLOW, logger, print_alert


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

async def collect_and_analyze() -> None:
    _section("COLLECTION CYCLE")

    # 1. Fetch
    snapshots = await _collect_all()
    if not snapshots:
        logger.warning("No data collected — all sources failed or returned empty.")
        return

    # 2. Deduplicate
    snapshots = _deduplicate(snapshots)
    logger.info(f"Unique tokens after dedup : {len(snapshots)}")

    # 3. Quality filter (logs rejected tokens)
    snapshots, filter_stats = _quality_filter(snapshots)

    if not snapshots:
        logger.warning("All tokens filtered out — thresholds may be too strict.")
        return

    # 4. Categorise
    snapshots = categorize_snapshots(snapshots)

    # 5. Load history + score
    history_map = _load_history(snapshots)
    snapshots = score_snapshots(snapshots, history_map)

    # 6. Persist snapshots
    db.save_snapshots(snapshots)
    logger.info(f"Saved {len(snapshots)} quality snapshots.")

    # 7. Narrative scoring (with persistence from DB)
    narrative_history = db.get_narrative_score_history(limit_per_narrative=6)
    narrative_scores = compute_narrative_scores(snapshots, narrative_history)
    for ns in narrative_scores:
        db.save_narrative_score(ns)

    # 8. Alerts
    _check_alerts(narrative_scores)

    # 9. Summary to terminal
    _print_summary(snapshots, narrative_scores, filter_stats)
    _section_end()


# ---------------------------------------------------------------------------
# Quality filter
# ---------------------------------------------------------------------------

def _quality_filter(
    snapshots: list[TokenSnapshot],
) -> tuple[list[TokenSnapshot], dict[str, int]]:
    """
    Discard tokens that don't meet quality thresholds.
    Returns (kept_snapshots, stats_dict).
    """
    kept: list[TokenSnapshot] = []
    stats: dict[str, int] = {
        "micro_cap":          0,
        "low_volume":         0,
        "thin_liquidity":     0,
        "extreme_volatility": 0,
    }
    suspicious: list[str] = []

    for snap in snapshots:
        ok, reason, category = _passes_quality(snap)
        if ok:
            kept.append(snap)
        else:
            stats[category] = stats.get(category, 0) + 1
            if category == "extreme_volatility":
                suspicious.append(f"{snap.symbol} ({snap.price_change_24h:+.0f}%)")

    # Log filter summary
    total_removed = sum(stats.values())
    if total_removed:
        logger.info(
            f"{DIM}Filtered out {total_removed} tokens:{RESET}"
            f"  micro-cap={stats['micro_cap']}"
            f"  low-vol={stats['low_volume']}"
            f"  thin-liq={stats['thin_liquidity']}"
            f"  suspicious={stats['extreme_volatility']}"
        )
    if suspicious:
        logger.warning(
            f"{YELLOW}Suspicious volatility ignored:{RESET} "
            + ", ".join(suspicious[:8])
        )

    logger.info(f"Quality tokens passing filters: {len(kept)}")
    return kept, stats


def _passes_quality(snap: TokenSnapshot) -> tuple[bool, str, str]:
    """Returns (passes, reason_str, category_str)."""
    # Extreme volatility: likely pump/rug — reject regardless of other metrics
    if abs(snap.price_change_24h) > MAX_PRICE_CHANGE_ABS:
        return False, f"volatility {snap.price_change_24h:+.0f}%", "extreme_volatility"

    # Market cap filter (skip if we simply don't have data)
    if snap.market_cap > 0 and snap.market_cap < MIN_MARKET_CAP:
        return False, f"mcap ${snap.market_cap:,.0f}", "micro_cap"

    # Volume filter
    if snap.volume_24h > 0 and snap.volume_24h < MIN_VOLUME_24H:
        return False, f"vol ${snap.volume_24h:,.0f}", "low_volume"

    # Liquidity filter (DEX tokens only — CoinGecko has liquidity=0)
    if snap.liquidity > 0 and snap.liquidity < MIN_LIQUIDITY_USD:
        return False, f"liq ${snap.liquidity:,.0f}", "thin_liquidity"

    return True, "", ""


# ---------------------------------------------------------------------------
# Pipeline helpers
# ---------------------------------------------------------------------------

async def _collect_all() -> list[TokenSnapshot]:
    results = await asyncio.gather(
        fetch_trending(),
        fetch_markets(),
        fetch_top_boosts(),
        fetch_latest_profiles(),
        return_exceptions=True,
    )
    all_snaps: list[TokenSnapshot] = []
    for r in results:
        if isinstance(r, Exception):
            logger.error(f"Collector raised: {r}")
        elif isinstance(r, list):
            all_snaps.extend(r)
    return all_snaps


def _deduplicate(snapshots: list[TokenSnapshot]) -> list[TokenSnapshot]:
    """Keep one snapshot per token_id, prioritising CoinGecko market data."""
    priority = {
        "coingecko_markets":   0,
        "coingecko_trending":  1,
        "dexscreener_boosts":  2,
        "dexscreener_profiles": 3,
    }
    best: dict[str, TokenSnapshot] = {}
    for snap in snapshots:
        if not snap.token_id:
            continue
        existing = best.get(snap.token_id)
        if existing is None:
            best[snap.token_id] = snap
        elif priority.get(snap.source, 99) < priority.get(existing.source, 99):
            best[snap.token_id] = snap
    return list(best.values())


def _load_history(snapshots: list[TokenSnapshot]) -> dict[str, list[dict[str, Any]]]:
    return {
        snap.token_id: db.get_snapshots_for_token(snap.token_id, limit=8)
        for snap in snapshots
    }


# ---------------------------------------------------------------------------
# Alert detection
# ---------------------------------------------------------------------------

def _check_alerts(narrative_scores: list[NarrativeScore]) -> None:
    for ns in narrative_scores:
        if (
            ns.confidence >= ALERT_CONFIDENCE_THRESHOLD
            and ns.token_count >= ALERT_MIN_TOKENS
            and ns.avg_momentum_score >= ALERT_MIN_MOMENTUM
        ):
            reason = _build_reason(ns)
            alert = Alert(
                narrative=ns.narrative,
                confidence=ns.confidence,
                tokens=ns.tokens,
                reason=reason,
            )
            db.save_alert(alert)
            print_alert(ns.narrative, ns.confidence, ns.tokens, reason)


def _build_reason(ns: NarrativeScore) -> str:
    parts: list[str] = []
    if ns.token_count >= 3:
        parts.append(f"{ns.token_count} related tokens trending simultaneously")
    if ns.avg_momentum_score >= 50:
        parts.append(f"high avg momentum ({ns.avg_momentum_score:.0f}/100)")
    if ns.volume_spike >= 20:
        parts.append(f"volume spike ({ns.volume_spike:.1f}% vol/mcap)")
    if ns.persistence_score >= 50:
        parts.append(f"persistent signal ({ns.persistence_score:.0f}/100)")
    return " + ".join(parts) if parts else "Emerging narrative signal detected"


# ---------------------------------------------------------------------------
# Terminal summary
# ---------------------------------------------------------------------------

def _print_summary(
    snapshots: list[TokenSnapshot],
    narrative_scores: list[NarrativeScore],
    filter_stats: dict[str, int],
) -> None:
    # ── Top High-Confidence Signals ──────────────────────────────────────
    high_conf = [ns for ns in narrative_scores if ns.confidence >= ALERT_CONFIDENCE_THRESHOLD]
    if high_conf:
        print(f"\n{GREEN}{BOLD}  ▸ TOP HIGH-CONFIDENCE SIGNALS{RESET}")
        for ns in high_conf[:5]:
            persist_str = f"  persist={ns.persistence_score:.0f}" if ns.persistence_score > 0 else ""
            print(
                f"  {CYAN}{BOLD}{ns.narrative:<16}{RESET}"
                f"  conf={GREEN}{ns.confidence:.0f}{RESET}"
                f"  tokens={ns.token_count}"
                f"  avg_score={ns.avg_momentum_score:.1f}"
                f"{persist_str}"
                f"  [{', '.join(ns.tokens[:4])}]"
            )
    else:
        logger.info("No high-confidence narratives this cycle.")

    # ── All narrative scores ─────────────────────────────────────────────
    if narrative_scores:
        logger.info(f"Narrative scores this cycle ({len(narrative_scores)}):")
        for ns in narrative_scores[:8]:
            logger.info(
                f"  {ns.narrative:<16}"
                f"  conf={ns.confidence:5.1f}"
                f"  tokens={ns.token_count}"
                f"  avg_score={ns.avg_momentum_score:.1f}"
                f"  persist={ns.persistence_score:.0f}"
            )

    # ── Top tokens ───────────────────────────────────────────────────────
    top_tokens = [s for s in snapshots if s.momentum_score >= 40][:8]
    if top_tokens:
        logger.info("Top quality tokens by momentum:")
        for t in top_tokens:
            liq_str = f"  liq=${t.liquidity/1e6:.1f}M" if t.liquidity > 0 else ""
            logger.info(
                f"  {t.symbol:<10}"
                f"  score={t.momentum_score:5.1f}"
                f"  persist={t.persistence_score:4.0f}"
                f"  24h={t.price_change_24h:+.1f}%"
                f"  narrative={t.narrative or 'Other'}"
                f"{liq_str}"
            )


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _section(title: str) -> None:
    print(f"\n{DIM}{'─' * 8}{RESET} {BOLD}{title}{RESET} {DIM}{'─' * (46 - len(title))}{RESET}")


def _section_end() -> None:
    print(f"{DIM}{'─' * 56}{RESET}\n")

