from pathlib import Path

from ai_usage_monitor.providers import Provider, summarize


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
    assert summary.events == 2
    assert summary.usage.input_tokens == 3_000
    assert summary.usage.output_tokens == 750
    assert summary.usage.cache_read_input_tokens == 300
    assert summary.remaining_tokens == 5_950
    assert summary.rate_limits[0].used_percent == 30.0
    assert summary.rate_limits[1].used_percent == 14.0
    assert summary.rate_limits[0].predicted_end is not None
    assert summary.plan_type == "plus"


def test_claude_statusline_fixture_collects_rate_limits():
    provider = Provider("Claude Code", [Path("tests/fixtures/claude-statusline.json")])
    events, files, notes, rate_limits = provider.collect()
    summary = summarize("Claude Code", events, files, None, notes, rate_limits)

    assert files == 1
    assert summary.events == 0
    assert summary.rate_limits[0].used_percent == 62.5
    assert summary.rate_limits[1].used_percent == 24.0
    assert summary.plan_type == "max"


if __name__ == "__main__":
    test_claude_fixture_collects_usage()
    test_codex_fixture_collects_usage()
    test_claude_statusline_fixture_collects_rate_limits()
    print("ok")
