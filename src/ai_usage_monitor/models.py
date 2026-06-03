from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @property
    def total(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )

    def add(self, other: "TokenUsage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_creation_input_tokens += other.cache_creation_input_tokens
        self.cache_read_input_tokens += other.cache_read_input_tokens


@dataclass
class UsageEvent:
    provider: str
    source: str
    timestamp: datetime | None
    model: str | None
    usage: TokenUsage
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Price:
    input_per_million: float
    output_per_million: float
    cache_creation_per_million: float = 0.0
    cache_read_per_million: float = 0.0


@dataclass
class RateLimitWindow:
    name: str
    used_percent: float
    window_minutes: int | None = None
    resets_at: datetime | None = None
    predicted_end: datetime | None = None

    @property
    def remaining_percent(self) -> float:
        return max(100.0 - self.used_percent, 0.0)


@dataclass
class ProviderSummary:
    name: str
    events: int
    files: int
    usage: TokenUsage
    estimated_cost: float
    limit_tokens: int | None
    remaining_tokens: int | None
    burn_tokens_per_hour: float | None
    estimated_exhaustion: datetime | None
    last_activity: datetime | None
    rate_limits: list[RateLimitWindow] = field(default_factory=list)
    plan_type: str | None = None
    limit_source: str | None = None
    notes: list[str] = field(default_factory=list)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
