from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .models import ProviderSummary, RateLimitWindow, TokenUsage, UsageEvent, utc_now
from .parser import extract_event, extract_rate_limits, parse_json_file, parse_json_lines, parse_timestamp
from .pricing import estimate_cost


@dataclass
class Provider:
    name: str
    default_roots: list[Path]

    def collect(self, roots: list[Path] | None = None) -> tuple[list[UsageEvent], int, list[str], list[RateLimitSnapshot]]:
        scan_roots = roots or self.default_roots
        events: list[UsageEvent] = []
        rate_limit_snapshots: list[RateLimitSnapshot] = []
        files = 0
        notes: list[str] = []
        for root in scan_roots:
            if not root.exists():
                notes.append(f"path not found: {root}")
                continue
            for path in iter_log_files(root):
                files += 1
                parser = parse_json_lines if path.suffix.lower() == ".jsonl" else parse_json_file
                for obj in parser(path):
                    event = extract_event(self.name, path, obj)
                    if event is not None:
                        events.append(event)
                    snapshot = extract_rate_limit_snapshot(path, obj)
                    if snapshot is not None:
                        rate_limit_snapshots.append(snapshot)
        return events, files, notes, rate_limit_snapshots


@dataclass
class RateLimitSnapshot:
    source: str
    timestamp: datetime | None
    windows: list[RateLimitWindow]
    plan_type: str | None


def iter_log_files(root: Path):
    if root.is_file():
        if root.suffix.lower() in {".json", ".jsonl"}:
            yield root
        return
    try:
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".json", ".jsonl"}:
                yield path
    except OSError:
        return


def summarize(
    name: str,
    events: list[UsageEvent],
    files: int,
    limit_tokens: int | None,
    notes: list[str],
    rate_limit_snapshots: list[RateLimitSnapshot] | None = None,
) -> ProviderSummary:
    usage = TokenUsage()
    cost = 0.0
    last_activity = None
    for event in events:
        usage.add(event.usage)
        cost += estimate_cost(event.model, event.usage)
        if event.timestamp and (last_activity is None or event.timestamp > last_activity):
            last_activity = event.timestamp

    remaining = None
    if limit_tokens is not None:
        remaining = max(limit_tokens - usage.total, 0)

    burn = burn_rate(events)
    exhaustion = None
    if remaining is not None and burn and burn > 0:
        exhaustion = utc_now() + timedelta(hours=remaining / burn)

    summary_notes = list(notes)
    if not events:
        summary_notes.append("no observable token usage found")

    latest_rate_limits = latest_rate_limit_snapshot(rate_limit_snapshots or [])
    rate_limits = with_predicted_end(latest_rate_limits, rate_limit_snapshots or []) if latest_rate_limits else []
    plan_type = latest_rate_limits.plan_type if latest_rate_limits else None
    limit_source = latest_rate_limits.source if latest_rate_limits else None
    if name.lower() == "codex" and not rate_limits:
        summary_notes.append("no Codex rate_limits snapshot found in local sessions")
    if name.lower() == "claude code" and not rate_limits:
        summary_notes.append("Claude rate_limits require statusline capture setup")

    return ProviderSummary(
        name=name,
        events=len(events),
        files=files,
        usage=usage,
        estimated_cost=cost,
        limit_tokens=limit_tokens,
        remaining_tokens=remaining,
        burn_tokens_per_hour=burn,
        estimated_exhaustion=exhaustion,
        last_activity=last_activity,
        rate_limits=rate_limits,
        plan_type=plan_type,
        limit_source=limit_source,
        notes=summary_notes,
    )


def burn_rate(events: list[UsageEvent]) -> float | None:
    stamped = [event for event in events if event.timestamp is not None]
    if len(stamped) < 2:
        return None
    stamped.sort(key=lambda event: event.timestamp)
    start = stamped[0].timestamp
    end = stamped[-1].timestamp
    if start is None or end is None:
        return None
    hours = (end - start).total_seconds() / 3600
    if hours <= 0:
        return None
    total = sum(event.usage.total for event in stamped)
    return total / hours


def default_claude_provider(home: Path) -> Provider:
    return Provider("Claude Code", [home / ".claude", home / ".ai-usage-monitor" / "claude-statusline.json"])


def default_codex_provider(home: Path) -> Provider:
    return Provider("Codex", [home / ".codex" / "sessions"])


def extract_rate_limit_snapshot(path: Path, obj: dict) -> RateLimitSnapshot | None:
    extracted = extract_rate_limits(obj)
    if extracted is None:
        return None
    windows, plan_type = extracted
    timestamp = parse_timestamp_from_obj(obj)
    return RateLimitSnapshot(str(path), timestamp, windows, plan_type)


def parse_timestamp_from_obj(obj: dict):
    timestamp = obj.get("timestamp")
    if isinstance(timestamp, str):
        return parse_timestamp(timestamp)
    payload = obj.get("payload")
    if isinstance(payload, dict):
        timestamp = payload.get("timestamp")
        if isinstance(timestamp, str):
            return parse_timestamp(timestamp)
    return None


def latest_rate_limit_snapshot(snapshots: list[RateLimitSnapshot]) -> RateLimitSnapshot | None:
    if not snapshots:
        return None
    return max(snapshots, key=lambda snapshot: snapshot.timestamp or utc_now())


def with_predicted_end(
    latest_snapshot: RateLimitSnapshot,
    snapshots: list[RateLimitSnapshot],
) -> list[RateLimitWindow]:
    windows: list[RateLimitWindow] = []
    for window in latest_snapshot.windows:
        predicted_end = predict_window_end(window, latest_snapshot, snapshots)
        windows.append(
            RateLimitWindow(
                name=window.name,
                used_percent=window.used_percent,
                window_minutes=window.window_minutes,
                resets_at=window.resets_at,
                predicted_end=predicted_end,
            )
        )
    return windows


def predict_window_end(
    latest_window: RateLimitWindow,
    latest_snapshot: RateLimitSnapshot,
    snapshots: list[RateLimitSnapshot],
) -> datetime | None:
    if latest_snapshot.timestamp is None or latest_window.used_percent >= 100:
        return None
    candidates: list[tuple[datetime, RateLimitWindow]] = []
    for snapshot in snapshots:
        if snapshot.timestamp is None or snapshot.timestamp >= latest_snapshot.timestamp:
            continue
        for window in snapshot.windows:
            if window.name != latest_window.name:
                continue
            if not same_reset_window(window, latest_window):
                continue
            candidates.append((snapshot.timestamp, window))
    if not candidates:
        return None

    increasing_candidates = [
        (timestamp, window)
        for timestamp, window in candidates
        if window.used_percent < latest_window.used_percent
    ]
    if not increasing_candidates:
        return None

    previous_timestamp, previous_window = max(increasing_candidates, key=lambda item: item[0])
    elapsed_minutes = (latest_snapshot.timestamp - previous_timestamp).total_seconds() / 60
    if elapsed_minutes <= 0:
        return None
    percent_per_minute = (latest_window.used_percent - previous_window.used_percent) / elapsed_minutes
    if percent_per_minute <= 0:
        return None
    minutes_to_end = latest_window.remaining_percent / percent_per_minute
    return latest_snapshot.timestamp + timedelta(minutes=minutes_to_end)


def same_reset_window(left: RateLimitWindow, right: RateLimitWindow) -> bool:
    if left.resets_at is None or right.resets_at is None:
        return True
    return left.resets_at == right.resets_at
