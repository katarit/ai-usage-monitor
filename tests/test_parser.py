from pathlib import Path
from datetime import timedelta

from ai_usage_monitor.providers import Provider, summarize
from ai_usage_monitor.models import utc_now
from ai_usage_monitor.parser import extract_event
from ai_usage_monitor.providers import extract_rate_limit_snapshot
from ai_usage_monitor.render import status_for_rate_limits


def test_claude_fixture_collects_usage():
    provider = Provider("Claude Code", [Path("tests/fixtures/claude")])
    events, files, notes, rate_limits = provider.collect()
    summary = summarize("Claude Code", events, files, 10_000, notes, rate_limits)

    assert files == 1
    assert summary.events == 2
    assert summary.usage.input_tokens == 2_000
    assert summary.usage.output_tokens == 500
    assert summary.usage.cache_creation_input_tokens == 100
    assert summary.usage.cache_read_input_tokens == 450
    assert summary.remaining_tokens == 6_950


def test_codex_fixture_collects_usage():
    provider = Provider("Codex", [Path("tests/fixtures/codex")])
    events, files, notes, rate_limits = provider.collect()
    summary = summarize("Codex", events, files, 10_000, notes, rate_limits)

    assert files == 1
    assert summary.events == 3
    assert summary.usage.input_tokens == 3_000
    assert summary.usage.output_tokens == 750
    assert summary.usage.cache_read_input_tokens == 300
    assert summary.remaining_tokens == 5_950
    assert summary.rate_limits[0].used_percent == 1.0
    assert summary.rate_limits[0].assumption == "recovered"
    assert summary.rate_limits[1].used_percent == 14.0
    assert summary.plan_type == "plus"


def test_claude_statusline_fixture_collects_rate_limits():
    provider = Provider("Claude Code", [Path("tests/fixtures/claude-statusline.json")])
    events, files, notes, rate_limits = provider.collect()
    summary = summarize("Claude Code", events, files, None, notes, rate_limits)

    assert files == 1
    assert summary.events == 1
    assert summary.usage.input_tokens == 8_500
    assert summary.usage.output_tokens == 1_200
    assert summary.usage.cache_creation_input_tokens == 300
    assert summary.usage.cache_read_input_tokens == 4_200
    assert summary.rate_limits[0].used_percent == 1.0
    assert summary.rate_limits[0].assumption == "recovered"
    assert summary.rate_limits[1].used_percent == 50.0
    assert summary.plan_type is None


def test_codex_projects_quota_when_token_count_moves_before_quota_refresh():
    reset = int((utc_now() + timedelta(hours=4)).timestamp())
    rows = [
        codex_token_count_row("2026-06-04T01:00:00Z", 1_000, 10.0, reset),
        codex_token_count_row("2026-06-04T01:10:00Z", 2_000, 20.0, reset),
        codex_token_count_row("2026-06-04T01:20:00Z", 3_000, 30.0, reset),
        codex_token_count_row("2026-06-04T01:30:00Z", 4_000, 40.0, reset),
        codex_token_count_row("2026-06-04T01:36:00Z", 4_600, 40.0, reset),
    ]
    events = [extract_event("Codex", Path("session.jsonl"), row) for row in rows]
    snapshots = [extract_rate_limit_snapshot(Path("session.jsonl"), row) for row in rows]
    summary = summarize(
        "Codex",
        [event for event in events if event is not None],
        1,
        None,
        [],
        [snapshot for snapshot in snapshots if snapshot is not None],
    )

    five_hour = summary.rate_limits[0]
    assert five_hour.used_percent == 40.0
    assert five_hour.projected_used_percent == 46.0
    assert five_hour.projection_source == "token_count"
    assert summary.source_status is not None
    assert "projected from token_count" not in summary.source_status
    assert status_for_rate_limits(summary.rate_limits) == "OK"


def test_codex_projection_stays_on_quota_before_stale_threshold():
    reset = int((utc_now() + timedelta(hours=4)).timestamp())
    rows = [
        codex_token_count_row("2026-06-04T01:00:00Z", 1_000, 10.0, reset),
        codex_token_count_row("2026-06-04T01:10:00Z", 2_000, 20.0, reset),
        codex_token_count_row("2026-06-04T01:20:00Z", 3_000, 30.0, reset),
        codex_token_count_row("2026-06-04T01:21:00Z", 3_800, 30.0, reset),
    ]
    events = [extract_event("Codex", Path("session.jsonl"), row) for row in rows]
    snapshots = [extract_rate_limit_snapshot(Path("session.jsonl"), row) for row in rows]
    summary = summarize(
        "Codex",
        [event for event in events if event is not None],
        1,
        None,
        [],
        [snapshot for snapshot in snapshots if snapshot is not None],
    )

    five_hour = summary.rate_limits[0]
    assert five_hour.used_percent == 30.0
    assert five_hour.projected_used_percent == 30.0
    assert five_hour.projection_source == "quota"


def test_codex_projection_caps_token_count_adjustment():
    reset = int((utc_now() + timedelta(hours=4)).timestamp())
    rows = [
        codex_token_count_row("2026-06-04T01:00:00Z", 1_000, 10.0, reset),
        codex_token_count_row("2026-06-04T01:10:00Z", 2_000, 20.0, reset),
        codex_token_count_row("2026-06-04T01:20:00Z", 3_000, 30.0, reset),
        codex_token_count_row("2026-06-04T01:30:00Z", 4_000, 40.0, reset),
        codex_token_count_row("2026-06-04T01:36:00Z", 10_000, 40.0, reset),
    ]
    events = [extract_event("Codex", Path("session.jsonl"), row) for row in rows]
    snapshots = [extract_rate_limit_snapshot(Path("session.jsonl"), row) for row in rows]
    summary = summarize(
        "Codex",
        [event for event in events if event is not None],
        1,
        None,
        [],
        [snapshot for snapshot in snapshots if snapshot is not None],
    )

    five_hour = summary.rate_limits[0]
    assert five_hour.used_percent == 40.0
    assert five_hour.projected_used_percent == 55.0
    assert five_hour.projection_source == "token_count"


def codex_token_count_row(timestamp: str, total_tokens: int, used_percent: float, reset: int):
    return {
        "timestamp": timestamp,
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {
                "total_token_usage": {
                    "input_tokens": total_tokens,
                    "cached_input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": total_tokens,
                }
            },
        },
        "rate_limits": {
            "primary": {
                "used_percent": used_percent,
                "window_minutes": 300,
                "resets_at": reset,
            },
            "plan_type": "plus",
        },
    }


if __name__ == "__main__":
    test_claude_fixture_collects_usage()
    test_codex_fixture_collects_usage()
    test_claude_statusline_fixture_collects_rate_limits()
    test_codex_projects_quota_when_token_count_moves_before_quota_refresh()
    test_codex_projection_stays_on_quota_before_stale_threshold()
    test_codex_projection_caps_token_count_adjustment()
    print("ok")
