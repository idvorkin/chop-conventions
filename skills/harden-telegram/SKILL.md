---
name: harden-telegram
description: Diagnose and recover the two-process Telegram MCP chain — run the doctor, hot-redeploy server.ts, reload plugins without bouncing MCP, restart telegram_bot.py, and walk through known failure modes. Use when Telegram stops working mid-session, messages aren't arriving, or the bot is silent.
allowed-tools: Bash, Read, Glob, Grep
---

# Harden Telegram

Diagnose and repair the two-process Telegram MCP channel. Three tiers:

| Invocation | Scope |
|---|---|
| `/harden-telegram doctor` | Quick health check via `telegram_debug.py doctor` |
| `/harden-telegram reload` | Hot redeploy + reload plugins without dropping MCP |
| `/harden-telegram deep` | Walk known failure modes if the doctor is clean but the channel is still broken |
| **`telegram_debug.py direct-send "..."`** | **Emergency escape hatch** — POST straight to Telegram Bot API, bypass MCP entirely. Use when `server.ts` is dead and you still need to reach Igor. Message is auto-prefixed with `[direct-send]` so it's visually distinct. See §Emergency Direct-Send below. |

## 🧭 Principle: diagnostics live in code, not here

**Before adding any "check X, grep Y" prose to this skill, ask: can it go in `telegram_debug.py` instead?**

Skills describe **WHEN** to run diagnostics and **HOW** to recover. Code describes **WHAT** to check. Paths move — code errors loudly, prose rots silently. If you catch yourself listing file paths, grep patterns, or "does process X exist" checks here, STOP and add them to `telegram_debug.py doctor` instead, then run it from this skill.

Full principle in [`../../CLAUDE.md`](../../CLAUDE.md) §"Diagnostics: Code Over Prose".

## Tool locations

The Python tooling this skill drives ships with the skill itself under `tools/`. The canonical `server.ts` + `telegram_bot.py` source now ships alongside it under `server/`. All paths are discoverable from any repo via the chop-conventions auto-link.

| Tool | Path |
|---|---|
| Doctor / diagnostics | `~/.claude/skills/harden-telegram/tools/telegram_debug.py` |
| Plugin-reload watchdog | `~/.claude/skills/harden-telegram/tools/watchdog.py` |
| Canonical source (deploy-from) | `~/.claude/skills/harden-telegram/server/` |
| Runtime state dir | `$LARRY_TELEGRAM_DIR` (default `~/larry-telegram/`) |
| Canonical source dir override | `$TELEGRAM_SOURCE_DIR` (optional — defaults to the `server/` subdir) |

Two env vars parameterize the tool:

- **`LARRY_TELEGRAM_DIR`** — runtime state directory (`bot.pid`, `bot.sock`, `inbound.db`, `server.log`, `attachments/`). Defaults to `~/larry-telegram/` if unset. The telegram_bot.py production process reads the same variable.
- **`TELEGRAM_SOURCE_DIR`** — directory containing the canonical `server.ts` + `telegram_bot.py` source you deploy from. **Defaults to the sibling `server/` subdirectory of this skill when unset.** Override if you're deploying from a different checkout (e.g. an in-flight feature branch elsewhere). The drift check always runs against whichever directory resolves.

### Source layout

```text
skills/harden-telegram/
├── SKILL.md              # this file (operator runbook)
├── design.md             # architectural reference, loaded on demand
├── tools/                # Python diagnostics vendored with the skill
│   ├── telegram_debug.py # doctor, direct-send, paths inventory
│   └── watchdog.py       # tmux-driven plugin reload
└── server/               # canonical Telegram server source (deploy-from)
    ├── server.ts         # bun MCP bridge (Igor's two-process fork)
    ├── server.ts.pre-split   # historical reference (pre-split monolith)
    ├── telegram_bot.py   # Python polling bot (SQLite + Unix socket)
    ├── package.json
    ├── bun.lock
    ├── tsconfig.json
    ├── hooks/            # log-telegram.py, log-telegram-inbound.py
    └── tests/            # pytest + bun test suites
```

**`server/` is the canonical source going forward.** When Tier 2a (Deploy) says `cp "$TELEGRAM_SOURCE_DIR/server.ts" <plugin-path>`, `$TELEGRAM_SOURCE_DIR` now resolves to `skills/harden-telegram/server/` by default — no env var required for the common case. Pre-existing callers that set `TELEGRAM_SOURCE_DIR` (e.g. cron scripts) continue to work unchanged; the override still wins when set.

This skill only helps on machines running the Telegram MCP plugin with the two-process architecture. If you don't have a `telegram_bot.py` process alongside the MCP `server.ts`, the doctor will report the whole chain as missing.

---

## Tier 1: Doctor (`/harden-telegram doctor`)

```bash
~/.claude/skills/harden-telegram/tools/telegram_debug.py doctor
```

Runs every check, prints ✅/⚠️/❌ per section, tails `server.log`, shows source/plugin hash drift, exits non-zero on failure. The script is a Typer CLI with a `uv run` shebang — subcommands are `doctor`, `paths`, `direct-send`, `send-reply`, `react`, `undelivered`. Run with no subcommand for the legacy human-readable summary; `--json` / `--tail N` options apply to that mode. Run `telegram_debug.py --help` for the current command list.

Read the output top-to-bottom. If everything is green, say so and stop. If anything is red, proceed to Tier 2 for redeploys or Tier 3 for known failure modes.

### Architecture (one-paragraph recap)

Two processes share the Telegram MCP responsibility. `telegram_bot.py` is the persistent Python poller — owns `getUpdates`, writes every event to `~/larry-telegram/inbound.db` (SQLite WAL), survives Claude restarts, singleton via `flock`. `server.ts` is the ephemeral bun MCP bridge — reads undelivered rows, emits MCP notifications, dies with the Claude session. Flow: `Telegram → telegram_bot.py → inbound.db → bot.sock wakeup → server.ts catchup → MCP → Claude`. Dual-reaction liveness: 👀 (inner, bot.py) + 🫡 (outer, server.ts). Both glyphs on a message means both halves of the pipeline ran.

**For the full design rationale** — durability contract, why SQLite WAL + Unix socket, flock semantics, 409 retry logic, invariants across crashes — read [`design.md`](./design.md) in this skill directory. That's the architectural reference, loaded on demand; this file is the operator runbook.

### Emergency Direct-Send (bypass MCP)

When the doctor is red, the MCP `reply` tool may be unavailable — you can't use the thing you're trying to fix. Use the escape hatch:

```bash
~/.claude/skills/harden-telegram/tools/telegram_debug.py direct-send "your message here"
# Override the default chat_id:
~/.claude/skills/harden-telegram/tools/telegram_debug.py direct-send "..." --chat-id 12345
```

How it works:
- Reads `TELEGRAM_BOT_TOKEN` from `~/.claude/channels/telegram/.env` (the same file the doctor checks).
- POSTs straight to `https://api.telegram.org/bot<TOKEN>/sendMessage` via stdlib `urllib` — no MCP, no `server.ts`, no `bot.sock`, no inbound.db. The only moving parts are the token file and Telegram's HTTPS endpoint.
- Chat id defaults to the most recent `inbound.chat_id` in `inbound.db`. Override with `--chat-id` if you need a specific target.
- Every outgoing message is auto-prefixed with `[direct-send] ` so Igor can tell on his phone that MCP was down when it landed. **Do not remove the tag** — the visual signal is the whole point.

Use it when:
- The hourly watchdog cron detects a red doctor and needs to notify Igor.
- You're walking Tier 2 recovery and need to send status updates that don't depend on `server.ts` being alive.
- You restart `telegram_bot.py` or redeploy `server.ts` and want to confirm the fix landed — the next message Igor sees without a `[direct-send]` tag proves MCP is back.

The watchdog cron scheduled by `/startup-larry` step 3 item 4 uses this exclusively — that's the design principle: **the thing watching Telegram must not depend on Telegram's MCP bridge.**

### Recovery Protocol (interactive session)

When an interactive Claude session detects the Telegram MCP has died — `mcp__plugin_telegram_telegram__*` tools vanish from the available tool list, or a `reply` call fails, or the doctor shows red — **follow this order. Do not skip the first direct-send.**

1. **Ping Igor's phone FIRST via `direct-send`, before diagnosing.** One second of cost, guarantees Igor knows something is happening even if he's on Telegram-only and not watching the terminal:
   ```bash
   ~/.claude/skills/harden-telegram/tools/telegram_debug.py \
     direct-send "⚠️ Larry: Telegram MCP down. Starting recovery."
   ```

2. **Diagnose.** Run `telegram_debug.py doctor`. Identify the specific failure mode.

3. **Walk Tier 2 recovery.** Kill stale process if needed, restart, fire `/reload-plugins` (via the background watchdog pattern — never foreground).

4. **Ping again on outcome** — same direct-send path:
   - Success: `direct-send "✅ Recovered — <what was fixed>"`
   - Failure: `direct-send "❌ Still broken — <details>"` AND append a timestamped line to `/tmp/larry_telegram_recovery.log`

5. **Verify via an untagged MCP reply.** After MCP comes back, send a normal `reply` tool call to confirm the bridge is live. The **absence** of the `[direct-send]` prefix on the next message Igor sees proves MCP is back — that's the semantic signal.

**Why the first direct-send is mandatory.** Silent recovery looks identical to "Claude crashed" from Igor's perspective. A 1-second notification is cheap insurance against a 30-minute silence. This is the same principle as the hourly watchdog cron documented above: **the thing watching Telegram must not depend on Telegram's MCP bridge.** The cron already follows this principle; the interactive recovery path must too.

**What counts as "detecting MCP is down".** Any of:
- System-reminder arrives saying `plugin:telegram:telegram` has disconnected
- `mcp__plugin_telegram_telegram__reply` or related tools are no longer in the tool list
- A reply-tool call returns a "tool not available" or transport error
- `telegram_debug.py doctor` shows any red section

Any one of these triggers the protocol. Do not wait for confirmation across multiple signals — ping first, confirm after.

---

## Tier 2: Reload (`/harden-telegram reload`)

### 2a. Deploy: cp, NEVER symlink

bun resolves imports relative to the real file path. Symlinking `server.ts` into the plugin cache breaks module resolution (`Cannot find module '@modelcontextprotocol/sdk'`).

```bash
# Find the active plugin version (may be 0.0.4 or 0.0.5 — don't guess):
cat ~/.claude/plugins/installed_plugins.json | python3 -c "import json,sys;print(json.load(sys.stdin)['telegram@claude-plugins-official'][0]['installPath'])"

# Deploy (TELEGRAM_SOURCE_DIR points at your canonical source tree):
cp "$TELEGRAM_SOURCE_DIR/server.ts" <that-path>/server.ts
```

Always back up first: `cp server.ts server.ts.backup-$(date +%Y%m%d-%H%M%S)`.

Doctor catches source/plugin drift automatically via sha256 compare when `TELEGRAM_SOURCE_DIR` is set.

### 2b. `/reload-plugins` doesn't pick up new server.ts code

**Symptom:** Changed `server.ts`, ran `cp` to plugin cache, ran `/reload-plugins`, but doctor shows the same bun PID.

**Cause:** `/reload-plugins` re-reads skills/hooks/agents/plugin config but leaves running MCP server processes alone. The existing bun process keeps serving the old code.

**Fix:** kill ONLY this session's bridge, then reload — the next MCP request respawns from the plugin cache. Run the doctor first to find the bridge owned by this session (look for the `SERVER.TS` line that prints `pid=<n> (claude=<your-claude-pid>)`):

```bash
~/.claude/skills/harden-telegram/tools/telegram_debug.py doctor
kill -TERM <pid-from-doctor>
# Then run /reload-plugins from another context (or via the watchdog below).
```

**Pair the kill with a polling cron.** In the same parallel tool-call batch as the kill, schedule `CronCreate` at `*/1 * * * *` to poll `telegram_debug.py undelivered`. The respawn window is 3–4 minutes; any inbound during that gap goes to a different session's bun and is invisible to this session. Delete the cron when the next untagged MCP reply confirms the bridge is back.

**DO NOT** use `pkill -f 'bun.*server.ts'` — that's a broadcast kill that also nukes bridges owned by *other* Claude sessions on the same machine. Each Claude session spawns its own bun `server.ts` child. The doctor classifies them as `ours` / `other-session` / `orphaned` by walking the `ppid` chain up to the nearest `claude` ancestor; trust that, never age or count. The historical "older bun = zombie" heuristic is wrong — multi-session machines routinely have several legitimate bridges running concurrently.

### 2c. Watchdog reload (from a background shell — NEVER foreground)

```bash
# Omit --pane to auto-resolve the caller's pane via parent-chain walk.
# NEVER pass `tmux display-message -p '#{pane_id}'` here — from a
# backgrounded, disowned subprocess with a stale TMUX_PANE env var,
# unscoped display-message falls back to the session's most-recently-active
# pane, which on a box with concurrent Claude sessions is routinely wrong.
# Let watchdog.py walk /proc/<pid>/stat ppid chain itself.
uv run ~/.claude/skills/harden-telegram/tools/watchdog.py reload \
  2>/tmp/watchdog_reload.log &
disown
```

**Must be backgrounded.** The watchdog sends an `Escape` to your pane before `/reload-plugins`, and foreground invocation cancels the current Claude agent turn.

Result log: `cat /tmp/watchdog_reload.log`.

Flags:
- `--pane %N` — target pane
- `--pid <n>` — auto-detect pane from bun pid
- `-m "message"` — follow-up text to send after reload lands

### 2d. telegram_bot.py died or never started

```bash
nohup "$TELEGRAM_SOURCE_DIR/telegram_bot.py" \
  --base-dir "${LARRY_TELEGRAM_DIR:-$HOME/larry-telegram}" \
  >>"${LARRY_TELEGRAM_DIR:-$HOME/larry-telegram}/startup.log" 2>&1 &
disown
```

The `flock` singleton inside the script prevents double-launch — safe to run when already alive. Verify with `telegram_debug.py doctor`.

### 2e. Full restart (nuclear)

Exit the Claude session, then re-launch via whatever script bootstraps `telegram_bot.py` on your setup. Whatever launcher you use should start `telegram_bot.py` *before* starting Claude (singleton-safe), then let Claude's MCP loader spawn `server.ts` from the plugin cache.

---

## Tier 3: Known Failure Modes (`/harden-telegram deep`)

Run the doctor first. If it's clean but Telegram is still misbehaving, walk this list.

### REACTION_INVALID on emoji reaction

**Symptom:** `outer reaction failed: 400 Bad Request: REACTION_INVALID` in `server.log`, or a raw API call with `{"type":"emoji","emoji":"X"}` is rejected.
**Cause:** Telegram's free-tier bot reaction whitelist is fixed. `✅` is NOT on it. `🦝` is NOT on it. `👀 🫡 👍 👎 🔥 🎉 🤝 ❤️ 😁 🤔 🤩 🙏 👌 🏆 💯 ⚡` ARE.
**Fix:** Pick from the whitelist. Full list: https://core.telegram.org/bots/api#reactiontypeemoji

### python-telegram-bot post_init skipped in manual lifecycle

**Symptom:** Bot process running but no `bot.sock`, no DB connection, no `polling as @...` line in log.
**Cause:** `Application.post_init()` callback is only auto-fired by `run_polling()` / `run_webhook()`. A manual lifecycle (`initialize → start → updater.start_polling`) must invoke it explicitly.
**Fix:** `await _post_init(app)` between `initialize()` and `start()`.

### REACTION dict vs ReactionTypeEmoji

**Symptom:** `[bot] reaction failed: unhashable type: 'dict'`.
**Cause:** Passing raw `{"type": "emoji", "emoji": "X"}` to `set_message_reaction()` — python-telegram-bot hashes reactions internally for dedup and plain dicts aren't hashable.
**Fix:** Use `telegram.ReactionTypeEmoji(emoji="X")`.

### PEP-723 script picks wrong Python on host

**Symptom:** Running `telegram_bot.py` directly fails with missing `telegram` module, or crashes at runtime despite `uv run` succeeding.
**Cause:** Host default Python is 3.14, but `python-telegram-bot` hasn't released 3.14-compatible wheels. `requires-python = ">=3.11"` lets uv pick 3.14 silently.
**Fix:** Pin the upper bound: `requires-python = ">=3.11,<3.14"`.

### Zombie telegram_bot.py steals updates (409 Conflict)

**Symptom:** `409 Conflict` in log, messages don't arrive.
**Cause:** Two instances polling the same bot token. `flock` should prevent this in steady state, but a crashed singleton can leave a dangling PID file.
**Fix:** `pkill -f telegram_bot.py`, verify only one comes back, check doctor. The 409 retry loop in `telegram_bot.py` uses exponential backoff — Telegram drops the stale poller on its own once a fresh `getUpdates` arrives.

### Plugin auto-update overwrites deployed server.ts

**Symptom:** After a plugin update, dual-reaction stops, `server.ts` doesn't connect to `bot.sock`.
**Cause:** Plugin cache was overwritten with the upstream copy, which doesn't have Igor's two-process code.
**Fix:** Redeploy via `cp` (Tier 2a). Doctor catches this via source/plugin hash drift check.

### Inbound messages arrive at telegram_bot.py but never reach Claude

**Symptom:** `inbound.db` has rows with `delivered = 0`, but Claude sees nothing.

Diagnosis order:

1. Run the doctor — confirm `bot.sock` accepts connections and `server.ts` is running.
2. If `server.ts` is dead: `/reload-plugins` may not respawn it (see Tier 2b). Kill bun and retry.
3. If `server.ts` is alive but not delivering: check `server.log` for `[mcp]` errors. The fallback path uses 2s `setInterval` polling when `bot.sock` is missing.

### Permission-reply outcome reaction gets clobbered

**Symptom:** User replied `y abcde` or `n abcde` to a permission request, but the reaction on their reply is 🫡 instead of ✔️/✖️.
**Cause:** `server.ts`'s `deliverRow` stamped the outer 🫡 on every delivered row. Free-tier reactions *replace* rather than *stack*, so the outer 🫡 clobbered `telegram_bot.py`'s outcome glyph.
**Fix:** Gate the outer reaction on `row.message_type === 'message'` in `deliverRow`. (Closed: igor2-bgt.3.3.)

### DB has values but app reads NULL for specific columns

**Symptom:** sqlite3 CLI shows the row with populated values; the app (via bun:sqlite or similar) reads NULL for a subset of columns — typically the ones populated by a second write.
**Cause:** Producer does INSERT (partial row) → notify consumer → UPDATE remaining columns. Consumer reads BEFORE the UPDATE commits, marks row delivered, never re-reads after the UPDATE. Classic race in split-write producer/consumer setups.
**Fix:** Defer the wakeup (`notify_clients()` or socket notify) until AFTER all column writes are committed. Or use a single compound INSERT that writes all columns at once. For telegram_bot.py this was shipped in PR #136 — same pattern applies to any split-write producer.
**How to diagnose:** if sqlite3 CLI sees the columns but the consumer sees NULL, don't chase SQLite/ORM bugs. Check the producer's INSERT vs notify vs UPDATE ordering FIRST.

### Debug build deployed to plugin cache — watchdog drift noise

When `cp`-ing an intentional debug build of `server.ts` into `~/.claude/plugins/cache/claude-plugins-official/telegram/<version>/server.ts`, the hourly watchdog's source/plugin hash check flags drift every run. Two options:

1. **Revert cache to pristine source when done debugging** (`cp "$TELEGRAM_SOURCE_DIR/server.ts" <cache-path>`). Clean state, watchdog quiet.
2. **Update the watchdog cron prompt to ignore drift during the debug window** — pass explicit "hash drift is expected, do not recover" instructions in the prompt.

Without one of these, the watchdog's default recovery path (redeploy source → cache) will clobber the live debug build mid-diagnostic. Seen during the 2026-04-15 session when the race condition debug required multiple server.ts iterations.

---

## Safety Rules

- **Never `pkill -f 'bun.*server.ts'` against a live session without a plan to respawn it.** That disconnects Claude's MCP bridge mid-turn. Pair every kill with a `/reload-plugins` dispatched from a background shell via the watchdog.
- **Tests that signal processes must mock their subprocess/OS calls.** An unmocked test of `_kill_stale_bun_server`-style code against the real process table SIGTERMs the live bridge — this happened on 2026-04-12. See [`../../CLAUDE.md`](../../CLAUDE.md) §"Process-Signaling Safety".
- **Never redeploy `server.ts` via symlink.** bun module resolution breaks. Use `cp`.
- **Never edit the Telegram bot token or `access.json` from this skill.** Those are the credential store — editing is what `/telegram:configure` and `/telegram:access` are for.

## Related beads

- `igor2-bgt` — (EPIC) Two-process Telegram Infrastructure
- `igor2-u8b` — Build telegram_bot.py persistent poller
- `igor2-vdi` — Modify server.ts to SQLite reader + MCP bridge
- `igor2-bgt.1` — Dual-reaction liveness
- `igor2-bgt.2` — Integration kill-test
- `igor2-bgt.3` — Post-cutover code review findings
- `igor2-bgt.4` — This skill's migration from igor2 to chop-conventions
- `igor2-cr1` — telegram_debug.py doctor mode
- `igor2-ddn` — Telegram watchdog auto-recover via tmux send-keys
