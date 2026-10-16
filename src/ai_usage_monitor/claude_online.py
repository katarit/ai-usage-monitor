from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from .models import RateLimitWindow, utc_now
from .parser import parse_timestamp
from .providers import RateLimitSnapshot


ENDPOINT = "https://api.anthropic.com/api/oauth/usage"
BETA_HEADER = "oauth-2025-04-20"
CACHE_NAME = "claude-online-usage-cache.json"
HISTORY_NAME = "claude-online-usage-history.jsonl"
ERRORS_NAME = "claude-online-errors.jsonl"
# Measured 2026-07..10, the endpoint sustained roughly 28 requests per hour per
# OAuth token (not documented; may change). 150s leaves headroom below that.
DEFAULT_TTL_SECONDS = 150
DEFAULT_BACKOFF_SECONDS = 300
MIN_BACKOFF_SECONDS = 30
MAX_BACKOFF_SECONDS = 3600
HISTORY_KEEP = 240
LOG_KEEP_DAYS = 30
KEYCHAIN_SERVICE = "Claude Code-credentials"
TOKEN_EXPIRED_NOTE = "Claude Code OAuth token expired; waiting for Claude Code to refresh it"


@dataclass
class ClaudeOnlineUsage:
    snapshots: list[RateLimitSnapshot]
    notes: list[str]


@dataclass
class OAuthCredentials:
    token: str | None
    plan_type: str | None
    expires_at: datetime | None


def load_claude_online_usage(home: Path, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> ClaudeOnlineUsage:
    cache_path = home / ".ai-usage-monitor" / CACHE_NAME
    history_path = home / ".ai-usage-monitor" / HISTORY_NAME
    errors_path = home / ".ai-usage-monitor" / ERRORS_NAME
    cached = read_cache(cache_path)
    history = read_history(history_path)
    if cached is not None and cache_is_fresh(cached, ttl_seconds):
        snapshot = snapshot_from_cache(cached, stale=False)
        if snapshot is not None:
            return ClaudeOnlineUsage(combine_snapshots(history, snapshot), [])

    def stale(notes: list[str]) -> ClaudeOnlineUsage:
        snapshot = snapshot_from_cache(cached, stale=True) if cached is not None else None
        return ClaudeOnlineUsage(combine_snapshots(history, snapshot), notes)

    # Credentials are read before the backoff check so that a token Claude Code
    # has just refreshed is used right away instead of waiting out the backoff.
    credentials = read_oauth_credentials(home)
    if credentials.token is None:
        return stale(["Claude online usage needs Claude Code OAuth credentials"])
    if credentials.expires_at is not None and credentials.expires_at <= utc_now():
        # An expired token was observed to get HTTP 429 (not 401), so fetching
        # would only extend the backoff while the shown quota goes stale.
        expired = credentials.expires_at.astimezone().strftime("%m-%d %H:%M")
        return stale([f"{TOKEN_EXPIRED_NOTE} (expired {expired})"])
    if cached is not None and backoff_is_active(cached) and not token_changed_since_backoff(cached, credentials):
        return stale(["Claude online usage fetch is backed off after rate limiting"])

    try:
        response = fetch_usage(credentials.token)
    except urllib.error.HTTPError as exc:
        if exc.code != 429:
            return stale([f"Claude online usage fetch failed: HTTP {exc.code}"])
        retry_after = exc.headers.get("Retry-After") if exc.headers is not None else None
        append_log(
            errors_path,
            {
                "at": utc_now().isoformat(),
                "status": 429,
                "retry_after": retry_after,
                "fetches_last_hour": count_recent(history, timedelta(hours=1)),
            },
            "at",
        )
        if cached is not None:
            backoff = backoff_seconds(retry_after)
            cached = dict(cached)
            cached["last_error"] = "HTTP 429"
            cached["backoff_until"] = (utc_now() + timedelta(seconds=backoff)).isoformat()
            cached["backoff_token_expires_at"] = (
                credentials.expires_at.isoformat() if credentials.expires_at is not None else None
            )
            write_cache(cache_path, cached)
        return stale(["Claude online usage fetch rate limited"])
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        return stale([f"Claude online usage fetch failed: {exc.__class__.__name__}"])

    fetched_at = utc_now()
    cache = {
        "fetched_at": fetched_at.isoformat(),
        "plan_type": credentials.plan_type,
        "response": sanitize_response(response),
    }
    write_cache(cache_path, cache)
    append_log(history_path, cache, "fetched_at")
    snapshot = snapshot_from_cache(cache, stale=False)
    snapshots = combine_snapshots(history, snapshot)
    return ClaudeOnlineUsage(snapshots, [])


def read_oauth_token(home: Path) -> tuple[str | None, str | None]:
    credentials = read_oauth_credentials(home)
    return credentials.token, credentials.plan_type


def read_oauth_credentials(home: Path) -> OAuthCredentials:
    env_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if env_token:
        return OAuthCredentials(env_token, None, None)

    # On macOS the Keychain is what Claude Code keeps current; a
    # .credentials.json left on disk can hold an expired token.
    credentials = read_credentials_keychain() if sys.platform == "darwin" else None
    if credentials is None:
        credentials = read_credentials_file(home)
    if credentials is None:
        return OAuthCredentials(None, None, None)

    oauth = credentials.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        return OAuthCredentials(None, None, None)
    token = oauth.get("accessToken")
    plan_type = oauth.get("subscriptionType") or oauth.get("rateLimitTier")
    return OAuthCredentials(
        token if isinstance(token, str) and token else None,
        plan_type if isinstance(plan_type, str) and plan_type else None,
        parse_expires_at(oauth.get("expiresAt")),
    )


def parse_expires_at(value: Any) -> datetime | None:
    # Claude Code stores expiresAt as epoch milliseconds.
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def read_credentials_file(home: Path) -> dict[str, Any] | None:
    credentials_path = home / ".claude" / ".credentials.json"
    try:
        with credentials_path.open("r", encoding="utf-8") as handle:
            credentials = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    return credentials if isinstance(credentials, dict) else None


def read_credentials_keychain() -> dict[str, Any] | None:
    # Claude Code on macOS keeps the login in the Keychain instead of .credentials.json.
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        credentials = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return credentials if isinstance(credentials, dict) else None


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


def append_log(path: Path, value: dict[str, Any], time_key: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False)
            handle.write("\n")
    except OSError:
        return
    trim_log(path, time_key)


def trim_log(path: Path, time_key: str, keep_days: int = LOG_KEEP_DAYS) -> None:
    """Drops entries older than keep_days.

    The file is rewritten only once the oldest entry is a day past the limit,
    so a log appended every few minutes is rewritten about once a day.
    """
    now = utc_now()
    try:
        with path.open("r", encoding="utf-8") as handle:
            first = handle.readline()
    except OSError:
        return
    oldest = log_entry_time(first, time_key)
    if oldest is None or oldest >= now - timedelta(days=keep_days + 1):
        return
    cutoff = now - timedelta(days=keep_days)
    temp_path = path.with_name(path.name + ".tmp")
    try:
        with path.open("r", encoding="utf-8") as source, temp_path.open("w", encoding="utf-8") as target:
            for line in source:
                timestamp = log_entry_time(line, time_key)
                if timestamp is not None and timestamp >= cutoff:
                    target.write(line)
        os.replace(temp_path, path)
    except OSError:
        try:
            temp_path.unlink()
        except OSError:
            pass


def log_entry_time(line: str, time_key: str) -> datetime | None:
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    timestamp = parse_timestamp(str(value.get(time_key) or ""))
    return timestamp.astimezone(timezone.utc) if timestamp is not None else None


def read_tail_lines(path: Path, count: int, block_size: int = 65536) -> list[str]:
    # Reads backwards from the end so the cost does not grow with the file.
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            position = handle.tell()
            data = b""
            while position > 0 and data.count(b"\n") <= count:
                step = min(block_size, position)
                position -= step
                handle.seek(position)
                data = handle.read(step) + data
    except OSError:
        return []
    lines = data.decode("utf-8", errors="replace").splitlines()
    if position > 0:
        lines = lines[1:]  # the first line may be cut in the middle
    return lines[-count:]


def read_history(path: Path, keep: int = HISTORY_KEEP) -> list[RateLimitSnapshot]:
    snapshots: list[RateLimitSnapshot] = []
    for line in read_tail_lines(path, keep):
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


def token_changed_since_backoff(cache: dict[str, Any], credentials: OAuthCredentials) -> bool:
    # The limit appeared to be per token (another machine's token kept working
    # meanwhile), so a token Claude Code refreshed after the 429 is tried right
    # away. Compared by expiry; the token is never stored.
    if credentials.expires_at is None:
        return False
    previous = parse_timestamp(str(cache.get("backoff_token_expires_at") or ""))
    if previous is None:
        return False
    return previous.astimezone(timezone.utc) != credentials.expires_at


def backoff_seconds(retry_after: str | None) -> int:
    seconds = None
    if retry_after:
        value = retry_after.strip()
        if value.isdigit():
            seconds = int(value)
        else:
            try:
                when = parsedate_to_datetime(value)
            except (TypeError, ValueError):
                when = None
            if when is not None:
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                seconds = int((when - utc_now()).total_seconds())
    if seconds is None:
        return DEFAULT_BACKOFF_SECONDS
    return max(MIN_BACKOFF_SECONDS, min(seconds, MAX_BACKOFF_SECONDS))


def count_recent(snapshots: list[RateLimitSnapshot], window: timedelta) -> int:
    since = utc_now() - window
    return sum(
        1
        for snapshot in snapshots
        if snapshot.timestamp is not None and snapshot.timestamp.astimezone(timezone.utc) >= since
    )


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
