from pathlib import Path
from datetime import datetime, timedelta

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


def test_claude_online_reset_jitter_keeps_prediction_and_quota_change_age():
    now = utc_now()
    reset = (now + timedelta(hours=3)).replace(second=0, microsecond=0)

    def online(minutes_ago, used, jitter_ms):
        return RateLimitSnapshot(
            "Claude online usage endpoint",
            now - timedelta(minutes=minutes_ago),
            [
                RateLimitWindow("5h", used, resets_at=reset + timedelta(milliseconds=jitter_ms)),
                RateLimitWindow("week", 60.0, resets_at=reset + timedelta(days=2, milliseconds=jitter_ms)),
            ],
            "pro",
            {"rate_limits": "online", "context_window": "local transcript"},
        )

    snapshots = [online(20, 30.0, -450), online(10, 40.0, 420), online(5, 40.0, -170), online(0, 40.0, 310)]

    summary = summarize("Claude Code", [], 0, None, [], snapshots)

    assert summary.rate_limits[0].predicted_end is not None
    assert "quota changed 10m ago" in summary.source_status


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


def write_dummy_credentials_file(home: Path, token: str, expires_in: timedelta | None = None) -> None:
    import json

    claude_dir = home / ".claude"
    claude_dir.mkdir(exist_ok=True)
    oauth = {"accessToken": token, "subscriptionType": "pro"}
    if expires_in is not None:
        oauth["expiresAt"] = int((utc_now() + expires_in).timestamp() * 1000)
    (claude_dir / ".credentials.json").write_text(json.dumps({"claudeAiOauth": oauth}), encoding="utf-8")


def online_usage_response(utilization: float = 40.0) -> dict:
    resets_at = (utc_now() + timedelta(hours=2)).isoformat()
    return {
        "five_hour": {"utilization": utilization, "resets_at": resets_at},
        "seven_day": {"utilization": 10.0, "resets_at": resets_at},
    }


def write_online_cache(home: Path, fetched_ago: timedelta, **extra) -> Path:
    import json

    cache_path = home / ".ai-usage-monitor" / "claude-online-usage-cache.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache = {"fetched_at": (utc_now() - fetched_ago).isoformat(), "plan_type": "pro", "response": online_usage_response(30.0)}
    cache.update(extra)
    cache_path.write_text(json.dumps(cache), encoding="utf-8")
    return cache_path


def http_429(retry_after: str | None):
    import email.message
    import urllib.error

    headers = email.message.Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError("https://example.invalid", 429, "Too Many Requests", headers, None)


def test_claude_oauth_token_prefers_macos_keychain_over_credentials_file():
    import json
    import subprocess
    import tempfile
    from unittest import mock

    from ai_usage_monitor import claude_online

    payload = json.dumps({"claudeAiOauth": {"accessToken": "dummy-token", "subscriptionType": "max"}})
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=payload, stderr="")
    with tempfile.TemporaryDirectory() as home, mock.patch.dict("os.environ", {}, clear=True):
        with mock.patch.object(claude_online.sys, "platform", "darwin"), mock.patch.object(
            claude_online.subprocess, "run", return_value=completed
        ) as run:
            assert claude_online.read_oauth_token(Path(home)) == ("dummy-token", "max")
            assert run.call_args.args[0][3] == claude_online.KEYCHAIN_SERVICE

        # A leftover credentials file must not shadow the Keychain on macOS.
        write_dummy_credentials_file(Path(home), "stale-token")
        with mock.patch.object(claude_online.sys, "platform", "darwin"), mock.patch.object(
            claude_online.subprocess, "run", return_value=completed
        ):
            assert claude_online.read_oauth_token(Path(home)) == ("dummy-token", "max")

        with mock.patch.object(claude_online.sys, "platform", "win32"), mock.patch.object(
            claude_online.subprocess, "run"
        ) as run:
            assert claude_online.read_oauth_token(Path(home)) == ("stale-token", "pro")
            run.assert_not_called()


def test_claude_oauth_token_keychain_miss_falls_back_to_credentials_file():
    import subprocess
    import tempfile
    from unittest import mock

    from ai_usage_monitor import claude_online

    missing = subprocess.CompletedProcess(args=[], returncode=44, stdout="", stderr="not found")
    with tempfile.TemporaryDirectory() as home, mock.patch.dict("os.environ", {}, clear=True):
        with mock.patch.object(claude_online.sys, "platform", "darwin"), mock.patch.object(
            claude_online.subprocess, "run", return_value=missing
        ):
            assert claude_online.read_oauth_token(Path(home)) == (None, None)

            write_dummy_credentials_file(Path(home), "file-token")
            assert claude_online.read_oauth_token(Path(home)) == ("file-token", "pro")


def test_claude_online_expired_token_is_not_fetched_and_says_why():
    import tempfile
    from unittest import mock

    from ai_usage_monitor import claude_online

    with tempfile.TemporaryDirectory() as tmp, mock.patch.dict("os.environ", {}, clear=True), mock.patch.object(
        claude_online.sys, "platform", "win32"
    ):
        home = Path(tmp)
        write_online_cache(home, timedelta(hours=3))
        write_dummy_credentials_file(home, "old-token", expires_in=timedelta(minutes=-5))
        with mock.patch.object(claude_online, "fetch_usage") as fetch:
            usage = claude_online.load_claude_online_usage(home)
        fetch.assert_not_called()
        assert usage.notes and usage.notes[0].startswith(claude_online.TOKEN_EXPIRED_NOTE)
        assert usage.snapshots[-1].capture_status["rate_limits"] == "online stale"

        # Once Claude Code refreshes the token, the next refresh fetches again.
        write_dummy_credentials_file(home, "new-token", expires_in=timedelta(hours=8))
        with mock.patch.object(claude_online, "fetch_usage", return_value=online_usage_response()) as fetch:
            usage = claude_online.load_claude_online_usage(home)
        fetch.assert_called_once_with("new-token")
        assert usage.notes == []


def test_claude_online_429_follows_retry_after_and_is_logged():
    import json
    import tempfile
    from unittest import mock

    from ai_usage_monitor import claude_online

    with tempfile.TemporaryDirectory() as tmp, mock.patch.dict("os.environ", {}, clear=True), mock.patch.object(
        claude_online.sys, "platform", "win32"
    ):
        home = Path(tmp)
        cache_path = write_online_cache(home, timedelta(minutes=10))
        write_dummy_credentials_file(home, "token", expires_in=timedelta(hours=8))
        with mock.patch.object(claude_online, "fetch_usage", side_effect=http_429("120")):
            usage = claude_online.load_claude_online_usage(home)
        assert usage.notes == ["Claude online usage fetch rate limited"]

        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        wait = (datetime.fromisoformat(cache["backoff_until"]) - utc_now()).total_seconds()
        assert 110 <= wait <= 120
        errors = (home / ".ai-usage-monitor" / claude_online.ERRORS_NAME).read_text(encoding="utf-8").splitlines()
        assert json.loads(errors[-1])["retry_after"] == "120"

        # Still backed off with the same token: no request is sent.
        with mock.patch.object(claude_online, "fetch_usage") as fetch:
            usage = claude_online.load_claude_online_usage(home)
        fetch.assert_not_called()
        assert usage.notes == ["Claude online usage fetch is backed off after rate limiting"]

        # A token refreshed by Claude Code has its own allowance: skip the backoff.
        write_dummy_credentials_file(home, "fresh-token", expires_in=timedelta(hours=8, minutes=30))
        with mock.patch.object(claude_online, "fetch_usage", return_value=online_usage_response()) as fetch:
            usage = claude_online.load_claude_online_usage(home)
        fetch.assert_called_once_with("fresh-token")


def test_claude_online_backoff_without_retry_after_uses_default():
    from ai_usage_monitor import claude_online

    assert claude_online.backoff_seconds(None) == claude_online.DEFAULT_BACKOFF_SECONDS
    assert claude_online.backoff_seconds("abc") == claude_online.DEFAULT_BACKOFF_SECONDS
    assert claude_online.backoff_seconds("5") == claude_online.MIN_BACKOFF_SECONDS
    assert claude_online.backoff_seconds("99999") == claude_online.MAX_BACKOFF_SECONDS


def test_claude_online_default_ttl_reaches_the_loader():
    from unittest import mock

    from ai_usage_monitor import cli

    args = cli.parse_args(["--claude-online-usage"])
    assert args.claude_online_ttl == 150
    with mock.patch.object(cli, "load_claude_online_usage") as load, mock.patch.object(
        cli, "default_claude_provider"
    ) as claude, mock.patch.object(cli, "default_codex_provider") as codex:
        load.return_value.snapshots = []
        load.return_value.notes = []
        claude.return_value.collect.return_value = ([], [], [], [])
        codex.return_value.collect.return_value = ([], [], [], [])
        cli.collect_summaries(args)
    assert load.call_args.kwargs["ttl_seconds"] == 150


def test_claude_online_history_is_trimmed_to_30_days_and_read_from_tail():
    import json
    import tempfile

    from ai_usage_monitor import claude_online

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "history.jsonl"
        now = utc_now()
        lines = []
        for days_ago in (40, 32, 29, 1):
            entry = {
                "fetched_at": (now - timedelta(days=days_ago)).isoformat(),
                "response": online_usage_response(float(days_ago)),
            }
            lines.append(json.dumps(entry))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        claude_online.append_log(path, {"fetched_at": now.isoformat(), "response": online_usage_response(0.0)}, "fetched_at")
        kept = [json.loads(line)["response"]["five_hour"]["utilization"] for line in path.read_text(encoding="utf-8").splitlines()]
        assert kept == [29.0, 1.0, 0.0]

        # Within the extra day nothing is rewritten.
        before = path.read_text(encoding="utf-8")
        claude_online.trim_log(path, "fetched_at")
        assert path.read_text(encoding="utf-8") == before

        # Tail reads cross block boundaries without returning a cut line.
        assert len(claude_online.read_tail_lines(path, 2, block_size=16)) == 2
        assert [s.windows[0].used_percent for s in claude_online.read_history(path, keep=2)] == [1.0, 0.0]


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
    test_claude_oauth_token_prefers_macos_keychain_over_credentials_file()
    test_claude_oauth_token_keychain_miss_falls_back_to_credentials_file()
    test_claude_online_expired_token_is_not_fetched_and_says_why()
    test_claude_online_429_follows_retry_after_and_is_logged()
    test_claude_online_backoff_without_retry_after_uses_default()
    test_claude_online_default_ttl_reaches_the_loader()
    test_claude_online_history_is_trimmed_to_30_days_and_read_from_tail()
    print("ok")
