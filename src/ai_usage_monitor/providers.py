from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median

from .models import ProviderSummary, RateLimitWindow, TokenUsage, UsageEvent, utc_now
from .parser import extract_event, extract_rate_limits, parse_json_file, parse_json_lines, parse_timestamp
from .pricing import estimate_cost

CODEX_PROJECTION_MIN_STALE_SECONDS = 180
CODEX_PROJECTION_MIN_UNREFLECTED_PERCENT = 0.5
CODEX_PROJECTION_MAX_ADDED_PERCENT = 15.0
CLAUDE_ONLINE_USAGE_SOURCE = "Claude online usage endpoint"


@dataclass
class Provider:
    name: str
    default_roots: list[Path]

    def collect(
        self,
        roots: list[Path] | None = None,
        *,
        full_scan: bool = False,
        latest_files: int = 5,
    ) -> tuple[list[UsageEvent], int, list[str], list[RateLimitSnapshot]]:
        scan_roots = roots or self.default_roots
        events: list[UsageEvent] = []
        seen_events: set[tuple] = set()
        rate_limit_snapshots: list[RateLimitSnapshot] = []
        files = 0
        notes: list[str] = []
        for root in scan_roots:
            if not root.exists():
                if self.name.lower() == "claude code" and root.name == "claude-statusline.json":
                    notes.append("Claude statusline capture is not configured")
                elif self.name.lower() == "claude code" and root.name == "claude-statusline-history.jsonl":
                    continue
                else:
                    notes.append(f"path not found: {root}")
                continue
            for path in iter_log_files(root, full_scan=full_scan, latest_files=latest_files):
                files += 1
                parser = parse_json_lines if path.suffix.lower() == ".jsonl" else parse_json_file
                for obj in parser(path):
                    event = extract_event(self.name, path, obj)
                    if event is not None:
                        event_key = usage_event_key(event)
                        if event_key not in seen_events:
                            seen_events.add(event_key)
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
    capture_status: dict | None = None


def usage_event_key(event: UsageEvent) -> tuple:
    usage = event.usage
    raw = event.raw
    message = raw.get("message") if isinstance(raw, dict) else None
    message_id = message.get("id") if isinstance(message, dict) else None
    request_id = raw.get("requestId") if isinstance(raw, dict) else None
    if message_id or request_id:
        return (
            event.provider,
            message_id,
            request_id,
            usage.input_tokens,
            usage.output_tokens,
            usage.cache_creation_input_tokens,
            usage.cache_read_input_tokens,
        )
    return (
        event.provider,
        event.timestamp,
        event.model,
        usage.input_tokens,
        usage.output_tokens,
        usage.cache_creation_input_tokens,
        usage.cache_read_input_tokens,
    )


def iter_log_files(root: Path, *, full_scan: bool = False, latest_files: int = 5):
    if root.is_file():
        if root.suffix.lower() in {".json", ".jsonl"}:
            yield root
        return
    try:
        paths = [
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in {".json", ".jsonl"}
        ]
    except OSError:
        return
    if full_scan:
        for path in paths:
            yield path
        return
    paths.sort(key=lambda path: safe_mtime(path), reverse=True)
    for path in paths[:latest_files]:
        yield path


def safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def summarize(
    name: str,
    events: list[UsageEvent],
    files: int,
    limit_tokens: int | None,
    notes: list[str],
    rate_limit_snapshots: list[RateLimitSnapshot] | None = None,
) -> ProviderSummary:
    if name.lower() == "claude code":
        events = active_claude_events(events)
    usage = TokenUsage()
    cost = 0.0
    last_activity = None
    for event in events:
        usage.add(event.usage)
        cost += estimate_cost(event.model, event.usage)
        if event.timestamp and (last_activity is None or event.timestamp > last_activity):
            last_activity = event.timestamp
    latest_cumulative = latest_cumulative_token_event(events) if name.lower() == "codex" else None
    if latest_cumulative is not None:
        usage = latest_cumulative.usage

    remaining = None
    if limit_tokens is not None:
        remaining = max(limit_tokens - usage.total, 0)

    burn_events = events
    if latest_cumulative is not None:
        burn_events = [event for event in events if event.source == latest_cumulative.source]
        burn = cumulative_burn_rate(burn_events)
    else:
        burn = burn_rate(burn_events)
    exhaustion = None
    if remaining is not None and burn and burn > 0:
        exhaustion = utc_now() + timedelta(hours=remaining / burn)

    summary_notes = list(notes)
    if not events:
        summary_notes.append("no observable token usage found")

    latest_rate_limits = latest_rate_limit_snapshot(name, rate_limit_snapshots or [])
    rate_limits = with_predicted_end(latest_rate_limits, rate_limit_snapshots or []) if latest_rate_limits else []
    if name.lower() == "codex" and rate_limits:
        rate_limits = with_codex_projection(rate_limits, events)
    plan_type = latest_rate_limits.plan_type if latest_rate_limits else None
    limit_source = latest_rate_limits.source if latest_rate_limits else None
    token_source = token_source_for_summary(name, latest_cumulative, events)
    snapshot_timestamp = latest_rate_limits.timestamp if latest_rate_limits else None
    source_status = build_source_status(name, latest_rate_limits, rate_limit_snapshots or [], latest_cumulative, events)
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
        token_source=token_source,
        snapshot_timestamp=snapshot_timestamp,
        source_status=source_status,
        notes=summary_notes,
    )


def token_source_for_summary(
    name: str,
    latest_cumulative: UsageEvent | None,
    events: list[UsageEvent],
) -> str | None:
    if name.lower() == "codex" and latest_cumulative is not None:
        return latest_cumulative.source
    latest = latest_event(events)
    return latest.source if latest is not None else None


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


def build_source_status(
    name: str,
    latest_rate_limits: RateLimitSnapshot | None,
    rate_limit_snapshots: list[RateLimitSnapshot],
    latest_cumulative: UsageEvent | None,
    events: list[UsageEvent],
) -> str | None:
    if name.lower() == "claude code":
        return claude_source_status(latest_rate_limits, rate_limit_snapshots, events)
    if name.lower() == "codex":
        return codex_source_status(latest_rate_limits, rate_limit_snapshots, latest_cumulative, events)
    return None


def claude_source_status(
    latest_rate_limits: RateLimitSnapshot | None,
    rate_limit_snapshots: list[RateLimitSnapshot],
    events: list[UsageEvent],
) -> str | None:
    parts: list[str] = []
    if latest_rate_limits is None:
        parts.append("rate_limits missing")
    else:
        status = latest_rate_limits.capture_status or {}
        rate_limits = status.get("rate_limits", "unknown")
        context = status.get("context_window", "missing")
        parts.append(f"rate_limits {rate_limits}")
        if has_reset_passed(latest_rate_limits):
            parts.append("quota reset passed")
            parts.append("waiting provider refresh")
        parts.append(f"quota changed {quota_change_age(latest_rate_limits, rate_limit_snapshots)}")
        parts.append(f"context {context}")
    latest_token = latest_event(events)
    if latest_token is not None:
        parts.append(f"transcript {relative_age(latest_token.timestamp)}")
    return " | ".join(parts) if parts else None


def codex_source_status(
    latest_rate_limits: RateLimitSnapshot | None,
    rate_limit_snapshots: list[RateLimitSnapshot],
    latest_cumulative: UsageEvent | None,
    events: list[UsageEvent],
) -> str | None:
    parts: list[str] = []
    if latest_rate_limits is not None:
        if has_reset_passed(latest_rate_limits):
            parts.append("quota reset passed")
            parts.append("waiting provider refresh")
        parts.append(f"quota changed {quota_change_age(latest_rate_limits, rate_limit_snapshots)}")
    latest_token = latest_cumulative or latest_event(events)
    if latest_token is not None:
        parts.append(f"token_count {relative_age(latest_token.timestamp)}")
        file_time = file_mtime(Path(latest_token.source))
        if file_time is not None:
            parts.append(f"file updated {relative_age(file_time)}")
    return " | ".join(parts) if parts else None


def with_codex_projection(
    rate_limits: list[RateLimitWindow],
    events: list[UsageEvent],
) -> list[RateLimitWindow]:
    projected: list[RateLimitWindow] = []
    for window in rate_limits:
        projection = codex_projected_used_percent(window, events)
        if projection is None:
            projected.append(window)
            continue
        projected_used, source = projection
        projected.append(
            RateLimitWindow(
                name=window.name,
                used_percent=window.used_percent,
                window_minutes=window.window_minutes,
                resets_at=window.resets_at,
                predicted_end=window.predicted_end,
                assumption=window.assumption,
                projected_used_percent=projected_used,
                projection_source=source,
            )
        )
    return projected


def codex_projected_used_percent(
    window: RateLimitWindow,
    events: list[UsageEvent],
) -> tuple[float, str] | None:
    if window.name != "5h":
        return None
    if window.assumption == "recovered" or window.used_percent >= 100:
        return window.used_percent, "quota"
    samples = codex_projection_samples(events, window.name, window.resets_at)
    if len(samples) < 3:
        return window.used_percent, "quota"
    latest = samples[-1]
    current_used = latest[2]
    current_reset = latest[3]
    if abs(current_used - window.used_percent) > 0.01:
        return window.used_percent, "quota"

    current_start_index = len(samples) - 1
    while current_start_index > 0:
        previous = samples[current_start_index - 1]
        if previous[2] != current_used or previous[3] != current_reset:
            break
        current_start_index -= 1
    current_start = samples[current_start_index]

    stale_seconds = (latest[0] - current_start[0]).total_seconds()
    if stale_seconds < CODEX_PROJECTION_MIN_STALE_SECONDS:
        return current_used, "quota"

    tokens_per_percent = stable_codex_tokens_per_percent(samples[: current_start_index + 1], current_reset)
    if tokens_per_percent is None:
        return current_used, "quota"
    if tokens_per_percent <= 0:
        return current_used, "quota"

    tokens_since_quota_change = latest[1] - current_start[1]
    if tokens_since_quota_change <= 0:
        return current_used, "quota"

    unreflected_percent = tokens_since_quota_change / tokens_per_percent
    if unreflected_percent < CODEX_PROJECTION_MIN_UNREFLECTED_PERCENT:
        return current_used, "quota"

    projected_delta = min(unreflected_percent, CODEX_PROJECTION_MAX_ADDED_PERCENT)
    projected_used = current_used + projected_delta
    projected_used = max(current_used, min(projected_used, 100.0))
    if projected_used < current_used + 0.5:
        return current_used, "quota"
    return projected_used, "token_count"


def stable_codex_tokens_per_percent(
    samples: list[tuple[datetime, int, float, datetime | None]],
    reset: datetime | None,
) -> float | None:
    ratios: list[float] = []
    latest_distinct = None
    for sample in samples:
        if sample[3] != reset:
            continue
        if latest_distinct is None:
            latest_distinct = sample
            continue
        if latest_distinct is not None and sample[2] > latest_distinct[2] and sample[1] > latest_distinct[1]:
            percent_delta = sample[2] - latest_distinct[2]
            token_delta = sample[1] - latest_distinct[1]
            if percent_delta > 0 and token_delta > 0:
                ratios.append(token_delta / percent_delta)
            latest_distinct = sample
    if not ratios:
        return None
    center = median(ratios)
    if center <= 0:
        return None
    filtered = [ratio for ratio in ratios if center * 0.25 <= ratio <= center * 4]
    return median(filtered or ratios)


def codex_projection_samples(
    events: list[UsageEvent],
    window_name: str,
    resets_at: datetime | None,
) -> list[tuple[datetime, int, float, datetime | None]]:
    samples: list[tuple[datetime, int, float, datetime | None]] = []
    for event in events:
        if event.timestamp is None or not has_total_token_usage(event.raw):
            continue
        window = codex_window_from_event(event, window_name)
        if window is None:
            continue
        if resets_at is not None and window.resets_at != resets_at:
            continue
        samples.append((event.timestamp, event.usage.total, window.used_percent, window.resets_at))
    samples.sort(key=lambda sample: sample[0])
    return samples


def codex_window_from_event(event: UsageEvent, window_name: str) -> RateLimitWindow | None:
    snapshot = extract_rate_limit_snapshot(Path(event.source), event.raw)
    if snapshot is None:
        return None
    for window in snapshot.windows:
        if window.name == window_name:
            return window
    return None


def quota_change_age(
    latest_rate_limits: RateLimitSnapshot,
    snapshots: list[RateLimitSnapshot],
) -> str:
    changed_at = quota_changed_at(latest_rate_limits, snapshots)
    if changed_at is None:
        return "unknown"
    return relative_age(changed_at)


def quota_changed_at(
    latest_rate_limits: RateLimitSnapshot,
    snapshots: list[RateLimitSnapshot],
) -> datetime | None:
    if latest_rate_limits.timestamp is None:
        return None
    latest_signature = quota_signature(latest_rate_limits)
    candidates = [
        snapshot
        for snapshot in snapshots
        if snapshot.timestamp is not None and snapshot.timestamp <= latest_rate_limits.timestamp
    ]
    candidates.sort(key=lambda snapshot: snapshot.timestamp or utc_now(), reverse=True)
    if not candidates:
        return latest_rate_limits.timestamp

    first_same = latest_rate_limits.timestamp
    saw_latest = False
    for snapshot in candidates:
        if quota_signature(snapshot) == latest_signature:
            first_same = snapshot.timestamp or first_same
            saw_latest = True
            continue
        if saw_latest:
            return first_same
    return first_same if saw_latest else latest_rate_limits.timestamp


def quota_signature(snapshot: RateLimitSnapshot) -> tuple:
    return tuple(
        sorted(
            (
                window.name,
                round(window.used_percent, 3),
                window.resets_at,
            )
            for window in snapshot.windows
        )
    )


def has_reset_passed(snapshot: RateLimitSnapshot) -> bool:
    now = utc_now()
    return any(
        window.resets_at is not None and window.resets_at.astimezone(timezone.utc) <= now
        for window in snapshot.windows
    )


def latest_event(events: list[UsageEvent]) -> UsageEvent | None:
    stamped = [event for event in events if event.timestamp is not None]
    if not stamped:
        return None
    return max(stamped, key=lambda event: event.timestamp or utc_now())


def active_claude_events(events: list[UsageEvent]) -> list[UsageEvent]:
    latest = latest_event(events)
    if latest is None:
        return events
    return [event for event in events if event.source == latest.source]


def file_mtime(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


def relative_age(value: datetime | None) -> str:
    if value is None:
        return "unavailable"
    seconds = max(0, int((utc_now() - value.astimezone(timezone.utc)).total_seconds()))
    if seconds < 60:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    return f"{hours}h {minutes % 60}m ago"


def cumulative_burn_rate(events: list[UsageEvent]) -> float | None:
    stamped = [event for event in events if event.timestamp is not None and has_total_token_usage(event.raw)]
    if len(stamped) < 2:
        return None
    stamped.sort(key=lambda event: event.timestamp)
    start = stamped[0]
    end = stamped[-1]
    if start.timestamp is None or end.timestamp is None:
        return None
    hours = (end.timestamp - start.timestamp).total_seconds() / 3600
    if hours <= 0:
        return None
    delta = end.usage.total - start.usage.total
    if delta <= 0:
        return None
    return delta / hours


def latest_cumulative_token_event(events: list[UsageEvent]) -> UsageEvent | None:
    cumulative = [event for event in events if has_total_token_usage(event.raw)]
    if not cumulative:
        return None
    return max(cumulative, key=lambda event: event.timestamp or utc_now())


def has_total_token_usage(obj: dict) -> bool:
    if obj.get("type") != "event_msg":
        return False
    payload = obj.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "token_count":
        return False
    info = payload.get("info")
    return isinstance(info, dict) and isinstance(info.get("total_token_usage"), dict)


def default_claude_provider(home: Path) -> Provider:
    usage_dir = home / ".ai-usage-monitor"
    return Provider(
        "Claude Code",
        [
            usage_dir / "claude-statusline.json",
            usage_dir / "claude-statusline-history.jsonl",
            home / ".claude" / "projects",
        ],
    )


def default_codex_provider(home: Path) -> Provider:
    return Provider("Codex", [home / ".codex" / "sessions"])


def extract_rate_limit_snapshot(path: Path, obj: dict) -> RateLimitSnapshot | None:
    extracted = extract_rate_limits(obj)
    if extracted is None:
        return None
    windows, plan_type = extracted
    timestamp = parse_timestamp_from_obj(obj)
    capture_status = obj.get("capture_status")
    return RateLimitSnapshot(
        str(path),
        timestamp,
        windows,
        plan_type,
        capture_status if isinstance(capture_status, dict) else None,
    )


def parse_timestamp_from_obj(obj: dict):
    captured_at = obj.get("captured_at")
    if isinstance(captured_at, str):
        return parse_timestamp(captured_at)
    timestamp = obj.get("timestamp")
    if isinstance(timestamp, str):
        return parse_timestamp(timestamp)
    payload = obj.get("payload")
    if isinstance(payload, dict):
        timestamp = payload.get("timestamp")
        if isinstance(timestamp, str):
            return parse_timestamp(timestamp)
    return None


def latest_rate_limit_snapshot(name: str, snapshots: list[RateLimitSnapshot]) -> RateLimitSnapshot | None:
    if not snapshots:
        return None
    if name.lower() == "claude code":
        online_snapshots = [
            snapshot for snapshot in snapshots if snapshot.source == CLAUDE_ONLINE_USAGE_SOURCE
        ]
        if online_snapshots:
            return max(online_snapshots, key=lambda snapshot: snapshot.timestamp or utc_now())
    return max(snapshots, key=lambda snapshot: snapshot.timestamp or utc_now())


def with_predicted_end(
    latest_snapshot: RateLimitSnapshot,
    snapshots: list[RateLimitSnapshot],
) -> list[RateLimitWindow]:
    windows: list[RateLimitWindow] = []
    for window in latest_snapshot.windows:
        if reset_has_passed(window):
            windows.append(
                RateLimitWindow(
                    name=window.name,
                    used_percent=1.0,
                    window_minutes=window.window_minutes,
                    resets_at=window.resets_at,
                    predicted_end=None,
                    assumption="recovered",
                )
            )
            continue
        predicted_end = predict_window_end(window, latest_snapshot, snapshots)
        windows.append(
            RateLimitWindow(
                name=window.name,
                used_percent=window.used_percent,
                window_minutes=window.window_minutes,
                resets_at=window.resets_at,
                predicted_end=predicted_end,
                assumption=window.assumption,
            )
        )
    return windows


def reset_has_passed(window: RateLimitWindow) -> bool:
    if window.resets_at is None:
        return False
    return window.resets_at.astimezone(timezone.utc) <= utc_now()


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
