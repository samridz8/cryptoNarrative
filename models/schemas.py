from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class TokenSnapshot(BaseModel):
    symbol: str
    name: str
    token_id: str
    price_usd: float = 0.0
    price_change_24h: float = 0.0
    price_change_1h: float = 0.0
    market_cap: float = 0.0
    volume_24h: float = 0.0
    liquidity: float = 0.0
    chain: str = "unknown"
    source: str = "unknown"
    narrative: Optional[str] = None
    ecosystem: str = "Unknown"
    # Raw momentum (0-100) — multi-signal formula
    momentum_score: float = 0.0
    # Persistence of signal across collection cycles (0-100)
    persistence_score: float = 0.0
    # Relative strength within narrative peer group (0-100)
    relative_strength: float = 0.0
    # Final composite score — narrative-aware, used for rankings (0-100)
    final_score: float = 0.0
    # Deterministic rule-based explanation bullets
    signal_explanation: list[str] = Field(default_factory=list)
    timestamp: Optional[datetime] = None


class NarrativeScore(BaseModel):
    narrative: str
    token_count: int
    avg_momentum_score: float
    volume_spike: float
    confidence: float
    persistence_score: float = 0.0
    tokens: list[str] = Field(default_factory=list)
    timestamp: Optional[datetime] = None


class Alert(BaseModel):
    narrative: str
    confidence: float
    tokens: list[str] = Field(default_factory=list)
    reason: str
    timestamp: Optional[datetime] = None


class GithubSignal(BaseModel):
    token_id: str
    repo: str
    stars: int = 0
    forks: int = 0
    days_since_push: int = 999
    activity_score: float = 0.0
    timestamp: Optional[datetime] = None


class NewsItem(BaseModel):
    title: str
    link: str = ""
    pub_date: str = ""
    narrative: str = ""
    title_hash: str = ""
    timestamp: Optional[datetime] = None

