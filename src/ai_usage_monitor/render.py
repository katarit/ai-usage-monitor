from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime

from .models import ProviderSummary, RateLimitWindow


BAR_WIDTH = 34


def render_text(
    summaries: list[ProviderSummary],
    watch: bool = False,
    refresh: int | None = None,
    details: bool = False,
) -> str:
    blocks = [render_header(watch, refresh)]
    max_observed = max((summary.usage.total for summary in summaries), default=0)
    for summary in summaries:
        blocks.append(render_summary(summary, max_observed, details))
    return "\n\n".join(blocks)


def render_json(summaries: list[ProviderSummary]) -> str:
    return json.dumps([asdict(summary) for summary in summaries], ensure_ascii=False, indent=2, default=str)


def render_header(watch: bool, refresh: int | None) -> str:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    mode = "WATCH" if watch else "ONCE"
    refresh_text = f"refresh: {refresh}s" if watch and refresh is not None else "refresh: off"
    return "\n".join(
        [
            "AI LIMIT MONITOR",
            "=" * 76,
            f"mode: {mode}    updated: {now}    {refresh_text}",
            "rate limit percentages use the latest local provider snapshot when available",
        ]
    )


def render_summary(summary: ProviderSummary, max_observed: int, details: bool = False) -> str:
    usage = summary.usage
    used_percent = percent_used(summary)
    remaining_percent = None if used_percent is None else max(100.0 - used_percent, 0.0)
    burn_per_minute = None if summary.burn_tokens_per_hour is None else summary.burn_tokens_per_hour / 60
    status = status_for_summary(used_percent, burn_per_minute)

    if summary.rate_limits:
        status = status_for_rate_limits(summary.rate_limits)
        title = summary.name.upper()
        if summary.plan_type:
            title = f"{title}    plan: {summary.plan_type}"
        lines = [title, "-" * 76, f"Status      {status}"]
        lines.extend(render_rate_limits(summary.rate_limits))
        lines.append(f"Predicted End {fmt_predicted_end(summary.rate_limits)}")
        lines.append(f"Burn Rate   {fmt_float(burn_per_minute)} tokens/min")
        lines.append(
            f"Breakdown   in {usage.input_tokens:,} | out {usage.output_tokens:,} | "
            f"cache new {usage.cache_creation_input_tokens:,} | cache read {usage.cache_read_input_tokens:,}"
        )
        lines.append(f"Tokens      {usage.total:,} observed")
        lines.append(f"Activity    last {fmt_time(summary.last_activity)} | events {summary.events:,}")
        if details:
            lines.append(f"Files       {summary.files:,}")
            lines.append(f"Source      {summary.limit_source or 'unavailable'}")
        if summary.notes:
            lines.append("Notes")
            lines.extend(f"  - {note}" for note in summary.notes)
        return "\n".join(lines)

    lines = [summary.name.upper(), "-" * 76, f"Status       {status}"]
    if used_percent is None:
        lines.extend(
            [
                f"Observed     {observed_bar(usage.total, max_observed)}  {usage.total:,} tokens",
                "Quota        subscription limit unknown",
                "Remaining    official remaining unavailable",
            ]
        )
    else:
        lines.extend(
            [
                f"Usage        {progress_bar(used_percent)}  {fmt_percent(used_percent)} used / {fmt_percent(remaining_percent)} left",
                f"Tokens       {usage.total:,} / {fmt_int(summary.limit_tokens)}  ({fmt_int(summary.remaining_tokens)} left)",
            ]
        )
    lines.extend(
        [
            f"Breakdown    in {usage.input_tokens:,} | out {usage.output_tokens:,} | cache new {usage.cache_creation_input_tokens:,} | cache read {usage.cache_read_input_tokens:,}",
            f"Burn Rate    {fmt_float(burn_per_minute)} tokens/min",
            f"Cost         {fmt_cost(summary)}",
            f"End          {fmt_dt(summary.estimated_exhaustion)}",
            f"Activity     {fmt_dt(summary.last_activity)}    events: {summary.events}    files: {summary.files}",
        ]
    )
    if summary.notes:
        lines.append("Notes")
        lines.extend(f"  - {note}" for note in summary.notes)
    return "\n".join(lines)


def render_rate_limits(rate_limits: list[RateLimitWindow]) -> list[str]:
    lines = []
    for window in sorted(rate_limits, key=rate_limit_sort_key):
        lines.append(
            f"{display_window_name(window):<11} {remaining_bar(window.remaining_percent)}  "
            f"{window.remaining_percent:.0f}% left   {window.used_percent:.0f}% used   "
            f"reset {fmt_reset(window.resets_at)}"
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
    if local.date() == now.date():
        return local.strftime("%H:%M")
    return local.strftime("%b %d")


def fmt_predicted_end(rate_limits: list[RateLimitWindow]) -> str:
    for window in sorted(rate_limits, key=rate_limit_sort_key):
        if window.predicted_end is None:
            continue
        if window.resets_at is not None and window.predicted_end > window.resets_at:
            return f"{display_window_name(window)}: after reset"
        return f"{display_window_name(window)}: {fmt_reset(window.predicted_end)}"
    return "unavailable"
