from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from .providers import default_claude_provider, default_codex_provider, summarize
from .render import render_json, render_text


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    while True:
        summaries = collect_summaries(args)
        if args.json:
            output = render_json(summaries)
        else:
            output = render_text(summaries, watch=args.watch, refresh=args.refresh, details=args.details)
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
    parser.add_argument("--refresh", type=int, default=15, help="Refresh interval in seconds for --watch.")
    parser.add_argument("--json", action="store_true", help="Print JSON.")
    parser.add_argument("--details", action="store_true", help="Show diagnostic details such as source paths.")
    parser.add_argument("--claude-path", action="append", type=Path, help="Claude Code log root or file.")
    parser.add_argument("--codex-path", action="append", type=Path, help="Codex log root or file.")
    parser.add_argument("--claude-limit-tokens", type=int, help="Manual Claude token limit for estimates.")
    parser.add_argument("--codex-limit-tokens", type=int, help="Manual Codex token limit for estimates.")
    return parser.parse_args(argv)


def collect_summaries(args: argparse.Namespace):
    home = Path.home()
    claude = default_claude_provider(home)
    codex = default_codex_provider(home)

    claude_events, claude_files, claude_notes, claude_rate_limits = claude.collect(args.claude_path)
    codex_events, codex_files, codex_notes, codex_rate_limits = codex.collect(args.codex_path)

    return [
        summarize("Claude Code", claude_events, claude_files, args.claude_limit_tokens, claude_notes, claude_rate_limits),
        summarize("Codex", codex_events, codex_files, args.codex_limit_tokens, codex_notes, codex_rate_limits),
    ]


def clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")
