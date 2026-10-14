#!/bin/bash
cd "$(dirname "$0")" || exit 1
export PYTHONPATH="$PWD/src"

PYTHON_CMD=python3
command -v python3 >/dev/null 2>&1 || PYTHON_CMD=python

CLAUDE_ONLINE_ARGS=(--claude-online-usage)
CODEX_CREDITS_ARGS=(--codex-reset-credits --codex-reset-credits-ttl 300)
for arg in "$@"; do
  [ "$arg" = "--no-claude-online-usage" ] && CLAUDE_ONLINE_ARGS=()
  [ "$arg" = "--no-codex-reset-credits" ] && CODEX_CREDITS_ARGS=()
done

if [ $# -eq 0 ]; then
  "$PYTHON_CMD" -m ai_usage_monitor --watch --profile normal "${CLAUDE_ONLINE_ARGS[@]}" "${CODEX_CREDITS_ARGS[@]}"
else
  "$PYTHON_CMD" -m ai_usage_monitor "${CLAUDE_ONLINE_ARGS[@]}" "${CODEX_CREDITS_ARGS[@]}" "$@"
fi
