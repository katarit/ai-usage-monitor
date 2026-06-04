from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime

from .models import ProviderSummary, RateLimitWindow, TokenUsage


BAR_WIDTH = 34


def render_text(
    summaries: list[ProviderSummary],
    watch: bool = False,
    refresh: int | None = None,
    refresh_label: str | None = None,
    details: bool = False,
    color: bool = False,
) -> str:
    blocks = [render_header(watch, refresh, refresh_label, color)]
    max_observed = max((summary.usage.total for summary in summaries), default=0)
    for summary in summaries:
        blocks.append(render_summary(summary, max_observed, details, color))
    return "\n\n".join(blocks)


def render_json(summaries: list[ProviderSummary]) -> str:
    return json.dumps([asdict(summary) for summary in summaries], ensure_ascii=False, indent=2, default=str)


def render_header(watch: bool, refresh: int | None, refresh_label: str | None = None, color: bool = False) -> str:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    mode = "WATCH" if watch else "ONCE"
    label = f" {refresh_label}" if watch and refresh_label else ""
    refresh_text = f"refresh: {refresh}s{label}" if watch and refresh is not None else "refresh: off"
    return "\n".join(
        [
            paint("AI LIMIT MONITOR", "bold", color),
            "=" * 76,
            f"mode: {mode}    updated: {now}    {refresh_text}",
            "rate limit percentages use the freshest provider quota snapshot available",
        ]
    )


def render_summary(summary: ProviderSummary, max_observed: int, details: bool = False, color: bool = False) -> str:
    usage = summary.usage
    used_percent = percent_used(summary)
    remaining_percent = None if used_percent is None else max(100.0 - used_percent, 0.0)
    burn_per_minute = None if summary.burn_tokens_per_hour is None else summary.burn_tokens_per_hour / 60
    status = status_for_summary(used_percent, burn_per_minute)

    if summary.rate_limits:
        status = status_for_rate_limits(summary.rate_limits)
        if is_stale(summary.snapshot_timestamp) and status != "ASSUMED RECOVERED":
            status = "STALE SNAPSHOT"
        title = summary.name.upper()
        if summary.plan_type:
            title = f"{title}    plan: {summary.plan_type}"
        lines = [paint(title, "bold", color), "-" * 76, f"Status      {paint_status(status, color)}"]
        lines.extend(render_rate_limits(summary.rate_limits, color))
        lines.append(f"Predicted End {fmt_predicted_end(summary.rate_limits)}")
        lines.append(f"Burn Rate   {paint_token_value(fmt_float(burn_per_minute), color)} tokens/min")
        lines.append(render_breakdown(usage, color))
        lines.append(f"Tokens      {paint_token_value(f'{usage.total:,}', color)} observed")
        lines.append(f"Activity    last {fmt_time(summary.last_activity)} | events {summary.events:,}")
        lines.append(f"Quota Read  {paint_age(fmt_age(summary.snapshot_timestamp), color)}")
        if summary.source_status:
            lines.append(f"Source      {paint_source_status(summary.source_status, color)}")
        if details:
            lines.append(f"Files       {summary.files:,}")
            lines.append(f"Limit File  {summary.limit_source or 'unavailable'}")
            lines.append(f"Token File  {summary.token_source or 'unavailable'}")
        if summary.notes:
            lines.append("Notes")
            lines.extend(f"  - {note}" for note in summary.notes)
        return "\n".join(lines)

    if summary.name.lower() == "claude code" and summary.usage.total == 0:
        lines = [
            paint(summary.name.upper(), "bold", color),
            "-" * 76,
            f"Status      {paint_status('NEEDS STATUSLINE CAPTURE', color)}",
            "5 hours     unavailable",
            "1 week      unavailable",
            "Predicted End unavailable",
            f"Burn Rate   {paint_token_value('unavailable', color)} tokens/min",
            "Breakdown   in 0 | out 0 | cache new 0 | cache read 0",
            f"Tokens      {paint_token_value('0', color)} observed",
            "Snapshot    unavailable",
        ]
        if details and summary.limit_source:
            lines.append(f"Source      {summary.limit_source}")
        if details and summary.notes:
            lines.append("Notes")
            lines.extend(f"  - {note}" for note in summary.notes)
        return "\n".join(lines)

    lines = [paint(summary.name.upper(), "bold", color), "-" * 76, f"Status       {paint_status(status, color)}"]
    if used_percent is None:
        lines.extend(
            [
                f"Observed     {observed_bar(usage.total, max_observed)}  {paint_token_value(f'{usage.total:,}', color)} tokens",
                "Quota        subscription limit unknown",
                "Remaining    official remaining unavailable",
            ]
        )
    else:
        lines.extend(
            [
                f"Usage        {progress_bar(used_percent)}  {fmt_percent(used_percent)} used / {fmt_percent(remaining_percent)} left",
                f"Tokens       {paint_token_value(f'{usage.total:,}', color)} / {fmt_int(summary.limit_tokens)}  ({fmt_int(summary.remaining_tokens)} left)",
            ]
        )
    lines.extend(
        [
            render_breakdown(usage, color, label="Breakdown   "),
            f"Burn Rate    {paint_token_value(fmt_float(burn_per_minute), color)} tokens/min",
            f"Cost         {fmt_cost(summary)}",
            f"End          {fmt_dt(summary.estimated_exhaustion)}",
            f"Activity     {fmt_dt(summary.last_activity)}    events: {summary.events}    files: {summary.files}",
        ]
    )
    if summary.notes:
        lines.append("Notes")
        lines.extend(f"  - {note}" for note in summary.notes)
    return "\n".join(lines)


def render_rate_limits(rate_limits: list[RateLimitWindow], color: bool = False) -> list[str]:
    lines = []
    for window in sorted(rate_limits, key=rate_limit_sort_key):
        prefix = "~" if window.assumption == "recovered" else ""
        lines.append(
            f"{display_window_name(window):<11} {remaining_bar(window.remaining_percent)}  "
            f"{prefix}{window.remaining_percent:.0f}% left   {prefix}{window.used_percent:.0f}% used   "
            f"reset {fmt_reset(window.resets_at)}"
        )
        if window.projected_used_percent is not None and window.projected_remaining_percent is not None:
            projected_left = f"~{window.projected_remaining_percent:.0f}% left"
            projected_used = f"~{window.projected_used_percent:.0f}% used"
            projected_label = f"{'Projected':<11}"
            projected_from_tokens = window.projection_source == "token_count"
            projected_source = f"from {window.projection_source or 'token_count'}"
            lines.append(
                f"{paint_projected_label(projected_label, color)} {remaining_bar(window.projected_remaining_percent)}  "
                f"{paint_projected(projected_left, color, projected_from_tokens)}   "
                f"{paint_projected(projected_used, color, projected_from_tokens)}   "
                f"{paint_projected(projected_source, color, projected_from_tokens)}"
            )
    return lines


def rate_limit_sort_key(window: RateLimitWindow) -> int:
    if window.name == "5h":
        return 0
    if window.name == "week":
        return 1
    return 2


def display_window_name(window: RateLimitWindow) -> str:
    if window.name == "5h":
        return "5 hours"
    if window.name == "week":
        return "1 week"
    return window.name


def percent_used(summary: ProviderSummary) -> float | None:
    if summary.limit_tokens is None or summary.limit_tokens <= 0:
        return None
    return min((summary.usage.total / summary.limit_tokens) * 100, 100.0)


def progress_bar(used_percent: float | None) -> str:
    if used_percent is None:
        return "[" + "limit not set".center(BAR_WIDTH, "-") + "]"
    used_cells = round((used_percent / 100) * BAR_WIDTH)
    used_cells = max(0, min(BAR_WIDTH, used_cells))
    return "[" + "#" * used_cells + "-" * (BAR_WIDTH - used_cells) + "]"


def remaining_bar(remaining_percent: float | None) -> str:
    if remaining_percent is None:
        return "[" + "limit not set".center(BAR_WIDTH, "-") + "]"
    remaining_cells = round((remaining_percent / 100) * BAR_WIDTH)
    remaining_cells = max(0, min(BAR_WIDTH, remaining_cells))
    return "[" + "#" * remaining_cells + "-" * (BAR_WIDTH - remaining_cells) + "]"


def observed_bar(tokens: int, max_observed: int) -> str:
    if tokens <= 0 or max_observed <= 0:
        return "[" + "-" * BAR_WIDTH + "]"
    cells = round((tokens / max_observed) * BAR_WIDTH)
    cells = max(1, min(BAR_WIDTH, cells))
    return "[" + "#" * cells + "-" * (BAR_WIDTH - cells) + "]"


def status_for_summary(used_percent: float | None, burn_per_minute: float | None) -> str:
    if used_percent is None:
        return risk_for_burn_rate(burn_per_minute)
    if used_percent >= 100:
        return "EXHAUSTED"
    if used_percent >= 90:
        return "LOW"
    if used_percent >= 70:
        return "WATCH"
    return "OK"


def status_for_rate_limits(rate_limits: list[RateLimitWindow]) -> str:
    if any(window.assumption == "recovered" for window in rate_limits):
        active_windows = [window for window in rate_limits if window.assumption != "recovered"]
        active_used = max((window.used_percent for window in active_windows), default=0.0)
        if active_used < 70:
            return "ASSUMED RECOVERED"
    if any(is_assumed_limit_hit(window) for window in rate_limits):
        return "ASSUMED LIMIT HIT"
    used_percent = max((window.used_percent for window in rate_limits), default=0.0)
    if used_percent >= 100:
        return "EXHAUSTED"
    if used_percent >= 90:
        return "LOW"
    if used_percent >= 70:
        return "WATCH"
    return "OK"


def risk_for_burn_rate(burn_per_minute: float | None) -> str:
    if burn_per_minute is None:
        return "SUBSCRIPTION LIMIT UNKNOWN"
    if burn_per_minute >= 100_000:
        return "HIGH BURN"
    if burn_per_minute >= 25_000:
        return "WATCH BURN"
    return "LOW BURN"


def is_assumed_limit_hit(window: RateLimitWindow) -> bool:
    if window.predicted_end is None or window.assumption == "recovered":
        return False
    return window.used_percent >= 90 and window.predicted_end.astimezone() <= datetime.now().astimezone()


def fmt_int(value: int | None) -> str:
    return "unconfigured" if value is None else f"{value:,}"


def fmt_float(value: float | None) -> str:
    return "unavailable" if value is None else f"{value:,.0f}"


def fmt_percent(value: float | None) -> str:
    return "unconfigured" if value is None else f"{value:.1f}%"


def fmt_cost(summary: ProviderSummary) -> str:
    if summary.estimated_cost <= 0:
        return "unavailable or included in subscription"
    return f"${summary.estimated_cost:.4f} estimated"


def fmt_dt(value: datetime | None) -> str:
    return "unavailable" if value is None else value.astimezone().isoformat(timespec="seconds")


def fmt_time(value: datetime | None) -> str:
    return "unavailable" if value is None else value.astimezone().strftime("%H:%M")


def fmt_reset(value: datetime | None) -> str:
    if value is None:
        return "unavailable"
    local = value.astimezone()
    now = datetime.now().astimezone()
    if local <= now:
        return "passed"
    if local.date() == now.date():
        return local.strftime("%H:%M")
    return local.strftime("%b %d")


def fmt_age(value: datetime | None) -> str:
    if value is None:
        return "unavailable"
    now = datetime.now().astimezone()
    local = value.astimezone()
    seconds = max(0, int((now - local).total_seconds()))
    if seconds < 60:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    return f"{hours}h {minutes % 60}m ago"


def is_stale(value: datetime | None) -> bool:
    if value is None:
        return True
    now = datetime.now().astimezone()
    local = value.astimezone()
    return (now - local).total_seconds() > 300


def fmt_predicted_end(rate_limits: list[RateLimitWindow]) -> str:
    for window in sorted(rate_limits, key=rate_limit_sort_key):
        if window.predicted_end is None:
            continue
        if window.predicted_end.astimezone() <= datetime.now().astimezone():
            if is_assumed_limit_hit(window):
                return f"{display_window_name(window)}: assumed limit hit"
            continue
        if window.resets_at is not None and window.predicted_end > window.resets_at:
            return f"{display_window_name(window)}: after reset"
        return f"{display_window_name(window)}: {fmt_reset(window.predicted_end)}"
    return "unavailable"


ANSI = {
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "cyan": "\033[36m",
    "reset": "\033[0m",
}


def paint(value: str, style: str, enabled: bool) -> str:
    if not enabled:
        return value
    return f"{ANSI[style]}{value}{ANSI['reset']}"


def paint_token_value(value: str, enabled: bool) -> str:
    if not any(char.isdigit() for char in value):
        return value
    return paint(value, "cyan", enabled)


def render_breakdown(usage: TokenUsage, enabled: bool, label: str = "Breakdown   ") -> str:
    return (
        f"{label}in {paint_token_value(f'{usage.input_tokens:,}', enabled)} | "
        f"out {paint_token_value(f'{usage.output_tokens:,}', enabled)} | "
        f"cache new {paint_token_value(f'{usage.cache_creation_input_tokens:,}', enabled)} | "
        f"cache read {paint_token_value(f'{usage.cache_read_input_tokens:,}', enabled)}"
    )


def paint_status(value: str, enabled: bool) -> str:
    if value in {"OK", "LOW BURN"}:
        return paint(value, "green", enabled)
    if value in {"WATCH", "WATCH BURN", "STALE SNAPSHOT", "ASSUMED RECOVERED"}:
        return paint(value, "yellow", enabled)
    if value in {"LOW", "HIGH BURN", "EXHAUSTED", "NEEDS STATUSLINE CAPTURE", "ASSUMED LIMIT HIT"}:
        return paint(value, "red", enabled)
    return value


def paint_projected(value: str, enabled: bool, active: bool = True) -> str:
    if not active:
        return value
    return paint(value, "cyan", enabled)


def paint_projected_label(value: str, enabled: bool) -> str:
    return paint(value, "cyan", enabled)


def paint_age(value: str, enabled: bool) -> str:
    if value == "unavailable":
        return paint(value, "dim", enabled)
    return value


def paint_source_status(value: str, enabled: bool) -> str:
    if not enabled:
        return value
    parts = []
    for part in value.split(" | "):
        if "missing" in part or "unknown" in part or "unavailable" in part:
            parts.append(paint(part, "red", enabled))
        elif part.startswith("quota changed"):
            parts.append(paint(part, "yellow", enabled))
        elif part.startswith(("quota reset passed", "waiting provider refresh")):
            parts.append(paint(part, "yellow", enabled))
        elif part.startswith(("token_count", "transcript")):
            parts.append(paint(part, "cyan", enabled))
        else:
            parts.append(part)
    return " | ".join(parts)
