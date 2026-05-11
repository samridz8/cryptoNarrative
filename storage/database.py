"""
SQLite persistence layer.
All functions use a fresh connection per call — fine for a 15-min cycle tool.
"""
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from models.schemas import Alert, GithubSignal, NewsItem, NarrativeScore, TokenSnapshot

DB_PATH = Path("data/narrative_radar.db")


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(str(DB_PATH), detect_types=sqlite3.PARSE_DECLTYPES)
    con.row_factory = sqlite3.Row
    return con


# ---------------------------------------------------------------------------
# Schema initialisation
# ---------------------------------------------------------------------------

def init_db() -> None:
    with _conn() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS token_snapshots (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp         DATETIME DEFAULT CURRENT_TIMESTAMP,
                symbol            TEXT    NOT NULL,
                name              TEXT,
                token_id          TEXT    NOT NULL,
                price_usd         REAL    DEFAULT 0,
                price_change_24h  REAL    DEFAULT 0,
                price_change_1h   REAL    DEFAULT 0,
                market_cap        REAL    DEFAULT 0,
                volume_24h        REAL    DEFAULT 0,
                liquidity         REAL    DEFAULT 0,
                chain             TEXT    DEFAULT 'unknown',
                source            TEXT    DEFAULT 'unknown',
                narrative         TEXT,
                ecosystem         TEXT    DEFAULT 'Unknown',
                momentum_score    REAL    DEFAULT 0,
                persistence_score REAL    DEFAULT 0,
                relative_strength REAL    DEFAULT 0,
                final_score       REAL    DEFAULT 0,
                signal_explanation TEXT   DEFAULT '[]'
            );

            CREATE TABLE IF NOT EXISTS narrative_scores (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp           DATETIME DEFAULT CURRENT_TIMESTAMP,
                narrative           TEXT    NOT NULL,
                token_count         INTEGER DEFAULT 0,
                avg_momentum_score  REAL    DEFAULT 0,
                volume_spike        REAL    DEFAULT 0,
                confidence          REAL    DEFAULT 0,
                persistence_score   REAL    DEFAULT 0,
                tokens              TEXT    DEFAULT '[]'
            );

            CREATE TABLE IF NOT EXISTS alerts (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp  DATETIME DEFAULT CURRENT_TIMESTAMP,
                narrative  TEXT    NOT NULL,
                confidence REAL    DEFAULT 0,
                tokens     TEXT    DEFAULT '[]',
                reason     TEXT
            );

            CREATE TABLE IF NOT EXISTS github_signals (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp        DATETIME DEFAULT CURRENT_TIMESTAMP,
                token_id         TEXT    NOT NULL,
                repo             TEXT    NOT NULL,
                stars            INTEGER DEFAULT 0,
                forks            INTEGER DEFAULT 0,
                days_since_push  INTEGER DEFAULT 999,
                activity_score   REAL    DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS news_items (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp  DATETIME DEFAULT CURRENT_TIMESTAMP,
                title      TEXT    NOT NULL,
                link       TEXT,
                pub_date   TEXT,
                narrative  TEXT,
                title_hash TEXT    UNIQUE
            );

            CREATE INDEX IF NOT EXISTS idx_snap_ts      ON token_snapshots(timestamp);
            CREATE INDEX IF NOT EXISTS idx_snap_token   ON token_snapshots(token_id);
            CREATE INDEX IF NOT EXISTS idx_narr_ts      ON narrative_scores(timestamp);
            CREATE INDEX IF NOT EXISTS idx_narr_name    ON narrative_scores(narrative);
            CREATE INDEX IF NOT EXISTS idx_gh_token     ON github_signals(token_id);
            CREATE INDEX IF NOT EXISTS idx_news_ts      ON news_items(timestamp);

            CREATE TABLE IF NOT EXISTS narrative_trend_history (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp      DATETIME DEFAULT CURRENT_TIMESTAMP,
                narrative      TEXT    NOT NULL,
                confidence     REAL    DEFAULT 0,
                persistence    REAL    DEFAULT 0,
                token_count    INTEGER DEFAULT 0,
                avg_momentum   REAL    DEFAULT 0,
                leader_symbols TEXT    DEFAULT '[]'
            );
            CREATE INDEX IF NOT EXISTS idx_trend_ts   ON narrative_trend_history(timestamp);
            CREATE INDEX IF NOT EXISTS idx_trend_name ON narrative_trend_history(narrative);
        """)

    # Safe migrations for previously created databases
    _safe_add_column("token_snapshots", "persistence_score", "REAL DEFAULT 0")
    _safe_add_column("token_snapshots", "relative_strength", "REAL DEFAULT 0")
    _safe_add_column("token_snapshots", "final_score",       "REAL DEFAULT 0")
    _safe_add_column("token_snapshots", "signal_explanation","TEXT DEFAULT '[]'")
    _safe_add_column("token_snapshots", "ecosystem",         "TEXT DEFAULT 'Unknown'")
    _safe_add_column("narrative_scores","persistence_score", "REAL DEFAULT 0")


def _safe_add_column(table: str, column: str, definition: str) -> None:
    with _conn() as con:
        try:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        except sqlite3.OperationalError:
            pass  # column already exists


# ---------------------------------------------------------------------------
# Token snapshots
# ---------------------------------------------------------------------------

def save_snapshots(snapshots: list[TokenSnapshot]) -> None:
    with _conn() as con:
        con.executemany(
            """
            INSERT INTO token_snapshots
                (symbol, name, token_id, price_usd, price_change_24h, price_change_1h,
                 market_cap, volume_24h, liquidity, chain, source, narrative, ecosystem,
                 momentum_score, persistence_score, relative_strength,
                 final_score, signal_explanation)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    s.symbol, s.name, s.token_id,
                    s.price_usd, s.price_change_24h, s.price_change_1h,
                    s.market_cap, s.volume_24h, s.liquidity,
                    s.chain, s.source, s.narrative, s.ecosystem,
                    s.momentum_score, s.persistence_score, s.relative_strength,
                    s.final_score, json.dumps(s.signal_explanation),
                )
                for s in snapshots
            ],
        )


def get_snapshots_for_token(token_id: str, limit: int = 10) -> list[dict[str, Any]]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM token_snapshots WHERE token_id=? ORDER BY timestamp DESC LIMIT ?",
            (token_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_latest_snapshots() -> list[dict[str, Any]]:
    """Most-recent snapshot per token, sorted by final_score desc."""
    with _conn() as con:
        rows = con.execute(
            """
            SELECT t.*
            FROM token_snapshots t
            INNER JOIN (
                SELECT token_id, MAX(id) AS max_id
                FROM token_snapshots GROUP BY token_id
            ) latest ON t.id = latest.max_id
            ORDER BY t.final_score DESC
            """
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["signal_explanation"] = json.loads(d.get("signal_explanation") or "[]")
        result.append(d)
    return result


def get_narrative_leaderboard(narrative: str, limit: int = 20) -> list[dict[str, Any]]:
    """Top tokens for a specific narrative by final_score."""
    with _conn() as con:
        rows = con.execute(
            """
            SELECT t.*
            FROM token_snapshots t
            INNER JOIN (
                SELECT token_id, MAX(id) AS max_id
                FROM token_snapshots WHERE narrative=?
                GROUP BY token_id
            ) latest ON t.id = latest.max_id
            ORDER BY t.final_score DESC
            LIMIT ?
            """,
            (narrative, limit),
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["signal_explanation"] = json.loads(d.get("signal_explanation") or "[]")
        result.append(d)
    return result


def get_suspicious_tokens(limit: int = 20) -> list[dict[str, Any]]:
    """Tokens with high volatility but low persistence or final score."""
    with _conn() as con:
        rows = con.execute(
            """
            SELECT t.*
            FROM token_snapshots t
            INNER JOIN (
                SELECT token_id, MAX(id) AS max_id
                FROM token_snapshots GROUP BY token_id
            ) latest ON t.id = latest.max_id
            WHERE ABS(t.price_change_24h) > 40
              AND (t.persistence_score < 20 OR t.final_score < 30)
            ORDER BY ABS(t.price_change_24h) DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["signal_explanation"] = json.loads(d.get("signal_explanation") or "[]")
        result.append(d)
    return result


# ---------------------------------------------------------------------------
# Narrative scores
# ---------------------------------------------------------------------------

def save_narrative_score(score: NarrativeScore) -> None:
    with _conn() as con:
        con.execute(
            """
            INSERT INTO narrative_scores
                (narrative, token_count, avg_momentum_score, volume_spike,
                 confidence, persistence_score, tokens)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                score.narrative, score.token_count, score.avg_momentum_score,
                score.volume_spike, score.confidence, score.persistence_score,
                json.dumps(score.tokens),
            ),
        )


def get_top_narratives(limit: int = 15) -> list[dict[str, Any]]:
    with _conn() as con:
        rows = con.execute(
            """
            SELECT n.*
            FROM narrative_scores n
            INNER JOIN (
                SELECT narrative, MAX(id) AS max_id
                FROM narrative_scores GROUP BY narrative
            ) latest ON n.id = latest.max_id
            ORDER BY n.confidence DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["tokens"] = json.loads(d["tokens"])
        result.append(d)
    return result


def get_narrative_score_history(limit_per_narrative: int = 6) -> dict[str, list[dict[str, Any]]]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM narrative_scores ORDER BY narrative, timestamp DESC"
        ).fetchall()
    history: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        d = dict(r)
        d["tokens"] = json.loads(d.get("tokens") or "[]")
        key = d["narrative"]
        if key not in history:
            history[key] = []
        if len(history[key]) < limit_per_narrative:
            history[key].append(d)
    return history


def get_narrative_confidence_history(narrative: str, limit: int = 10) -> list[dict[str, Any]]:
    """Recent confidence trend for a single narrative."""
    with _conn() as con:
        rows = con.execute(
            """
            SELECT timestamp, confidence, token_count, avg_momentum_score
            FROM narrative_scores WHERE narrative=?
            ORDER BY timestamp DESC LIMIT ?
            """,
            (narrative, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Narrative trend history  (time-series snapshots for trend charting)
# ---------------------------------------------------------------------------

def save_narrative_trends(scores: list[NarrativeScore]) -> None:
    """Persist one trend-history row per narrative per cycle."""
    with _conn() as con:
        con.executemany(
            """
            INSERT INTO narrative_trend_history
                (narrative, confidence, persistence, token_count, avg_momentum, leader_symbols)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    s.narrative,
                    s.confidence,
                    s.persistence_score,
                    s.token_count,
                    s.avg_momentum_score,
                    json.dumps(s.tokens[:4]),
                )
                for s in scores
            ],
        )


def get_narrative_trend(narrative: str, limit: int = 12) -> list[dict[str, Any]]:
    """Return the most-recent N trend rows for a specific narrative."""
    with _conn() as con:
        rows = con.execute(
            """
            SELECT timestamp, confidence, persistence, token_count, avg_momentum, leader_symbols
            FROM narrative_trend_history WHERE narrative=?
            ORDER BY timestamp DESC LIMIT ?
            """,
            (narrative, limit),
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["leader_symbols"] = json.loads(d.get("leader_symbols") or "[]")
        result.append(d)
    return result


def get_all_narrative_trends(limit_per: int = 6) -> dict[str, list[dict[str, Any]]]:
    """Latest N trend points for every narrative (for dashboard overview)."""
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM narrative_trend_history ORDER BY narrative, timestamp DESC"
        ).fetchall()
    trends: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        d = dict(r)
        d["leader_symbols"] = json.loads(d.get("leader_symbols") or "[]")
        key = d["narrative"]
        if key not in trends:
            trends[key] = []
        if len(trends[key]) < limit_per:
            trends[key].append(d)
    return trends


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

def save_alert(alert: Alert) -> None:
    with _conn() as con:
        con.execute(
            "INSERT INTO alerts (narrative, confidence, tokens, reason) VALUES (?,?,?,?)",
            (alert.narrative, alert.confidence, json.dumps(alert.tokens), alert.reason),
        )


def get_recent_alerts(limit: int = 20) -> list[dict[str, Any]]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM alerts ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["tokens"] = json.loads(d["tokens"])
        result.append(d)
    return result


# ---------------------------------------------------------------------------
# GitHub signals
# ---------------------------------------------------------------------------

def save_github_signals(signals: list[GithubSignal]) -> None:
    with _conn() as con:
        con.executemany(
            """
            INSERT INTO github_signals
                (token_id, repo, stars, forks, days_since_push, activity_score)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (s.token_id, s.repo, s.stars, s.forks, s.days_since_push, s.activity_score)
                for s in signals
            ],
        )


def get_latest_github_signals() -> list[dict[str, Any]]:
    with _conn() as con:
        rows = con.execute(
            """
            SELECT g.*
            FROM github_signals g
            INNER JOIN (
                SELECT token_id, MAX(id) AS max_id
                FROM github_signals GROUP BY token_id
            ) latest ON g.id = latest.max_id
            ORDER BY g.activity_score DESC
            """
        ).fetchall()
    return [dict(r) for r in rows]


def get_github_signal_for_token(token_id: str) -> dict[str, Any] | None:
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM github_signals WHERE token_id=? ORDER BY id DESC LIMIT 1",
            (token_id,),
        ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# News items
# ---------------------------------------------------------------------------

def save_news_items(items: list[NewsItem]) -> None:
    with _conn() as con:
        for item in items:
            try:
                con.execute(
                    """
                    INSERT INTO news_items (title, link, pub_date, narrative, title_hash)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (item.title, item.link, item.pub_date, item.narrative, item.title_hash),
                )
            except sqlite3.IntegrityError:
                pass  # duplicate title_hash — skip


def get_recent_news(limit: int = 30) -> list[dict[str, Any]]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM news_items ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_news_narrative_counts(hours: int = 6) -> dict[str, int]:
    """Count news items per narrative in the last N hours."""
    with _conn() as con:
        rows = con.execute(
            """
            SELECT narrative, COUNT(*) as cnt
            FROM news_items
            WHERE timestamp >= datetime('now', ?)
              AND narrative != ''
            GROUP BY narrative
            """,
            (f"-{hours} hours",),
        ).fetchall()
    return {r["narrative"]: r["cnt"] for r in rows}


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def get_summary() -> dict[str, Any]:
    with _conn() as con:
        total_tokens   = con.execute("SELECT COUNT(DISTINCT token_id) FROM token_snapshots").fetchone()[0]
        total_snapshots= con.execute("SELECT COUNT(*) FROM token_snapshots").fetchone()[0]
        last_update    = con.execute("SELECT MAX(timestamp) FROM token_snapshots").fetchone()[0]
        total_alerts   = con.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        total_news     = con.execute("SELECT COUNT(*) FROM news_items").fetchone()[0]
        github_tracked = con.execute("SELECT COUNT(DISTINCT token_id) FROM github_signals").fetchone()[0]
    return {
        "total_tokens":   total_tokens,
        "total_snapshots":total_snapshots,
        "last_update":    last_update,
        "total_alerts":   total_alerts,
        "total_news":     total_news,
        "github_tracked": github_tracked,
    }


