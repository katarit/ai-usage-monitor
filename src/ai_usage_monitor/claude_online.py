from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import RateLimitWindow, utc_now
from .parser import parse_timestamp
from .providers import RateLimitSnapshot


ENDPOINT = "https://api.anthropic.com/api/oauth/usage"
BETA_HEADER = "oauth-2025-04-20"
CACHE_NAME = "claude-online-usage-cache.json"
HISTORY_NAME = "claude-online-usage-history.jsonl"
DEFAULT_BACKOFF_SECONDS = 300


@dataclass
class ClaudeOnlineUsage:
    snapshots: list[RateLimitSnapshot]
    notes: list[str]


def load_claude_online_usage(home: Path, ttl_seconds: int = 60) -> ClaudeOnlineUsage:
    cache_path = home / ".ai-usage-monitor" / CACHE_NAME
    history_path = home / ".ai-usage-monitor" / HISTORY_NAME
    cached = read_cache(cache_path)
    history = read_history(history_path)
    if cached is not None and cache_is_fresh(cached, ttl_seconds):
        snapshot = snapshot_from_cache(cached, stale=False)
        if snapshot is not None:
            return ClaudeOnlineUsage(combine_snapshots(history, snapshot), [])
    if cached is not None and backoff_is_active(cached):
        snapshot = snapshot_from_cache(cached, stale=True)
        notes = ["Claude online usage fetch is backed off after rate limiting"]
        snapshots = combine_snapshots(history, snapshot)
        return ClaudeOnlineUsage(snapshots, notes)

    token, plan_type = read_oauth_token(home)
    if token is None:
        snapshot = snapshot_from_cache(cached, stale=True) if cached is not None else None
        notes = ["Claude online usage needs Claude Code OAuth credentials"]
        snapshots = combine_snapshots(history, snapshot)
        return ClaudeOnlineUsage(snapshots, notes)

    try:
        response = fetch_usage(token)
    except urllib.error.HTTPError as exc:
        snapshot = snapshot_from_cache(cached, stale=True) if cached is not None else None
        notes = [f"Claude online usage fetch failed: HTTP {exc.code}"]
        if exc.code == 429 and cached is not None:
            updated = dict(cached)
            updated["last_error"] = "HTTP 429"
            updated["backoff_until"] = (utc_now() + timedelta(seconds=DEFAULT_BACKOFF_SECONDS)).isoformat()
            write_cache(cache_path, updated)
            snapshot = snapshot_from_cache(updated, stale=True)
            notes = ["Claude online usage fetch rate limited"]
        snapshots = combine_snapshots(history, snapshot)
        return ClaudeOnlineUsage(snapshots, notes)
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        snapshot = snapshot_from_cache(cached, stale=True) if cached is not None else None
        notes = [f"Claude online usage fetch failed: {exc.__class__.__name__}"]
        snapshots = combine_snapshots(history, snapshot)
        return ClaudeOnlineUsage(snapshots, notes)

    fetched_at = utc_now()
    cache = {
        "fetched_at": fetched_at.isoformat(),
        "plan_type": plan_type,
        "response": sanitize_response(response),
    }
    write_cache(cache_path, cache)
    append_history(history_path, cache)
    snapshot = snapshot_from_cache(cache, stale=False)
    snapshots = combine_snapshots(history, snapshot)
    return ClaudeOnlineUsage(snapshots, [])


def read_oauth_token(home: Path) -> tuple[str | None, str | None]:
    env_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if env_token:
        return env_token, None

    credentials_path = home / ".claude" / ".credentials.json"
    try:
        with credentials_path.open("r", encoding="utf-8") as handle:
            credentials = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None, None

    oauth = credentials.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        return None, None
    token = oauth.get("accessToken")
    plan_type = oauth.get("subscriptionType") or oauth.get("rateLimitTier")
    return (
        token if isinstance(token, str) and token else None,
        plan_type if isinstance(plan_type, str) and plan_type else None,
    )


def fetch_usage(token: str) -> dict[str, Any]:
    request = urllib.request.Request(
        ENDPOINT,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": BETA_HEADER,
            "User-Agent": "ai-usage-monitor",
        },
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = response.read().decode("utf-8")
    value = json.loads(payload)
    return value if isinstance(value, dict) else {}


def sanitize_response(response: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key in ("five_hour", "seven_day", "extra_usage"):
        value = response.get(key)
        if isinstance(value, dict):
            sanitized[key] = value
        elif value is None:
            sanitized[key] = None
    return sanitized


def read_cache(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def write_cache(path: Path, value: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except OSError:
        return


def append_history(path: Path, value: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False)
            handle.write("\n")
    except OSError:
        return


def read_history(path: Path, keep: int = 240) -> list[RateLimitSnapshot]:
    snapshots: list[RateLimitSnapshot] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return snapshots
    for line in lines[-keep:]:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict):
            continue
        snapshot = snapshot_from_cache(value, stale=True)
        if snapshot is not None:
            snapshots.append(snapshot)
    return snapshots


def combine_snapshots(
    history: list[RateLimitSnapshot],
    current: RateLimitSnapshot | None,
) -> list[RateLimitSnapshot]:
    if current is None or current.timestamp is None:
        return history
    return [
        snapshot
        for snapshot in history
        if snapshot.timestamp is None or snapshot.timestamp != current.timestamp
    ] + [current]


def cache_is_fresh(cache: dict[str, Any], ttl_seconds: int) -> bool:
    fetched_at = parse_timestamp(str(cache.get("fetched_at") or ""))
    if fetched_at is None:
        return False
    age = (utc_now() - fetched_at.astimezone(timezone.utc)).total_seconds()
    return age <= max(ttl_seconds, 0)


def backoff_is_active(cache: dict[str, Any]) -> bool:
    backoff_until = parse_timestamp(str(cache.get("backoff_until") or ""))
    if backoff_until is None:
        return False
    return utc_now() < backoff_until.astimezone(timezone.utc)


def snapshot_from_cache(cache: dict[str, Any], *, stale: bool) -> RateLimitSnapshot | None:
    response = cache.get("response")
    if not isinstance(response, dict):
        return None
    fetched_at = parse_timestamp(str(cache.get("fetched_at") or ""))
    windows: list[RateLimitWindow] = []
    for key, name in (("five_hour", "5h"), ("seven_day", "week")):
        value = response.get(key)
        if not isinstance(value, dict):
            continue
        window = online_window(name, value)
        if window is not None:
            windows.append(window)
    if not windows:
        return None
    status = "online stale" if stale else "online"
    plan_type = cache.get("plan_type")
    return RateLimitSnapshot(
        "Claude online usage endpoint",
        fetched_at,
        windows,
        plan_type if isinstance(plan_type, str) else None,
        {"rate_limits": status, "context_window": "local transcript"},
    )


def online_window(name: str, value: dict[str, Any]) -> RateLimitWindow | None:
    utilization = value.get("utilization")
    if not isinstance(utilization, (int, float)):
        return None
    resets_at = value.get("resets_at")
    return RateLimitWindow(
        name=name,
        used_percent=float(utilization),
        resets_at=parse_timestamp(resets_at if isinstance(resets_at, str) else None),
    )
