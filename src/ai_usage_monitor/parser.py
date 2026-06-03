from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import RateLimitWindow, TokenUsage, UsageEvent


INPUT_KEYS = ("input_tokens", "inputTokens", "prompt_tokens", "promptTokens")
OUTPUT_KEYS = ("output_tokens", "outputTokens", "completion_tokens", "completionTokens")
CACHE_CREATE_KEYS = (
    "cache_creation_input_tokens",
    "cacheCreationInputTokens",
    "cache_creation_tokens",
    "cacheCreationTokens",
)
CACHE_READ_KEYS = (
    "cache_read_input_tokens",
    "cacheReadInputTokens",
    "cached_input_tokens",
    "cachedInputTokens",
    "cached_tokens",
    "cachedTokens",
)
USAGE_KEYS = ("usage", "token_usage", "tokenUsage")
MODEL_KEYS = ("model", "model_name", "modelName")
TIME_KEYS = ("timestamp", "created_at", "createdAt", "time", "date")


def parse_json_lines(path: Path) -> Iterable[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    yield value
    except OSError:
        return


def parse_json_file(path: Path) -> Iterable[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return
    if isinstance(value, dict):
        yield value
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                yield item


def extract_event(provider: str, source: Path, obj: dict[str, Any]) -> UsageEvent | None:
    usage_obj = first_usage_object(obj)
    if usage_obj is None:
        return None
    usage = usage_from_dict(usage_obj)
    if usage.total <= 0:
        return None
    return UsageEvent(
        provider=provider,
        source=str(source),
        timestamp=parse_timestamp(first_string(obj, TIME_KEYS)),
        model=first_string(obj, MODEL_KEYS) or first_string(usage_obj, MODEL_KEYS),
        usage=usage,
        raw=obj,
    )


def first_usage_object(obj: dict[str, Any]) -> dict[str, Any] | None:
    token_count_usage = token_count_last_usage(obj)
    if token_count_usage is not None:
        return token_count_usage
    for key in USAGE_KEYS:
        value = obj.get(key)
        if isinstance(value, dict):
            return value
    if has_any_token_key(obj):
        return obj
    for value in obj.values():
        if isinstance(value, dict):
            found = first_usage_object(value)
            if found is not None:
                return found
    return None


def token_count_last_usage(obj: dict[str, Any]) -> dict[str, Any] | None:
    if obj.get("type") != "event_msg":
        return None
    payload = obj.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "token_count":
        return None
    info = payload.get("info")
    if not isinstance(info, dict):
        return None
    value = info.get("last_token_usage")
    return value if isinstance(value, dict) else None


def has_any_token_key(obj: dict[str, Any]) -> bool:
    keys = set(obj.keys())
    return bool(keys.intersection(INPUT_KEYS + OUTPUT_KEYS + CACHE_CREATE_KEYS + CACHE_READ_KEYS))


def usage_from_dict(obj: dict[str, Any]) -> TokenUsage:
    return TokenUsage(
        input_tokens=first_int(obj, INPUT_KEYS),
        output_tokens=first_int(obj, OUTPUT_KEYS),
        cache_creation_input_tokens=first_int(obj, CACHE_CREATE_KEYS),
        cache_read_input_tokens=first_int(obj, CACHE_READ_KEYS),
    )


def first_int(obj: dict[str, Any], keys: tuple[str, ...]) -> int:
    for key in keys:
        value = obj.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return 0


def first_string(obj: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = obj.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def extract_rate_limits(obj: dict[str, Any]) -> tuple[list[RateLimitWindow], str | None] | None:
    rate_limits = find_rate_limits_object(obj)
    if rate_limits is None:
        return None
    windows: list[RateLimitWindow] = []
    for key, label in (("primary", "5h"), ("secondary", "week")):
        value = rate_limits.get(key)
        if isinstance(value, dict):
            window = rate_limit_window_from_dict(label, value)
            if window is not None:
                windows.append(window)
    if not windows:
        return None
    plan_type = rate_limits.get("plan_type")
    return windows, plan_type if isinstance(plan_type, str) else None


def find_rate_limits_object(obj: dict[str, Any]) -> dict[str, Any] | None:
    value = obj.get("rate_limits") or obj.get("rateLimits")
    if isinstance(value, dict):
        return value
    for child in obj.values():
        if isinstance(child, dict):
            found = find_rate_limits_object(child)
            if found is not None:
                return found
    return None


def rate_limit_window_from_dict(name: str, obj: dict[str, Any]) -> RateLimitWindow | None:
    percent = first_float(obj, ("used_percent", "used_percentage", "usedPercent", "usedPercentage"))
    if percent is None:
        return None
    return RateLimitWindow(
        name=name,
        used_percent=percent,
        window_minutes=first_optional_int(obj, ("window_minutes", "windowMinutes")),
        resets_at=parse_unix_timestamp(first_optional_int(obj, ("resets_at", "resetsAt"))),
    )


def first_float(obj: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = obj.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                continue
    return None


def first_optional_int(obj: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        value = obj.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def parse_unix_timestamp(value: int | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None
