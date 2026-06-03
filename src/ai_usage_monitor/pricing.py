from __future__ import annotations

from .models import Price, TokenUsage


DEFAULT_PRICES: dict[str, Price] = {
    "claude-3-5-sonnet": Price(3.0, 15.0, 3.75, 0.30),
    "claude-3-7-sonnet": Price(3.0, 15.0, 3.75, 0.30),
    "claude-sonnet-4": Price(3.0, 15.0, 3.75, 0.30),
    "claude-opus-4": Price(15.0, 75.0, 18.75, 1.50),
    "gpt-5.1-codex": Price(0.0, 0.0),
    "gpt-5.1-codex-mini": Price(0.0, 0.0),
    "unknown": Price(0.0, 0.0),
}


def price_for_model(model: str | None) -> Price:
    if not model:
        return DEFAULT_PRICES["unknown"]
    normalized = model.lower()
    for key, price in DEFAULT_PRICES.items():
        if key in normalized:
            return price
    return DEFAULT_PRICES["unknown"]


def estimate_cost(model: str | None, usage: TokenUsage) -> float:
    price = price_for_model(model)
    return (
        usage.input_tokens * price.input_per_million
        + usage.output_tokens * price.output_per_million
        + usage.cache_creation_input_tokens * price.cache_creation_per_million
        + usage.cache_read_input_tokens * price.cache_read_per_million
    ) / 1_000_000
