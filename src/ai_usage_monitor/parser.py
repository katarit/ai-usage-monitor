from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import RateLimitWindow, TokenUsage, UsageEvent

# Codex's primary/secondary rate-limit keys are positional, not name-bound: the
# account can report only one window at a time (observed: the 5h window absent,
# a single window carrying the 7-day length reported under "primary"). Classify
# by the window's own length instead of trusting which key it arrived under.
CODEX_WEEK_WINDOW_MINUTES_THRESHOLD = 1440  # 1 day; separates 5h (300) from week (10080)


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
TIME_KEYS = ("captured_at", "timestamp", "created_at", "createdAt", "time", "date")


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
    context_usage = context_window_usage(obj)
    if context_usage is not None:
        return context_usage
    token_count_usage = token_count_usage_object(obj)
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


def context_window_usage(obj: dict[str, Any]) -> dict[str, Any] | None:
    context_window = obj.get("context_window") or obj.get("contextWindow")
    if not isinstance(context_window, dict):
        return None
    current_usage = context_window.get("current_usage") or context_window.get("currentUsage")
    if isinstance(current_usage, dict):
        return current_usage
    usage = {}
    if "total_input_tokens" in context_window:
        usage["input_tokens"] = context_window["total_input_tokens"]
    if "totalInputTokens" in context_window:
        usage["input_tokens"] = context_window["totalInputTokens"]
    if "total_output_tokens" in context_window:
        usage["output_tokens"] = context_window["total_output_tokens"]
    if "totalOutputTokens" in context_window:
        usage["output_tokens"] = context_window["totalOutputTokens"]
    return usage if has_any_token_key(usage) else None


def token_count_usage_object(obj: dict[str, Any]) -> dict[str, Any] | None:
    if obj.get("type") != "event_msg":
        return None
    payload = obj.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "token_count":
        return None
    info = payload.get("info")
    if not isinstance(info, dict):
        return None
    value = info.get("total_token_usage")
    if isinstance(value, dict):
        return value
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
    for key, label in (("five_hour", "5h"), ("seven_day", "week")):
        value = rate_limits.get(key)
        if isinstance(value, dict):
            window = rate_limit_window_from_dict(label, value)
            if window is not None:
                windows.append(window)
    for key, default_label in (("primary", "5h"), ("secondary", "week")):
        value = rate_limits.get(key)
        if isinstance(value, dict):
            window = rate_limit_window_from_dict(default_label, value)
            if window is not None:
                windows.append(classify_codex_window(window))
    if not windows:
        return None
    plan_type = rate_limits.get("plan_type")
    return windows, plan_type if isinstance(plan_type, str) else None


def classify_codex_window(window: RateLimitWindow) -> RateLimitWindow:
    if window.window_minutes is None:
        return window
    inferred = "week" if window.window_minutes > CODEX_WEEK_WINDOW_MINUTES_THRESHOLD else "5h"
    if inferred == window.name:
        return window
    return replace(window, name=inferred)


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
