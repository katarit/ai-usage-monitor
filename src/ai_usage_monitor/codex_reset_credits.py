from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import ResetCredit, utc_now
from .parser import parse_timestamp


ENDPOINT = "https://chatgpt.com/backend-api/wham/rate-limit-reset-credits"
CACHE_NAME = "codex-reset-credits-cache.json"
SOURCE = "Codex reset credits endpoint"
DEFAULT_BACKOFF_SECONDS = 300


@dataclass
class CodexResetCredits:
    credits: list[ResetCredit]
    source: str | None
    notes: list[str]


def load_codex_reset_credits(home: Path, ttl_seconds: int = 300) -> CodexResetCredits:
    cache_path = home / ".ai-usage-monitor" / CACHE_NAME
    cached = read_cache(cache_path)
    if cached is not None and cache_is_fresh(cached, ttl_seconds):
        return CodexResetCredits(credits_from_cache(cached), source_from_cache(cached, stale=False), [])
    if cached is not None and backoff_is_active(cached):
        return CodexResetCredits(
            credits_from_cache(cached),
            source_from_cache(cached, stale=True),
            ["Codex reset credits fetch is backed off after rate limiting"],
        )

    auth = read_codex_auth(home)
    if auth is None:
        notes = ["Codex reset credits need Codex OAuth credentials"]
        return CodexResetCredits(credits_from_cache(cached), source_from_cache(cached, stale=True), notes)

    token, account_id = auth
    try:
        response = fetch_reset_credits(token, account_id)
    except urllib.error.HTTPError as exc:
        if exc.code == 429 and cached is not None:
            updated = dict(cached)
            updated["last_error"] = "HTTP 429"
            updated["backoff_until"] = (utc_now() + timedelta(seconds=DEFAULT_BACKOFF_SECONDS)).isoformat()
            write_cache(cache_path, updated)
            return CodexResetCredits(
                credits_from_cache(updated),
                source_from_cache(updated, stale=True),
                ["Codex reset credits fetch rate limited"],
            )
        notes = [f"Codex reset credits fetch failed: HTTP {exc.code}"]
        return CodexResetCredits(credits_from_cache(cached), source_from_cache(cached, stale=True), notes)
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        notes = [f"Codex reset credits fetch failed: {exc.__class__.__name__}"]
        return CodexResetCredits(credits_from_cache(cached), source_from_cache(cached, stale=True), notes)

    cache = {
        "fetched_at": utc_now().isoformat(),
        "response": sanitize_response(response),
    }
    write_cache(cache_path, cache)
    return CodexResetCredits(credits_from_cache(cache), source_from_cache(cache, stale=False), [])


def read_codex_auth(home: Path) -> tuple[str, str] | None:
    auth_path = home / ".codex" / "auth.json"
    try:
        with auth_path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    tokens = value.get("tokens")
    if not isinstance(tokens, dict):
        return None
    access_token = tokens.get("access_token")
    account_id = tokens.get("account_id")
    if not isinstance(access_token, str) or not access_token:
        return None
    if not isinstance(account_id, str) or not account_id:
        return None
    return access_token, account_id


def fetch_reset_credits(token: str, account_id: str) -> dict[str, Any]:
    request = urllib.request.Request(
        ENDPOINT,
        headers={
            "Authorization": f"Bearer {token}",
            "OpenAI-Beta": f"organization={account_id}",
            "ChatGPT-Account-ID": account_id,
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
    for key in ("available_count", "total_earned_count"):
        value = response.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            sanitized[key] = value
    credits = []
    for credit in response.get("credits") or []:
        if not isinstance(credit, dict):
            continue
        sanitized_credit: dict[str, Any] = {}
        for key in ("status", "reset_type", "granted_at", "expires_at", "title", "description"):
            value = credit.get(key)
            if isinstance(value, str):
                sanitized_credit[key] = value
        credits.append(sanitized_credit)
    sanitized["credits"] = credits
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


def credits_from_cache(cache: dict[str, Any] | None) -> list[ResetCredit]:
    if cache is None:
        return []
    response = cache.get("response")
    if not isinstance(response, dict):
        return []
    credits: list[ResetCredit] = []
    for credit in response.get("credits") or []:
        if not isinstance(credit, dict):
            continue
        status = credit.get("status")
        if not isinstance(status, str) or not status:
            continue
        credits.append(
            ResetCredit(
                status=status,
                expires_at=parse_timestamp(credit.get("expires_at") if isinstance(credit.get("expires_at"), str) else None),
                granted_at=parse_timestamp(credit.get("granted_at") if isinstance(credit.get("granted_at"), str) else None),
                reset_type=credit.get("reset_type") if isinstance(credit.get("reset_type"), str) else None,
                title=credit.get("title") if isinstance(credit.get("title"), str) else None,
                description=credit.get("description") if isinstance(credit.get("description"), str) else None,
            )
        )
    return credits


def source_from_cache(cache: dict[str, Any] | None, *, stale: bool) -> str | None:
    if cache is None or not credits_from_cache(cache):
        return None
    fetched_at = parse_timestamp(str(cache.get("fetched_at") or ""))
    age = relative_age(fetched_at)
    suffix = "stale" if stale else "cached"
    return f"{SOURCE} {suffix} {age}"


def relative_age(value: datetime | None) -> str:
    if value is None:
        return "unknown"
    seconds = max(0, int((utc_now() - value.astimezone(timezone.utc)).total_seconds()))
    if seconds < 60:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    return f"{hours}h {minutes % 60}m ago"
