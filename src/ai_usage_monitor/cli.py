from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from .claude_online import load_claude_online_usage
from .codex_reset_credits import load_codex_reset_credits
from .providers import default_claude_provider, default_codex_provider, summarize
from .render import render_json, render_text

REFRESH_PROFILES = {
    "fast": 10,
    "normal": 15,
    "slow": 30,
}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    args.refresh = resolve_refresh(args)
    args.refresh_label = resolve_refresh_label(args)
    while True:
        summaries = collect_summaries(args)
        if args.json:
            output = render_json(summaries)
        else:
            output = render_text(
                summaries,
                watch=args.watch,
                refresh=args.refresh,
                refresh_label=args.refresh_label,
                details=args.details,
                color=use_color(args),
            )
        if args.watch:
            clear_screen()
        print(output)
        if not args.watch:
            return 0
        time.sleep(args.refresh)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Monitor local Claude Code and Codex token usage.")
    parser.add_argument("--once", action="store_true", help="Run once. This is the default.")
    parser.add_argument("--watch", action="store_true", help="Refresh continuously.")
    parser.add_argument("--profile", choices=REFRESH_PROFILES.keys(), default="normal", help="Refresh profile for --watch.")
    parser.add_argument("--refresh", type=int, help="Custom refresh interval in seconds for --watch.")
    parser.add_argument("--json", action="store_true", help="Print JSON.")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI colors in text output.")
    parser.add_argument("--details", action="store_true", help="Show diagnostic details such as source paths.")
    parser.add_argument("--full-scan", action="store_true", help="Scan all provider log files instead of newest candidates.")
    parser.add_argument("--claude-token-scan", action="store_true", help="Also scan all ~/.claude JSON/JSONL files for Claude token activity.")
    parser.add_argument("--claude-path", action="append", type=Path, help="Claude Code log root or file.")
    parser.add_argument("--codex-path", action="append", type=Path, help="Codex log root or file.")
    parser.add_argument("--claude-limit-tokens", type=int, help="Manual Claude token limit for estimates.")
    parser.add_argument("--codex-limit-tokens", type=int, help="Manual Codex token limit for estimates.")
    parser.add_argument(
        "--claude-online-usage",
        action="store_true",
        help="Read Claude subscription usage from the Claude Code OAuth usage endpoint.",
    )
    parser.add_argument(
        "--no-claude-online-usage",
        dest="claude_online_usage",
        action="store_false",
        help="Disable Claude online subscription usage lookup.",
    )
    parser.add_argument(
        "--claude-online-ttl",
        type=int,
        default=300,
        help="Seconds to cache Claude online usage responses. Default: 300.",
    )
    parser.add_argument(
        "--codex-reset-credits",
        action="store_true",
        help="Read available Codex reset credits from the ChatGPT account endpoint.",
    )
    parser.add_argument(
        "--no-codex-reset-credits",
        dest="codex_reset_credits",
        action="store_false",
        help="Disable Codex reset credit lookup.",
    )
    parser.add_argument(
        "--codex-reset-credits-ttl",
        type=int,
        default=300,
        help="Seconds to cache Codex reset credit responses. Default: 300.",
    )
    return parser.parse_args(argv)


def collect_summaries(args: argparse.Namespace):
    home = Path.home()
    claude = default_claude_provider(home)
    codex = default_codex_provider(home)
    claude_paths = args.claude_path
    if claude_paths is None and args.claude_token_scan:
        claude_paths = [
            home / ".ai-usage-monitor" / "claude-statusline.json",
            home / ".ai-usage-monitor" / "claude-statusline-history.jsonl",
            home / ".claude",
        ]

    claude_events, claude_files, claude_notes, claude_rate_limits = claude.collect(
        claude_paths,
        full_scan=args.full_scan,
    )
    if args.claude_online_usage:
        online_usage = load_claude_online_usage(home, ttl_seconds=args.claude_online_ttl)
        claude_rate_limits.extend(online_usage.snapshots)
        claude_notes.extend(online_usage.notes)
    codex_events, codex_files, codex_notes, codex_rate_limits = codex.collect(
        args.codex_path,
        full_scan=args.full_scan,
        latest_files=5,
    )
    codex_reset_credits = None
    if args.codex_reset_credits:
        codex_reset_credits = load_codex_reset_credits(home, ttl_seconds=args.codex_reset_credits_ttl)
        codex_notes.extend(codex_reset_credits.notes)

    claude_summary = summarize(
        "Claude Code",
        claude_events,
        claude_files,
        args.claude_limit_tokens,
        claude_notes,
        claude_rate_limits,
    )
    codex_summary = summarize(
        "Codex",
        codex_events,
        codex_files,
        args.codex_limit_tokens,
        codex_notes,
        codex_rate_limits,
    )
    if codex_reset_credits is not None:
        codex_summary.reset_credits = codex_reset_credits.credits
        codex_summary.reset_credit_source = codex_reset_credits.source
    return [claude_summary, codex_summary]


def clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def use_color(args: argparse.Namespace) -> bool:
    if args.no_color or args.json:
        return False
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def resolve_refresh(args: argparse.Namespace) -> int:
    if args.refresh is not None:
        return args.refresh
    return REFRESH_PROFILES[args.profile]


def resolve_refresh_label(args: argparse.Namespace) -> str:
    if args.refresh is not None:
        return "custom"
    return args.profile
