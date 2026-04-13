---
name: harden-telegram
description: Diagnose and recover the two-process Telegram MCP chain — run the doctor, hot-redeploy server.ts, reload plugins without bouncing MCP, restart telegram_bot.py, and walk through known failure modes. Use when Telegram stops working mid-session, messages aren't arriving, or the bot is silent.
allowed-tools: Bash, Read, Glob, Grep
---

# Harden Telegram

Diagnose and repair the two-process Telegram MCP channel. Three tiers:

| Invocation | Scope |
|---|---|
| `/harden-telegram doctor` | Quick health check via `telegram_debug.py --doctor` |
| `/harden-telegram reload` | Hot redeploy + reload plugins without dropping MCP |
| `/harden-telegram deep` | Walk known failure modes if the doctor is clean but the channel is still broken |

## 🧭 Principle: diagnostics live in code, not here

**Before adding any "check X, grep Y" prose to this skill, ask: can it go in `telegram_debug.py` instead?**

Skills describe **WHEN** to run diagnostics and **HOW** to recover. Code describes **WHAT** to check. Paths move — code errors loudly, prose rots silently. If you catch yourself listing file paths, grep patterns, or "does process X exist" checks here, STOP and add them to `telegram_debug.py --doctor` instead, then run it from this skill.

Full principle in [`../../CLAUDE.md`](../../CLAUDE.md) §"Diagnostics: Code Over Prose".

## Tool locations

The Python tooling this skill drives ships with the skill itself under `tools/`. All paths are discoverable from any repo via the chop-conventions auto-link.

| Tool | Path |
|---|---|
| Doctor / diagnostics | `~/.claude/skills/harden-telegram/tools/telegram_debug.py` |
| Plugin-reload watchdog | `~/.claude/skills/harden-telegram/tools/watchdog.py` |
| Runtime state dir | `$LARRY_TELEGRAM_DIR` (default `~/larry-telegram/`) |
| Canonical source dir | `$TELEGRAM_SOURCE_DIR` (optional — enables deploy-drift check) |

Two env vars parameterize the tool:

- **`LARRY_TELEGRAM_DIR`** — runtime state directory (`bot.pid`, `bot.sock`, `inbound.db`, `server.log`, `attachments/`). Defaults to `~/larry-telegram/` if unset. The telegram_bot.py production process reads the same variable.
- **`TELEGRAM_SOURCE_DIR`** — directory containing the canonical `server.ts` + `telegram_bot.py` source you deploy from. Required if you want the doctor to verify the plugin-cache copy matches your upstream. If unset, the drift check degrades to a note ("plugin cache: &lt;hash&gt; — set TELEGRAM_SOURCE_DIR to enable drift check").

This skill only helps on machines running the Telegram MCP plugin with the two-process architecture. If you don't have a `telegram_bot.py` process alongside the MCP `server.ts`, the doctor will report the whole chain as missing.

---

## Tier 1: Doctor (`/harden-telegram doctor`)

```bash
python3 ~/.claude/skills/harden-telegram/tools/telegram_debug.py --doctor
```

Runs every check, prints ✅/⚠️/❌ per section, tails `server.log`, shows source/plugin hash drift, exits non-zero on failure. JSON mode: `--json`. Legacy human-readable summary (pre-doctor): plain invocation with no flags.

Read the output top-to-bottom. If everything is green, say so and stop. If anything is red, proceed to Tier 2 for redeploys or Tier 3 for known failure modes.

### Architecture (one-paragraph recap)

Two processes share the Telegram MCP responsibility. `telegram_bot.py` is the persistent Python poller — owns `getUpdates`, writes every event to `~/larry-telegram/inbound.db` (SQLite WAL), survives Claude restarts, singleton via `flock`. `server.ts` is the ephemeral bun MCP bridge — reads undelivered rows, emits MCP notifications, dies with the Claude session. Flow: `Telegram → telegram_bot.py → inbound.db → bot.sock wakeup → server.ts catchup → MCP → Claude`. Dual-reaction liveness: 👀 (inner, bot.py) + 🫡 (outer, server.ts). Both glyphs on a message means both halves of the pipeline ran.

**For the full design rationale** — durability contract, why SQLite WAL + Unix socket, flock semantics, 409 retry logic, invariants across crashes — read the [`durable-telegram`](../durable-telegram/SKILL.md) skill. That's the design reference; this skill is the operator runbook.

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

**Fix:** kill the old bun first, then reload — the next MCP request respawns from the plugin cache:

```bash
pkill -TERM -f 'bun.*server.ts'
# Then run /reload-plugins from another context (or via the watchdog below).
```

### 2c. Watchdog reload (from a background shell — NEVER foreground)

```bash
# %25 → your pane id (tmux display-message -p '#{pane_id}')
python3 ~/.claude/skills/harden-telegram/tools/watchdog.py reload --pane %25 \
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

The `flock` singleton inside the script prevents double-launch — safe to run when already alive. Verify with `telegram_debug.py --doctor`.

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
- `igor2-cr1` — telegram_debug.py --doctor mode
- `igor2-ddn` — Telegram watchdog auto-recover via tmux send-keys
