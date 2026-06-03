# AI Usage Monitor

Local read-only monitor for Claude Code and Codex usage limits and token activity.

This tool does not call private APIs, read browser cookies, reuse auth tokens, or modify Claude/Codex configuration.

## What It Shows

- Claude Code 5-hour and weekly usage percentage when Claude statusline capture is configured
- Codex 5-hour and weekly usage percentage from local Codex session `rate_limits`
- remaining percentage, used percentage, reset time, and terminal progress bars
- observed token usage as supporting detail
- observed usage bars and burn-rate risk when subscription limits are unknown
- input, output, cache creation, and cache read token buckets
- estimated cost from local price settings
- configured limit and estimated remaining tokens
- burn rate and estimated exhaustion time
- live refresh with `--watch`

Example with real rate limit percentages:

```text
AI LIMIT MONITOR
============================================================================
mode: WATCH    updated: 2026-06-03T21:55:00+09:00    refresh: 15s
rate limit percentages use the latest local provider snapshot when available

CLAUDE CODE
----------------------------------------------------------------------------
Status      OK
5 hours    [#############---------------------]  37% left   63% used   reset 23:00
1 week     [##########################--------]  76% left   24% used   reset Jun 08
Predicted End 5 hours: after reset
Burn Rate  18,342 tokens/min
Breakdown  in 41,321,426 | out 187,466 | cache new 0 | cache read 0
```

When a provider has no rate limit snapshot yet, token activity is shown as a fallback:

```text
CLAUDE CODE
----------------------------------------------------------------------------
Status       WATCH BURN
Observed     [####################--------------]  1,809,593,832 tokens
Quota        subscription limit unknown
Remaining    official remaining unavailable
Burn Rate    37,057 tokens/min
```

## Quick Start

```cmd
run.cmd --once
```

Run from the project root with:

```cmd
run.cmd --once --claude-path .\tests\fixtures\claude --codex-path .\tests\fixtures\codex
```

Watch mode:

```cmd
run.cmd --watch --refresh 15
```

Watch mode clears and redraws the terminal every refresh interval. The default interval used by `run.cmd` is 15 seconds. Change it with `--refresh`.

On Windows, `run.cmd` starts watch mode:

```cmd
.\run.cmd
```

Pass arguments to override the default:

```cmd
.\run.cmd --once --claude-path .\tests\fixtures\claude --codex-path .\tests\fixtures\codex
```

## Paths

Default scan roots:

- Claude Code rate limits: `%USERPROFILE%\.ai-usage-monitor\claude-statusline.json`
- Claude Code token activity: `%USERPROFILE%\.claude`
- Codex rate limits and token activity: `%USERPROFILE%\.codex\sessions`

Override them with:

```cmd
run.cmd --once --claude-path C:\path\to\claude\logs --codex-path C:\path\to\codex\logs
```

## Limits

Codex limit percentages are read from local Codex session records when `token_count.rate_limits` events exist.

Claude limit percentages require statusline capture. Add a Claude Code statusline command that pipes statusline JSON to:

```cmd
python C:\Users\you\Projects\tools-dev\ai-usage-monitor\scripts\claude_statusline_capture.py
```

The capture script writes only `rate_limits` to:

```text
%USERPROFILE%\.ai-usage-monitor\claude-statusline.json
```

Manual token limits are still supported as a fallback:

```cmd
run.cmd --once --claude-limit-tokens 1000000 --codex-limit-tokens 1000000
```

Manual token limits are estimates. Rate limit percentages from Codex session records and Claude statusline capture are preferred when available.

When no limit is configured, true remaining percentage cannot be calculated. The monitor switches to observed usage, burn rate, and risk display instead.

## Data Accuracy

| Field | Source | Accuracy |
| --- | --- | --- |
| Codex 5h/week usage percent | Local Codex `token_count.rate_limits` events | Provider-reported local snapshot |
| Claude 5h/week usage percent | Claude Code statusline `rate_limits` input | Provider-reported local snapshot |
| Observed tokens | Local JSON/JSONL records | Exact for records that expose token fields |
| Estimated cost | Observed tokens and local price table | Estimate |
| Remaining tokens | Observed tokens subtracted from manual limit | Estimate |

## JSON Output

```cmd
run.cmd --once --json
```

## Notes

- Unknown or malformed log lines are skipped.
- Token extraction is conservative and may miss provider-specific formats that do not expose token fields.
