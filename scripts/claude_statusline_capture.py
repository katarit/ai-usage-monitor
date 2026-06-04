from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        write_heartbeat(False, ["invalid_json"])
        return 0

    out_dir = Path(os.environ.get("AI_USAGE_MONITOR_DIR", Path.home() / ".ai-usage-monitor"))
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "claude-statusline.json"
    tmp_path = out_dir / "claude-statusline.json.tmp"
    previous = read_previous(out_path)
    rate_limits = find_rate_limits(payload) or previous.get("rate_limits")
    context_window = find_context_window(payload) or previous.get("context_window")
    has_new_rate_limits = bool(find_rate_limits(payload))
    has_new_context_window = bool(find_context_window(payload))
    write_heartbeat(
        has_new_rate_limits,
        sorted(payload.keys()) if isinstance(payload, dict) else [],
        has_context_window=has_new_context_window,
    )
    if not rate_limits and not context_window:
        print("AI usage: rate limits unavailable")
        return 0

    output = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "capture_status": {
            "rate_limits": "fresh" if has_new_rate_limits else "carried",
            "context_window": "fresh" if has_new_context_window else "carried",
        },
    }
    if rate_limits:
        output["rate_limits"] = rate_limits
    if context_window:
        output["context_window"] = context_window
    maybe_copy(payload, output, "cost")
    maybe_copy(payload, output, "timestamp")
    maybe_copy(payload, output, "model")
    tmp_path.write_text(json.dumps(output, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(out_path)
    append_history(out_dir, output)
    print(render_statusline(rate_limits or {}))
    return 0


def read_previous(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def maybe_copy(source, target, key):
    if isinstance(source, dict) and key in source and isinstance(source[key], (str, int, float, dict)):
        target[key] = source[key]


def append_history(out_dir: Path, output: dict) -> None:
    history_path = out_dir / "claude-statusline-history.jsonl"
    with history_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(output, ensure_ascii=False) + "\n")


def write_heartbeat(has_rate_limits: bool, top_level_keys, *, has_context_window: bool = False) -> None:
    out_dir = Path(os.environ.get("AI_USAGE_MONITOR_DIR", Path.home() / ".ai-usage-monitor"))
    out_dir.mkdir(parents=True, exist_ok=True)
    heartbeat = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "has_rate_limits": has_rate_limits,
        "has_context_window": has_context_window,
        "top_level_keys": list(top_level_keys),
    }
    out_path = out_dir / "claude-statusline-heartbeat.json"
    tmp_path = out_dir / "claude-statusline-heartbeat.json.tmp"
    tmp_path.write_text(json.dumps(heartbeat, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(out_path)


def render_statusline(rate_limits) -> str:
    primary = first_percent_left(rate_limits, ("five_hour", "primary"))
    secondary = first_percent_left(rate_limits, ("seven_day", "secondary"))
    parts = []
    if primary is not None:
        parts.append(f"5h {primary:.0f}% left")
    if secondary is not None:
        parts.append(f"week {secondary:.0f}% left")
    if not parts:
        return "AI usage: rate limits unavailable"
    return "AI usage: " + " | ".join(parts)


def first_percent_left(rate_limits, keys) -> float | None:
    for key in keys:
        value = percent_left(rate_limits.get(key))
        if value is not None:
            return value
    return None


def percent_left(window) -> float | None:
    if not isinstance(window, dict):
        return None
    value = (
        window.get("used_percentage")
        if "used_percentage" in window
        else window.get("used_percent")
    )
    if isinstance(value, (int, float)):
        return max(100.0 - float(value), 0.0)
    return None


def find_rate_limits(value):
    if isinstance(value, dict):
        candidate = value.get("rate_limits") or value.get("rateLimits")
        if isinstance(candidate, dict):
            return candidate
        for child in value.values():
            found = find_rate_limits(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_rate_limits(child)
            if found:
                return found
    return None


def find_context_window(value):
    if isinstance(value, dict):
        candidate = value.get("context_window") or value.get("contextWindow")
        if isinstance(candidate, dict):
            return candidate
        for child in value.values():
            found = find_context_window(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_context_window(child)
            if found:
                return found
    return None


if __name__ == "__main__":
    raise SystemExit(main())
