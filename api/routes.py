from fastapi import APIRouter

import storage.database as db

router = APIRouter(prefix="/api")


@router.get("/narratives")
def get_narratives():
    return db.get_top_narratives(limit=15)


@router.get("/tokens")
def get_tokens():
    return db.get_latest_snapshots()[:50]


@router.get("/alerts")
def get_alerts():
    return db.get_recent_alerts(limit=20)


@router.get("/summary")
def get_summary():
    return db.get_summary()


@router.get("/leaderboard/{narrative}")
def get_leaderboard(narrative: str, limit: int = 20):
    """Top tokens for a specific narrative, ranked by final_score."""
    return db.get_narrative_leaderboard(narrative=narrative, limit=limit)


@router.get("/suspicious")
def get_suspicious():
    """Tokens with high volatility but low persistence — likely noise/manipulation."""
    return db.get_suspicious_tokens(limit=25)


@router.get("/news")
def get_news(limit: int = 30):
    """Recent crypto news articles mapped to narratives."""
    return db.get_recent_news(limit=limit)


@router.get("/news/counts")
def get_news_counts(hours: int = 6):
    """Article counts per narrative in the last N hours (narrative acceleration proxy)."""
    return db.get_news_narrative_counts(hours=hours)


@router.get("/github")
def get_github():
    """Latest GitHub activity signals for tracked protocol repos."""
    return db.get_latest_github_signals()


@router.get("/narrative-trend/{narrative}")
def get_narrative_trend(narrative: str, limit: int = 12):
    """Historical confidence/persistence trend for a single narrative (for charting)."""
    return db.get_narrative_trend(narrative=narrative, limit=limit)


@router.get("/narrative-trends")
def get_all_narrative_trends():
    """Latest trend snapshots for all narratives — confidence/persistence over time."""
    return db.get_all_narrative_trends(limit_per=6)
