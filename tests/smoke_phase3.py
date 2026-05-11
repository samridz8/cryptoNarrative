"""Phase 3 smoke tests — run from project root."""
import os
import sys
import traceback

# Ensure project root is on path regardless of working directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.makedirs("data", exist_ok=True)

ok = 0
fail = 0


def chk(name, fn):
    global ok, fail
    try:
        fn()
        print(f"  PASS  {name}")
        ok += 1
    except Exception as e:
        print(f"  FAIL  {name}: {e}")
        traceback.print_exc()
        fail += 1


# 1. config
def t_config():
    import config as c
    assert c.PERSISTENCE_MOMENTUM_THRESHOLD == 35.0, c.PERSISTENCE_MOMENTUM_THRESHOLD
    assert c.PERSISTENCE_MIN_HISTORY == 1
    assert hasattr(c, "GITHUB_ENABLED")
    assert hasattr(c, "NEWS_ENABLED")
    assert hasattr(c, "COINPAPRIKA_ENABLED")


chk("config: Phase 3 keys", t_config)


# 2. schemas
def t_schemas():
    from models.schemas import GithubSignal, NewsItem, NarrativeScore, TokenSnapshot

    ts = TokenSnapshot(symbol="X", name="x", token_id="x-1")
    assert ts.final_score == 0.0
    assert ts.relative_strength == 0.0
    assert ts.signal_explanation == []
    assert ts.ecosystem == "Unknown"
    GithubSignal(token_id="bittensor", repo="opentensor/bittensor")
    NewsItem(title="test", title_hash="abc123")


chk("schemas: new fields + GithubSignal + NewsItem", t_schemas)


# 3. ecosystems
def t_eco():
    from utils.ecosystems import detect_ecosystem

    assert detect_ecosystem("solana") == "Solana"
    assert detect_ecosystem("base") == "Base"
    assert detect_ecosystem("ethereum") == "Ethereum"
    assert detect_ecosystem("arbitrum") == "Arbitrum"
    assert detect_ecosystem("unknown") == "Unknown"
    assert detect_ecosystem("", tags=["solana-ecosystem"]) == "Solana"


chk("utils.ecosystems: chain + tag detection", t_eco)


# 4. database
def t_db():
    import storage.database as db

    db.init_db()
    s = db.get_summary()
    assert "total_tokens" in s
    assert "total_news" in s
    assert "github_tracked" in s


chk("storage.database: init + summary", t_db)


# 5. momentum persistence (exponential decay)
def t_persist():
    from scoring.momentum import _compute_persistence

    # All cold -> 0
    cold = [{"momentum_score": 10}] * 4
    assert _compute_persistence(cold) == 0.0, _compute_persistence(cold)
    # All hot
    hot = [{"momentum_score": 80}] * 8
    score = _compute_persistence(hot)
    assert score > 80, f"Expected > 80, got {score}"
    # Most recent cold -> cap at 50
    mixed = [{"momentum_score": 10}, {"momentum_score": 80}, {"momentum_score": 80}, {"momentum_score": 80}]
    capped = _compute_persistence(mixed)
    assert capped <= 50, f"Expected <= 50, got {capped}"


chk("scoring.momentum: exponential persistence + cap", t_persist)


# 6. token quality scorer
def t_quality():
    from models.schemas import NarrativeScore, TokenSnapshot
    from scoring.token_quality import compute_final_scores

    snaps = [
        TokenSnapshot(
            symbol="TAO", name="Bittensor", token_id="bittensor",
            momentum_score=65, persistence_score=70,
            price_change_24h=12, volume_24h=5e6, market_cap=1e9,
            liquidity=8e7, narrative="AI Agents",
        ),
        TokenSnapshot(
            symbol="WIF", name="dogwifhat", token_id="wif",
            momentum_score=40, persistence_score=10,
            price_change_24h=80, volume_24h=3e7, market_cap=5e8,
            liquidity=2e6, narrative="Meme",
        ),
    ]
    ns_map = {
        "AI Agents": NarrativeScore(
            narrative="AI Agents", token_count=5,
            avg_momentum_score=55, volume_spike=8, confidence=72,
        ),
        "Meme": NarrativeScore(
            narrative="Meme", token_count=3,
            avg_momentum_score=35, volume_spike=30, confidence=40,
        ),
    }
    result = compute_final_scores(snaps, ns_map)
    tao = next(r for r in result if r.symbol == "TAO")
    wif = next(r for r in result if r.symbol == "WIF")
    assert tao.final_score > wif.final_score, f"TAO {tao.final_score} vs WIF {wif.final_score}"
    assert wif.final_score < wif.momentum_score, "Meme penalty not applied"
    assert len(tao.signal_explanation) > 0, "No explanation for TAO"
    assert tao.relative_strength >= 0


chk("scoring.token_quality: final_score + relative_strength + explanations", t_quality)


# 7. narratives ecosystem coherence
def t_narr_eco():
    from models.schemas import TokenSnapshot
    from scoring.narratives import compute_narrative_scores

    snaps = [
        TokenSnapshot(
            symbol="ARB", name="Arbitrum", token_id="arbitrum",
            momentum_score=55, persistence_score=40,
            price_change_24h=8, volume_24h=2e7, market_cap=2e9,
            liquidity=5e7, narrative="Layer2", chain="arbitrum",
        ),
        TokenSnapshot(
            symbol="OP", name="Optimism", token_id="optimism",
            momentum_score=50, persistence_score=35,
            price_change_24h=6, volume_24h=1.5e7, market_cap=1.5e9,
            liquidity=4e7, narrative="Layer2", chain="optimism",
        ),
        TokenSnapshot(
            symbol="MATIC", name="Polygon", token_id="polygon",
            momentum_score=45, persistence_score=30,
            price_change_24h=5, volume_24h=1e7, market_cap=8e8,
            liquidity=3e7, narrative="Layer2", chain="polygon",
        ),
    ]
    scores = compute_narrative_scores(snaps)
    assert len(scores) >= 1
    l2 = next((s for s in scores if s.narrative == "Layer2"), None)
    assert l2 is not None, "Layer2 not found"


chk("scoring.narratives: ecosystem coherence", t_narr_eco)


# 8. news feed parsing (offline, no HTTP)
def t_news_parse():
    from collectors.news_feed import _map_headline_to_narrative, _parse_feed_xml, _title_hash

    xml = (
        '<?xml version="1.0"?>'
        "<rss><channel>"
        "<item><title>Bittensor AI Agents launch new subnet</title>"
        "<link>https://ct.co/1</link>"
        "<pubDate>Mon, 01 Jan 2024 00:00:00 +0000</pubDate></item>"
        "<item><title>Arbitrum L2 TVL hits record high</title>"
        "<link>https://ct.co/2</link></item>"
        "</channel></rss>"
    )
    items = _parse_feed_xml(xml, "test")
    assert len(items) == 2, f"Expected 2 items, got {len(items)}"
    assert items[0].narrative == "AI Agents", items[0].narrative
    assert items[1].narrative == "Layer2", items[1].narrative
    assert len(_title_hash("test")) == 16


chk("collectors.news_feed: XML parsing + narrative mapping", t_news_parse)


# 9. github tracker (offline)
def t_gh_map():
    from collectors.github_tracker import GITHUB_REPO_MAP, _activity_score, _days_since

    assert "bittensor" in GITHUB_REPO_MAP
    assert "arbitrum" in GITHUB_REPO_MAP
    fresh = _activity_score(5000, 2)
    stale = _activity_score(5000, 60)
    assert fresh > stale, f"fresh {fresh} not > stale {stale}"
    days = _days_since("2020-01-01T00:00:00Z")
    assert days > 365, f"Expected >365 days, got {days}"


chk("collectors.github_tracker: score formula", t_gh_map)


# 10. coinpaprika tag mapping
def t_paprika():
    from collectors.coinpaprika import TAG_TO_NARRATIVE, _tags_to_narrative

    assert TAG_TO_NARRATIVE["artificial-intelligence"] == "AI Agents"
    assert _tags_to_narrative(["memes", "artificial-intelligence"]) == "Meme"
    assert _tags_to_narrative(["layer-2"]) == "Layer2"
    assert _tags_to_narrative(["unknown-tag"]) is None


chk("collectors.coinpaprika: tag mapping", t_paprika)


# 11. routes
def t_routes():
    import api.routes

    paths = [r.path for r in api.routes.router.routes]
    assert "/api/leaderboard/{narrative}" in paths, paths
    assert "/api/news" in paths
    assert "/api/github" in paths
    assert "/api/suspicious" in paths


chk("api.routes: new endpoints registered", t_routes)


# ── Results ──────────────────────────────────────────────────────────────────
print()
print(f"Results: {ok} passed, {fail} failed")
if fail:
    sys.exit(1)
else:
    print("ALL CHECKS PASSED")
