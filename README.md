# AI Usage Monitor

Local terminal monitor for Claude Code and Codex usage limits, token activity, reset times, burn rate, and data freshness.

Default usage is optimized for one-person local operation. Claude Code quota percentages are read from the Claude Code OAuth usage endpoint by default when using `run.cmd`; token details are read from local files. The OAuth token is never printed, saved, or cached.

## English

### Version

Current version: `1.1.2`

`1.1.2` fixes a Codex rate-limit window misclassification. Codex's `primary`/`secondary` rate-limit keys do not reliably identify the 5-hour and weekly windows, but the monitor assumed `primary` always meant the 5-hour window and `secondary` always meant the weekly window. When an account currently reports only one window (for example, no active 5-hour window), the monitor now classifies each window by its actual length (`window_minutes`) instead of trusting the key it arrived under, so `5 hours` and `1 week` are labeled correctly no matter which slot the data arrives in.

`1.1.1` fixed two freshness regressions: the Claude online quota cache TTL is restored to 60 seconds (it had drifted to 300 seconds in `1.1.0`, delaying the `5 hours`/`1 week` percentages), and `run.cmd` no longer lets its default flags silently override a user-supplied `--claude-online-ttl` or `--codex-reset-credits-ttl`.

`1.1.0` improved freshness labeling and added Codex reset credit display. Claude token freshness and quota freshness are separated more clearly, and available Codex reset credits are shown as a supplemental line.

### Policy

This monitor prioritizes accurate remaining quota over guessed token limits.

| Policy | Reason |
| --- | --- |
| Show remaining percentage first | The main question during work is how much usable quota is left. |
| Use provider-reported quota data when available | Subscription limits are not reliably inferable from local token counts. |
| Keep token details local | Tokens, breakdown, burn rate, and activity can be read from local session/transcript files without calling model APIs. |
| Cache Claude online quota briefly | The quota endpoint is not a model call, but polling it every redraw is unnecessary. |
| Label stale data clearly | Old quota snapshots are useful only when the UI makes their age visible. |

### What It Shows

| Item | Meaning |
| --- | --- |
| `Status` | Overall risk state from provider quota snapshots, or `STALE SNAPSHOT` when quota data is old. It does not include `Projected`. |
| `5 hours` | Remaining and used percentage for the short usage window, plus reset time. |
| `1 week` | Remaining and used percentage for the weekly usage window, plus reset time. |
| `Projected` | Codex 5-hour supplemental line. It usually mirrors `5 hours` as `from quota`, and switches to `from token_count` only when local token activity appears ahead of quota updates. |
| `Predicted End` | Estimated time to reach 100% at the current rate, when enough history exists. |
| `Burn Rate` | Observed tokens per minute. |
| `Breakdown` | Input, output, cache creation, and cache read tokens. |
| `Tokens` | Observed local tokens for the active source. |
| `Activity` | Last token activity time and number of parsed events. |
| `Quota Read` | Age of the quota snapshot read by the monitor. This can be fresh even when the value itself has not changed. |
| `Source` | Freshness of quota and token sources, including `quota changed`, `token_count`, `transcript`, and `file updated`. |
| `Reset Credits` | Codex supplemental lines shown only when available reset credits are found, with each credit's granted and expiration times. |

### Example Output

The following example uses dummy data only, including reset-credit counts and timestamps.

```text
AI LIMIT MONITOR
============================================================================
mode: WATCH    updated: 2026-06-04T12:30:00+09:00    refresh: 15s normal
rate limit percentages use the freshest provider quota snapshot available

CLAUDE CODE    plan: pro
----------------------------------------------------------------------------
Status      WATCH
5 hours     [##########------------------------]  30% left   70% used   reset 13:40
1 week      [####################--------------]  60% left   40% used   reset Jun 05
Predicted End 5 hours: after reset
Burn Rate   12,480 tokens/min
Breakdown   in 120,000 | out 18,400 | cache new 32,000 | cache read 410,000
Tokens      580,400 observed
Activity    last 12:29 | events 42
Quota Read  15s ago
Source      rate_limits online | quota changed 1m ago | context local transcript | transcript 20s ago

CODEX    plan: plus
----------------------------------------------------------------------------
Status      OK
5 hours     [#############---------------------]  38% left   62% used   reset 20:58
Projected   [#############---------------------]  ~38% left   ~62% used   from quota
1 week      [##########################--------]  75% left   25% used   reset Jun 11
Predicted End 5 hours: 18:25
Burn Rate   24,900 tokens/min
Breakdown   in 850,000 | out 26,000 | cache new 0 | cache read 1,200,000
Tokens      2,076,000 observed
Activity    last 12:29 | events 128
Quota Read  12s ago
Source      quota changed 1m ago | token_count 12s ago | file updated 3s ago
Reset Credits 2 available
  #1  granted 2031-01-01 09:00:00 +0900 | expires 2031-02-01 09:00:00 +0900
  #2  granted 2031-01-05 18:30:00 +0900 | expires 2031-02-05 18:30:00 +0900
```

### Data Sources

| Feature | Claude Code source | Codex source | Why this source |
| --- | --- | --- | --- |
| 5-hour quota percentage | `https://api.anthropic.com/api/oauth/usage` using the local Claude Code OAuth session | Codex local session files, `token_count.rate_limits` window classified as 5-hour by its length | Quota percentages must come from provider-reported account/session data. Local token totals alone cannot reproduce subscription limits. |
| Weekly quota percentage | Same Claude usage endpoint, `seven_day` | Codex local session files, `token_count.rate_limits` window classified as weekly by its length | Weekly limits are account-window limits, not raw transcript totals. Codex's `primary`/`secondary` keys do not reliably identify which window is which, so the monitor classifies each window by its actual length (`window_minutes`) rather than trusting the key it arrived under. |
| Reset time | Claude usage endpoint `resets_at` | Codex local `rate_limits.*.resets_at` | Reset timestamps are emitted with provider quota snapshots. |
| Token breakdown | Claude Code transcript files, plus statusLine context when present | Codex `token_count.info.total_token_usage` and usage records | These local files are the most direct low-cost source for active token activity. |
| Burn rate | Local timestamped Claude transcript/statusLine history | Local timestamped Codex session events | Burn rate needs multiple local observations; it does not require an online quota call. |
| Plan label | Claude Code credentials metadata when available | Codex local rate-limit snapshot when available | Plan labels are informational only and should not be guessed. |
| Projected quota | Not used | Codex 5-hour cumulative `token_count` delta after the last unchanged quota percentage, gated by staleness and token movement | Codex may update token activity earlier than quota percentage. The estimate is only a lag-compensation line, never a replacement for the official quota row. Weekly projection is avoided because current-session token deltas can overstate account-wide weekly movement. |
| Reset credits | Not used | Optional ChatGPT account reset-credit endpoint, sanitized and cached | Reset credits are not quota windows. They are displayed separately and are never used to alter remaining percentage. |
| Freshness | Online cache age, statusLine capture age, transcript age | Token event age and session file mtime | Freshness explains whether the displayed data is live, cached, or stale. |

### Extraction Logic

- The monitor scans JSON/JSONL files only. Unknown or malformed records are skipped.
- In normal mode it reads the freshest session candidates instead of all historical files. `--full-scan` is available for diagnostics.
- Duplicate usage records are removed by message/request identity when available, otherwise by timestamp/model/token tuple.
- Claude token totals are taken from the latest active transcript source after local transcript parsing.
- Codex token totals prefer the latest cumulative `token_count.info.total_token_usage` from the active session file. This avoids summing old sessions into the current display.
- Claude quota snapshots prefer the online usage endpoint. Local statusLine `rate_limits` are fallback data when online quota is unavailable.
- Quota snapshots are extracted from provider-emitted `rate_limits`; manual token limits are not used for subscription quota percentages.

### Claude Online Usage

`run.cmd` enables Claude online quota by default:

```cmd
run.cmd
```

It reads the access token from this priority order:

1. `CLAUDE_CODE_OAUTH_TOKEN`
2. Claude Code credentials file

The monitor calls the Claude usage endpoint with the existing Claude Code OAuth session. This is a usage/quota request, not a model inference request, so it should not consume conversation tokens. The response is cached here for 60 seconds by default (`--claude-online-ttl`):

```text
AI usage monitor cache directory / claude-online-usage-cache.json
```

The cache stores only sanitized quota fields and timestamps. It does not store the OAuth token. If the online request fails, the monitor falls back to the last cache or local statusLine snapshot and marks the source as stale. When online quota is available, it takes priority over local statusLine `rate_limits`.

The Claude online usage endpoint is an undocumented helper used for this local monitor. To avoid treating it like a high-frequency official API, HTTP 429 responses trigger a short backoff.

### Claude Local Token Flow

Claude token details come from local transcript files:

```text
Claude Code transcript directory
```

The optional statusLine capture still helps when Claude Code provides context-window or rate-limit data locally:

```text
AI usage monitor cache directory / claude-statusline.json
AI usage monitor cache directory / claude-statusline-history.jsonl
AI usage monitor cache directory / claude-statusline-heartbeat.json
```

Diagnostic command:

```cmd
scripts\diagnose_claude_statusline.cmd
```

### Codex Data Flow

Codex is read from local session JSONL files:

```text
Codex session directory
```

The monitor reads several fresh session candidates on every refresh, then prefers the active source's latest cumulative `token_count`. This avoids summing old sessions while increasing the chance of catching the active session. Codex account UI can still be ahead of local JSONL writes; `Source` shows `quota changed`, `token_count` age, and file update age so this lag is visible.

`Projected` is always rendered for the Codex 5-hour window:

- `from quota`: the projected line mirrors the provider 5-hour quota because the provider value is fresh enough or local token movement is too small to justify compensation.
- `from token_count`: the monitor adds a bounded local estimate when the 5-hour quota percentage has been unchanged for at least 3 minutes and cumulative `token_count` moved by at least 0.5 projected percentage points.

The token-to-percent conversion uses the median of previous quota-increase intervals in the same reset window, with broad outlier filtering. A single refresh can add at most 15 percentage points to the provider value. This keeps short bursts from dominating the estimate while still making likely quota lag visible during active development.

Codex reset credits are read separately when enabled by the launcher. The monitor stores only sanitized credit fields such as status, granted time, and expiration time in its local cache. Access tokens and account IDs are not printed or cached. If the reset-credit request fails, quota and token display continue normally.

### Refresh

`run.cmd` starts watch mode:

```cmd
run.cmd
```

One-shot:

```cmd
run.cmd --once
```

Profiles:

| Profile | Redraw interval |
| --- | --- |
| `fast` | 10 seconds |
| `normal` | 15 seconds |
| `slow` | 30 seconds |
| `--refresh N` | Custom seconds |

Claude online quota is cached for 60 seconds and Codex reset credits for 300 seconds by default, even though the terminal redraws every 15 seconds. Local Claude/Codex files are reread every redraw.

### Commands

Run with real local data:

```cmd
run.cmd --once
```

Show source paths:

```cmd
run.cmd --once --details
```

Scan all historical files for diagnostics:

```cmd
run.cmd --once --full-scan
```

Use fixture data:

```cmd
run.cmd --once --no-claude-online-usage --no-codex-reset-credits --claude-path tests\fixtures\claude-statusline.json --codex-path tests\fixtures\codex
```

JSON output:

```cmd
run.cmd --once --json
```

Disable ANSI colors:

```cmd
run.cmd --once --no-color
```

Color rules are intentionally narrow:

- `quota changed ...` is highlighted as the key freshness point for the remaining percentage.
- `Projected` label always uses the token-source color. Projected values and `from token_count` use the token-source color only when token-count compensation is active; `from quota` values stay plain.
- Token numbers in `Burn Rate`, `Breakdown`, and `Tokens` use the token-source color. Labels stay plain.
- `token_count` and `transcript` use the same token-source color because they are the source for token data.
- The token-source color is blue/cyan so it does not collide with green status values such as `OK`.
- `Quota Read` and `file updated` stay plain because they are supporting timestamps, not proof that quota changed.

Run tests:

```cmd
cmd.exe /c "set PYTHONPATH=src&& python tests\test_parser.py"
```

### Privacy And Limits

- The monitor does not read browser cookies.
- The Claude OAuth token is read only to call the usage endpoint and is not saved, printed, or cached.
- The Claude online quota call is not a model inference call.
- The Codex reset-credit lookup uses the existing local Codex OAuth session, caches only sanitized credit metadata, and does not change quota percentages.
- Codex online/account usage endpoints are not used unless a stable local or official source is identified. Codex `Projected` is a local estimate and is labeled separately.
- Unknown or malformed local log records are skipped.

### License

MIT License. See `LICENSE`.

## 日本語

### バージョン

現在のバージョン: `1.1.2`

`1.1.2` では Codex のレート制限ウィンドウの誤分類を修正しました。Codex の `primary`/`secondary` キーは、どちらが5時間でどちらが週次かを確実には表していませんが、モニターはこれまで `primary` を常に5時間ウィンドウ、`secondary` を常に週次ウィンドウとみなしていました。アカウントが現在ウィンドウを1つしか返さない場合（例: 5時間枠が存在しない場合）でも、キーをそのまま信用せず実際のウィンドウ長（`window_minutes`）で分類するようにしたため、どちらのキーにデータが来ても `5 hours` / `1 week` が正しく表示されます。

`1.1.1` では freshness に関する退行を2件修正しました。Claude online quota の cache TTL を 60 秒に戻し（`1.1.0` で 300 秒に伸びており、`5 hours`/`1 week` の％反映が遅れていました）、`run.cmd` の既定引数がユーザー指定の `--claude-online-ttl` / `--codex-reset-credits-ttl` を黙って上書きしていた問題を修正しました。

`1.1.0` では freshness 表示を改善し、Codex reset credit 表示を追加しました。Claude の token freshness と quota freshness をより明確に分け、利用可能な Codex reset credit は補助行として表示します。

### 方針

このモニターは、推測したトークン上限よりも、できるだけ正確な「残り使用量」を優先します。

| 方針 | 理由 |
| --- | --- |
| 残り％を先に表示 | 作業中に一番知りたいのは、あとどれだけ使えるかです。 |
| 可能な限り provider が出す quota 値を使う | サブスクリプション上限は、ローカルの token 合計だけでは正確に再現できません。 |
| token 詳細はローカルから読む | Tokens、Breakdown、Burn Rate、Activity はローカルの transcript/session から低負荷で取得できます。 |
| Claude online quota は短時間キャッシュ | usage endpoint は model call ではありませんが、画面更新のたびに呼ぶ必要はありません。 |
| 古いデータは明示 | 古い snapshot も、いつ取得したものかが分かるなら参考情報として使えます。 |

### 表示項目

| 項目 | 意味 |
| --- | --- |
| `Status` | provider quota snapshot から見た状態。quota が古い場合は `STALE SNAPSHOT`。`Projected` は混ぜません。 |
| `5 hours` | 5時間枠の残り％、使用済み％、reset 時刻。 |
| `1 week` | 週間枠の残り％、使用済み％、reset 時刻。 |
| `Projected` | Codex 5時間枠の補助行。通常は `from quota` として `5 hours` と同じ値を出し、local token activity が quota 更新より先行していると判断した場合だけ `from token_count` に切り替えます。 |
| `Predicted End` | 現在の増加ペースで 100% に達する予測時刻。履歴が足りない場合は unavailable。 |
| `Burn Rate` | 観測された tokens/min。 |
| `Breakdown` | input、output、cache creation、cache read tokens。 |
| `Tokens` | active source で観測されたローカルトークン数。 |
| `Activity` | 最終 token activity と parsed event 数。 |
| `Quota Read` | monitor が quota snapshot を読んだ時刻からの経過時間。値自体が更新された時刻とは別です。 |
| `Source` | `quota changed`、`token_count`、`transcript`、`file updated` など、quota と token source の鮮度。 |
| `Reset Credits` | Codex の利用可能な reset credit が見つかった場合だけ表示する補助行。各券の付与日時と期限を表示します。 |

### 表示例

以下は reset credit の件数・日時を含め、完全なダミーデータです。

```text
AI LIMIT MONITOR
============================================================================
mode: WATCH    updated: 2026-06-04T12:30:00+09:00    refresh: 15s normal
rate limit percentages use the freshest provider quota snapshot available

CLAUDE CODE    plan: pro
----------------------------------------------------------------------------
Status      WATCH
5 hours     [##########------------------------]  30% left   70% used   reset 13:40
1 week      [####################--------------]  60% left   40% used   reset Jun 05
Predicted End 5 hours: after reset
Burn Rate   12,480 tokens/min
Breakdown   in 120,000 | out 18,400 | cache new 32,000 | cache read 410,000
Tokens      580,400 observed
Activity    last 12:29 | events 42
Quota Read  15s ago
Source      rate_limits online | quota changed 1m ago | context local transcript | transcript 20s ago

CODEX    plan: plus
----------------------------------------------------------------------------
Status      OK
5 hours     [#############---------------------]  38% left   62% used   reset 20:58
Projected   [#############---------------------]  ~38% left   ~62% used   from quota
1 week      [##########################--------]  75% left   25% used   reset Jun 11
Predicted End 5 hours: 18:25
Burn Rate   24,900 tokens/min
Breakdown   in 850,000 | out 26,000 | cache new 0 | cache read 1,200,000
Tokens      2,076,000 observed
Activity    last 12:29 | events 128
Quota Read  12s ago
Source      quota changed 1m ago | token_count 12s ago | file updated 3s ago
Reset Credits 2 available
  #1  granted 2031-01-01 09:00:00 +0900 | expires 2031-02-01 09:00:00 +0900
  #2  granted 2031-01-05 18:30:00 +0900 | expires 2031-02-05 18:30:00 +0900
```

### データ取得方法

| 機能 | Claude Code の取得元 | Codex の取得元 | そのデータを使う理由 |
| --- | --- | --- | --- |
| 5時間 quota % | ローカル Claude Code OAuth session で `https://api.anthropic.com/api/oauth/usage` を呼ぶ | Codex local session files の `token_count.rate_limits` のうち、長さで5時間と判定したウィンドウ | quota % は provider が持つ account/session データが必要です。ローカル token 合計だけでは subscription limit を再現できません。 |
| 週間 quota % | 同じ Claude usage endpoint の `seven_day` | Codex local session files の `token_count.rate_limits` のうち、長さで週間と判定したウィンドウ | 週間上限は transcript 合計ではなく account window の値だからです。Codex の `primary`/`secondary` キーはどちらがどちらかを確実には表さないため、キーではなく実際のウィンドウ長（`window_minutes`）で分類しています。 |
| reset 時刻 | Claude usage endpoint の `resets_at` | Codex local `rate_limits.*.resets_at` | reset は provider quota snapshot と一緒に出る値です。 |
| token breakdown | Claude Code transcript files と、存在する場合は statusLine context | Codex `token_count.info.total_token_usage` と usage records | active な token activity を低負荷に見るには、ローカルファイルが最も直接的です。 |
| Burn Rate | timestamp 付き Claude transcript/statusLine history | timestamp 付き Codex session events | burn rate は複数のローカル観測点から計算でき、online quota call は不要です。 |
| plan 表示 | 取得できる場合のみ Claude Code credentials metadata | 取得できる場合のみ Codex local rate-limit snapshot | plan は参考表示であり、推測表示は避けます。 |
| Projected quota | 使いません | 5時間 quota % が変わらない間の Codex cumulative `token_count` 差分を、stale 時間と token 増分で制御して使用 | Codex は token activity が quota % より先に更新される場合があります。その遅れを、公式 quota 行を置き換えずに見えるようにします。週間枠は、現在セッションの token 差分だけで account-wide な週次変化を過大表示しやすいため推定しません。 |
| Reset credits | 使いません | 任意の ChatGPT account reset-credit endpoint。sanitize して cache | Reset credit は quota window ではありません。別表示し、残り％の計算には使いません。 |
| 鮮度 | online cache age、statusLine capture age、transcript age | token event age と session file mtime | 表示値が live、cached、stale のどれか判断するためです。 |

### 抽出ロジック

- monitor は JSON/JSONL のみを scan します。不明または壊れた record は skip します。
- 通常モードでは全履歴ではなく、新しい session candidate を優先して読みます。診断時は `--full-scan` を使えます。
- usage record は、message/request identity がある場合はそれで重複排除し、ない場合は timestamp/model/token tuple で重複排除します。
- Claude token totals は local transcript を解析したうえで、最新の active transcript source を使います。
- Codex token totals は active session file の最新 cumulative `token_count.info.total_token_usage` を優先します。これにより古い session を現在値へ合算しないようにします。
- Claude quota snapshot は online usage endpoint を優先します。local statusLine の `rate_limits` は、online quota が使えない場合の fallback data として扱います。
- quota snapshot は provider が出す `rate_limits` から抽出します。manual token limit は subscription quota % の計算には使いません。

### Claude Online Usage

`run.cmd` は既定で Claude online quota を有効にします。

```cmd
run.cmd
```

access token は次の順で読みます。

1. `CLAUDE_CODE_OAUTH_TOKEN`
2. Claude Code credentials file

既存の Claude Code OAuth session を使って Claude usage endpoint を呼びます。これは usage/quota 確認であり、model inference ではないため、会話 token を消費する類の呼び出しではありません。レスポンスは既定で 60 秒だけキャッシュします（`--claude-online-ttl`）。

```text
AI usage monitor cache directory / claude-online-usage-cache.json
```

キャッシュするのは quota と timestamp だけです。OAuth token は保存しません。online request が失敗した場合は、最後の cache または local statusLine snapshot にフォールバックし、source を stale として表示します。online quota が使える場合は、local statusLine の `rate_limits` より優先します。

Claude online usage endpoint は、このローカル monitor のために使っている undocumented helper です。高頻度の公式 API のように扱わないため、HTTP 429 では短い backoff を入れます。

### Claude Local Token Flow

Claude の token 詳細は local transcript から読みます。

```text
Claude Code transcript directory
```

statusLine capture は、Claude Code が local に context-window や rate-limit を渡す場合の補助として引き続き使えます。

```text
AI usage monitor cache directory / claude-statusline.json
AI usage monitor cache directory / claude-statusline-history.jsonl
AI usage monitor cache directory / claude-statusline-heartbeat.json
```

診断コマンド:

```cmd
scripts\diagnose_claude_statusline.cmd
```

### Codex Data Flow

Codex は local session JSONL を読みます。

```text
Codex session directory
```

monitor は毎回、複数の新しい session candidate を読み、active source の最新 cumulative `token_count` を優先します。これにより古い session を合算せず、active session を拾える確度を上げます。ただし Codex の account UI が local JSONL より先に更新される場合があります。その差は `Source` の `quota changed`、`token_count` age、file update age で見えるようにします。

`Projected` は Codex 5時間枠で常に表示します。

- `from quota`: provider の 5時間 quota が十分新しい、または local token movement が小さいため、`Projected` は公式 5時間 quota と同じ値を表示します。
- `from token_count`: 5時間 quota % が3分以上変わらず、かつ cumulative `token_count` が履歴換算で0.5%以上動いた場合だけ、local estimate を加算します。

token から percent への換算は、同じ reset window 内で過去に quota % が増えた複数区間の中央値を使い、大きな外れ値を除外します。1回の補正幅は provider 値から最大 +15% までに制限します。これにより、作業中の短い token burst で推定が暴れすぎることを防ぎつつ、active development 中の quota 反映遅れを見えるようにします。

Codex reset credit は launcher で有効な場合に別途読みます。monitor は status、granted time、expiration time など表示に必要な sanitized credit field だけを local cache に保存します。access token と account ID は表示・キャッシュしません。reset-credit request が失敗しても、quota と token 表示は通常通り継続します。

### 更新頻度

`run.cmd` は watch mode で起動します。

```cmd
run.cmd
```

1回だけ実行:

```cmd
run.cmd --once
```

profiles:

| Profile | 画面更新間隔 |
| --- | --- |
| `fast` | 10秒 |
| `normal` | 15秒 |
| `slow` | 30秒 |
| `--refresh N` | 任意秒数 |

画面は 15 秒ごとに更新しますが、Claude online quota は既定で 60 秒、Codex reset credit は既定で 300 秒キャッシュします。Claude/Codex の local file は画面更新ごとに読み直します。

### Commands

実データで実行:

```cmd
run.cmd --once
```

source path を表示:

```cmd
run.cmd --once --details
```

診断用に全履歴を scan:

```cmd
run.cmd --once --full-scan
```

fixture data で実行:

```cmd
run.cmd --once --no-claude-online-usage --no-codex-reset-credits --claude-path tests\fixtures\claude-statusline.json --codex-path tests\fixtures\codex
```

JSON output:

```cmd
run.cmd --once --json
```

ANSI color を無効化:

```cmd
run.cmd --once --no-color
```

色のルールは意図的に絞っています。

- `quota changed ...` は、残り％が最後に変化した重要ポイントとして強調します。
- `Projected` の項目名は常に token-source 色で表示します。値と `from token_count` は token-count 補正が有効なときだけ token-source 色にし、`from quota` の値は通常色のままにします。
- `Burn Rate`、`Breakdown`、`Tokens` の token 数値だけを token 系 source 色にします。項目名は通常表示のままです。
- `token_count` と `transcript` は token data の取得元なので同じ token 系 source 色にします。
- token-source 色は青系にし、`OK` などの緑 status と混同しないようにします。
- `Quota Read` と `file updated` は補助時刻であり、quota が変化した根拠ではないため強調しません。

テスト:

```cmd
cmd.exe /c "set PYTHONPATH=src&& python tests\test_parser.py"
```

### Privacy And Limits

- browser cookie は読みません。
- Claude OAuth token は usage endpoint 呼び出しにだけ使い、保存・表示・キャッシュしません。
- Claude online quota call は model inference call ではありません。
- Codex reset-credit lookup は既存の local Codex OAuth session を使い、sanitized credit metadata だけを cache し、quota % は変更しません。
- Codex online/account usage endpoint は、安定した local または official source が確認できるまで使いません。Codex `Projected` はローカル推定として別表示します。
- 不明または壊れた local log record は skip します。

### License

MIT License です。詳細は `LICENSE` を参照してください。
