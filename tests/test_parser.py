from pathlib import Path
from datetime import timedelta

from ai_usage_monitor.codex_reset_credits import credits_from_cache, sanitize_response
from ai_usage_monitor.models import RateLimitWindow, ResetCredit
from ai_usage_monitor.providers import Provider, RateLimitSnapshot, summarize
from ai_usage_monitor.models import utc_now
from ai_usage_monitor.parser import extract_event
from ai_usage_monitor.providers import extract_rate_limit_snapshot
from ai_usage_monitor.render import render_text, status_for_rate_limits


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


def test_claude_online_quota_takes_priority_over_newer_statusline_snapshot():
    now = utc_now()
    stale_statusline = RateLimitSnapshot(
        "claude-statusline.json",
        now,
        [
            RateLimitWindow("5h", 1.0, resets_at=now - timedelta(minutes=1)),
            RateLimitWindow("week", 0.0, resets_at=now + timedelta(days=1)),
        ],
        None,
        {"rate_limits": "fresh", "context_window": "fresh"},
    )
    online_quota = RateLimitSnapshot(
        "Claude online usage endpoint",
        now - timedelta(seconds=30),
        [
            RateLimitWindow("5h", 65.0, resets_at=now + timedelta(hours=4)),
            RateLimitWindow("week", 7.0, resets_at=now + timedelta(days=1)),
        ],
        "pro",
        {"rate_limits": "online", "context_window": "local transcript"},
    )

    summary = summarize("Claude Code", [], 0, None, [], [stale_statusline, online_quota])

    assert summary.limit_source == "Claude online usage endpoint"
    assert summary.rate_limits[0].used_percent == 65.0
    assert summary.rate_limits[0].assumption is None
    assert summary.plan_type == "pro"


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


def test_stale_claude_rate_limit_snapshot_renders_stale_status():
    old = utc_now() - timedelta(hours=2)
    snapshot = RateLimitSnapshot(
        "claude-statusline.json",
        old,
        [
            RateLimitWindow("5h", 1.0, resets_at=old - timedelta(minutes=1)),
            RateLimitWindow("week", 51.0, resets_at=utc_now() + timedelta(days=1)),
        ],
        None,
        {"rate_limits": "fresh", "context_window": "fresh"},
    )
    summary = summarize("Claude Code", [], 1, None, [], [snapshot])
    output = render_text([summary], details=True, color=False)

    assert "Status      STALE SNAPSHOT" in output
    assert "rate_limits stale" in (summary.source_status or "")


def test_codex_reset_credits_render_as_supplemental_line():
    now = utc_now()
    snapshot = RateLimitSnapshot(
        "session.jsonl",
        now,
        [RateLimitWindow("5h", 42.0, resets_at=now + timedelta(hours=1))],
        "plus",
    )
    summary = summarize("Codex", [], 0, None, [], [snapshot])
    summary.reset_credits = [
        ResetCredit("available", granted_at=now - timedelta(days=1), expires_at=now + timedelta(days=1)),
        ResetCredit("redeemed", granted_at=now - timedelta(days=2), expires_at=now + timedelta(days=2)),
    ]
    output = render_text([summary], details=True, color=False)

    assert "Reset Credits 1 available" in output
    assert "  #1  granted " in output
    assert " | expires " in output


def test_codex_reset_credit_cache_sanitizes_private_fields():
    response = {
        "available_count": 1,
        "total_earned_count": 2,
        "credits": [
            {
                "id": "secret-id",
                "status": "available",
                "expires_at": "2026-07-27T00:01:17Z",
                "profile_user_id": "private-user",
                "profile_image_url": "https://example.invalid/private.png",
            }
        ],
    }
    sanitized = sanitize_response(response)
    cache = {"fetched_at": utc_now().isoformat(), "response": sanitized}
    credits = credits_from_cache(cache)

    assert "id" not in sanitized["credits"][0]
    assert "profile_user_id" not in sanitized["credits"][0]
    assert len(credits) == 1
    assert credits[0].status == "available"


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
    test_claude_online_quota_takes_priority_over_newer_statusline_snapshot()
    test_codex_projects_quota_when_token_count_moves_before_quota_refresh()
    test_codex_projection_stays_on_quota_before_stale_threshold()
    test_codex_projection_caps_token_count_adjustment()
    test_stale_claude_rate_limit_snapshot_renders_stale_status()
    test_codex_reset_credits_render_as_supplemental_line()
    test_codex_reset_credit_cache_sanitizes_private_fields()
    print("ok")
