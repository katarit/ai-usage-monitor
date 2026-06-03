from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0

    rate_limits = find_rate_limits(payload)
    if not rate_limits:
        return 0

    out_dir = Path(os.environ.get("AI_USAGE_MONITOR_DIR", Path.home() / ".ai-usage-monitor"))
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "claude-statusline.json"
    tmp_path = out_dir / "claude-statusline.json.tmp"
    tmp_path.write_text(json.dumps({"rate_limits": rate_limits}, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(out_path)
    return 0


def find_rate_limits(value):
    if isinstance(value, dict):
        candidate = value.get("rate_limits") or value.get("rateLimits")
        if isinstance(candidate, dict):
            return candidate
        for child in value.values():
            found = find_rate_limits(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_rate_limits(child)
            if found:
                return found
    return None


if __name__ == "__main__":
    raise SystemExit(main())
