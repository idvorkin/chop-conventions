# Harden Telegram — Design Reference

> **Loaded on demand.** This file is not a skill entry point — it's supplementary reading for the [`harden-telegram`](./SKILL.md) skill. SKILL.md is the operator runbook ("what do I do when it breaks?"); this file is the architectural reference ("why is it built this way and what does it guarantee?").

Read this before:
- Modifying `telegram_bot.py` or `server.ts` in a way that touches the queue, the socket, the singleton, or the reaction protocol
- Designing a similar durable bridge somewhere else and wondering if the shape fits
- Debugging a failure that the doctor doesn't recognize (the invariants here tell you what *should* be true)

Full design spec (on Igor's box): `~/gits/igor2/docs/superpowers/specs/2026-04-12-telegram-two-process-design.md` — the canonical long-form version of this skill's content lives there for historical traceability.

---

## The problem the split solves

Telegram's Bot API has no message-history endpoint. Once `getUpdates` returns a batch and the poller advances its cursor, those messages are gone. If the poller dies mid-batch — or between batches — anything received in the gap is lost forever.

Old design: one bun process held `getUpdates` *and* served MCP over stdio. When Claude restarted (for any reason — MCP disconnect, crash, user-initiated), the bun process died with it. Every message that arrived during the Claude restart window was dropped on the floor.

New design: separate **who polls Telegram** from **who talks to Claude**.

```
Telegram API
    │
    ▼
┌────────────────────────────────────────┐
│  telegram_bot.py  (persistent)         │
│  • Owns getUpdates forever             │
│  • Writes every event → inbound.db     │
│  • Singleton via PID + flock           │
│  • Survives Claude restarts            │
└────────┬───────────────┬───────────────┘
         │ SQLite WAL    │ bot.sock (wakeup)
         │ (durable)     │ (bare "\n")
┌────────▼───────────────▼───────────────┐
│  server.ts  (ephemeral)                │
│  • Reads undelivered rows              │
│  • Emits MCP notifications             │
│  • Dies with the Claude session        │
│  • Never polls Telegram                │
└────────────────┬───────────────────────┘
                 ▼
             Claude Code
```

---

## Durability contract

**At-least-once delivery.** If server.ts crashes after sending the MCP notification but before committing `delivered=1`, the row is re-delivered on the next catch-up pass. Claude seeing a duplicate is better than losing a message; `message_id` in the meta lets Claude dedupe if it matters.

### What survives what

| Event | Messages in flight | Recovery |
|---|---|---|
| Claude restart / MCP disconnect | Queued in `inbound.db` with `delivered=0` | server.ts catches up on reconnect via `SELECT WHERE delivered=0` |
| server.ts crash | Same as above | Same |
| telegram_bot.py crash | **Anything received between last successful write and crash** | Only real gap in the system. Telegram will re-send once the next poll cycle starts, since the cursor isn't advanced until the write commits. |
| OS crash / power loss | Rows in WAL but not checkpointed | SQLite WAL replays on next open. Writes that hadn't synced are lost — unavoidable without `PRAGMA synchronous=FULL`, which we don't use for throughput reasons. |
| Plugin cache auto-update overwrites `server.ts` | Queue keeps accumulating. Claude stops seeing new messages until server.ts is redeployed. | Doctor catches this via source/plugin sha256 drift. See harden-telegram Tier 2a. |

The important invariant: **telegram_bot.py advances the Telegram cursor only after the row is written and committed to WAL.** A crash mid-`INSERT` means Telegram re-delivers the same update on the next `getUpdates` call. This is the one place where the "lose a message" window exists, and it's bounded to a single in-flight write.

### Why at-least-once and not exactly-once

Exactly-once would require a distributed-transaction-equivalent protocol between telegram_bot.py's write and Telegram's cursor advance — not possible with the Bot API. At-least-once with client-side dedupe on `message_id` is the industry default for this shape.

---

## Why SQLite WAL + Unix socket

**SQLite WAL mode** is the queue:
- `PRAGMA journal_mode=WAL` — writer (telegram_bot.py INSERT) doesn't block readers (server.ts SELECT)
- `PRAGMA busy_timeout=5000` on both connections — lets concurrent writes (telegram_bot.py INSERT, server.ts UPDATE `delivered=1`) resolve without SQLITE_BUSY errors at the Python/TS layer
- `BEGIN IMMEDIATE` for the INSERT and the UPDATE so lock conflicts surface at `BEGIN` time, not at `COMMIT`
- Single file on local disk. No broker, no port, no daemon. Survives everything short of OS crash + corrupt FS.

**Why not a real queue (RabbitMQ/NATS/Redis)?** Overkill for one writer + one reader on the same host. A broker adds a whole new process to supervise, fail-over, and patch. SQLite is already there.

**Unix socket (`bot.sock`)** is the wakeup signal, not the data channel:
- telegram_bot.py writes `\n` (bare newline, one byte) to every connected client after each commit
- server.ts treats `\n` as "go re-query the DB"; the row content is never on the socket
- This separates durability (SQLite) from latency (socket push). If the socket dies, server.ts falls back to 2s `setInterval` polling — degraded latency but zero message loss.
- Why push at all if polling is the fallback? Latency. A tight loop polling every 100ms burns CPU; a 2s poll feels laggy. The push gives sub-100ms delivery in the happy path.

---

## Singleton via `flock` + PID file

telegram_bot.py must be a singleton — two instances calling `getUpdates` on the same bot token is a 409 Conflict loop.

The pattern:
1. `open(bot.pid, 'w+')` and `fcntl.flock(fd, LOCK_EX | LOCK_NB)`
2. If the flock fails, another instance owns it; exit cleanly
3. If it succeeds, write the PID and hold the lock for the process lifetime
4. `flock` releases automatically on process exit (normal or crash) — no stale-lock cleanup needed
5. The PID file is advisory; the real guard is `flock`

**Why `flock` and not just a PID file?** A PID file without a lock is racy: process A writes its PID and dies, process B reads the file, sees "alive" from a stale PID, refuses to start, manual intervention needed. `flock` releases at process death regardless of how the process died, so the next start always succeeds.

**Stale `bot.sock` handling:** on startup, telegram_bot.py tries to `connect()` to `bot.sock`; if the connect fails (nobody listening), it `unlink()`s the stale socket file before `bind()`ing its own. This is necessary because a hard crash leaves the socket file on disk.

---

## 409 Conflict retry semantics

The polite-failure case: telegram_bot.py calls `getUpdates`, Telegram responds 409 because another poller is still holding the cursor. Causes:
1. A stale telegram_bot.py that the flock didn't catch (very rare — only if flock is broken or user disabled it)
2. A second machine running the same bot token (the user forgot to disable the old box)
3. During migration — the legacy bun `server.ts` hadn't stopped yet

telegram_bot.py's response: exponential backoff (1, 2, 4, 8, 16, 30, 30, 30, …) and keep retrying forever. No manual intervention needed — Telegram drops the stale poller on its own once the holding cursor times out, typically within 60 seconds.

**Historical gotcha (bgt.3.1):** an earlier version of the retry loop also did `pkill -f 'bun.*server.ts'` on every 409 to clear out the legacy bun poller during migration. Post-migration, that kill was *still* there — and it couldn't distinguish the new MCP-only `server.ts` from the legacy polling one (same process name, same plugin cache cwd). Any transient 409 (or any bot restart) would SIGTERM Claude's live MCP bridge. Removed in commit `317031a`. **Lesson: don't kill by process name when the names are identical across roles.** Kill by `/proc/<pid>/cwd`, a role-specific env var, or just let Telegram's own timeout resolve the conflict.

---

## Dual-reaction liveness protocol

Each delivered message gets two reactions, stamped by two different processes:

| Reaction | Stamped by | Proves |
|---|---|---|
| 👀 (inner) | `telegram_bot.py` | Message was received, passed the allowlist gate, written to SQLite |
| 🫡 (outer) | `server.ts` | Row was read from SQLite, MCP notification was emitted to Claude |

Seeing both 👀 and 🫡 on a message is a visual "both halves of the pipeline ran" signal. Seeing only 👀 means server.ts never picked it up (dead? disconnected? stuck?). Seeing only 🫡 shouldn't happen — server.ts can't deliver a row that telegram_bot.py didn't ingest.

### Reaction whitelist gotcha

Telegram's **free-tier bot reaction whitelist** is fixed. `✅` is *not* on it — the initial design tried to use ✅ and got `400 REACTION_INVALID` in production. `👀 🫡 👍 👎 🔥 🎉 🤝 ❤️ 😁 🤔 🤩 🙏 👌 🏆 💯 ⚡` are. Full list: https://core.telegram.org/bots/api#reactiontypeemoji

### Permission-reply outcome (bgt.3.3)

Free-tier reactions **replace** rather than **stack** — a second `setMessageReaction` call clobbers the first. For `permission_reply` rows, telegram_bot.py stamps ✔️ or ✖️ (matching the y/n outcome) instead of the generic ack 👀. server.ts must skip the outer 🫡 stamp for `message_type === 'permission_reply'`, or the outcome glyph gets clobbered. The guard lives in `server.ts:deliverRow` — gate the outer reaction on `row.message_type === 'message'`.

---

## Plugin-cache deploy: `cp`, never symlink

bun resolves imports **relative to the real file path**. If you symlink `$TELEGRAM_SOURCE_DIR/server.ts` into `~/.claude/plugins/cache/claude-plugins-official/telegram/0.0.5/server.ts`, bun follows the symlink, starts resolving imports from your source directory, and fails with `Cannot find module '@modelcontextprotocol/sdk'` because the MCP SDK only lives in the plugin cache's `node_modules`.

Always `cp`. Doctor catches source/plugin drift via sha256 compare — if the source and deployed copy diverge, the doctor reports it and harden-telegram walks the redeploy.

---

## Invariants & edge cases

**Invariants that must hold:**
- Exactly one telegram_bot.py process per bot token per host (enforced by flock)
- `inbound.db.delivered` is monotonic: once 1, never back to 0
- `telegram_bot.py` never reads `delivered` — only writes `delivered=0` on INSERT
- `server.ts` never writes rows — only reads rows and UPDATEs `delivered=1`
- The Telegram cursor advance happens *after* the SQLite commit, not before
- `assertSendable` always runs before any file upload (see `server.ts:150`)
- `ATTACHMENTS_DIR` is in the `assertSendable` allowlist; `STATE_DIR` (credential store) is not

**Known edge cases:**
- **`catchup()` re-entry while in flight** (bgt.3.4): wakeups that arrive during an in-flight pass set `pendingCatchup=true` so the in-flight loop re-queries `selectUndelivered` before returning. Without this, rows inserted between the snapshot SELECT and the end of the pass wait for the next wakeup — fine for bursty traffic, visible delay for isolated messages.
- **Regex drift between Python and JS** (bgt.3.5): `deliverPermissionReply` used to silently drop rows whose text didn't re-match server.ts's regex after telegram_bot.py's did. Now falls back to delivering as a regular message + hex-dump log. Long-term fix: store parsed groups in explicit columns so server.ts doesn't re-match.
- **Attachment path guard** (bgt.3.2): `download_attachment` fallback writes under `ATTACHMENTS_DIR/inbox/`, not `STATE_DIR/inbox/`, so `assertSendable` allows the returned path back through `reply(files=…)`.
- **PEP-723 Python version pin**: `requires-python = ">=3.11,<3.14"` — without the upper bound, `uv` picks Python 3.14, and `python-telegram-bot` doesn't ship 3.14 wheels yet. The script starts but crashes at import.
- **python-telegram-bot manual lifecycle**: `Application.post_init()` only fires from `run_polling()`/`run_webhook()`. A manual `initialize → start → updater.start_polling` must call `await _post_init(app)` explicitly — without it, `bot.sock` is never bound and the socket-push path silently fails.

---

## Related

- [`SKILL.md`](./SKILL.md) — operator runbook (doctor, reload, recovery tiers)
- Igor's canonical design doc: `~/gits/igor2/docs/superpowers/specs/2026-04-12-telegram-two-process-design.md`
- Igor's migration plan: `~/gits/igor2/docs/superpowers/plans/2026-04-12-telegram-two-process-migration.md`

## Related beads

- `igor2-bgt` — (EPIC) Two-process Telegram Infrastructure
- `igor2-u8b` — Build telegram_bot.py persistent poller
- `igor2-vdi` — Modify server.ts to SQLite reader + MCP bridge
- `igor2-bgt.1` — Dual-reaction liveness
- `igor2-bgt.2` — Integration kill-test
- `igor2-bgt.3` — Post-cutover code review findings (all 5 closed)
- `igor2-bgt.3.1` — `_kill_stale_bun_server` removal (the kill-by-name footgun)
- `igor2-bgt.3.3` — Permission-reply reaction clobber fix
- `igor2-bgt.3.4` — `pendingCatchup` for mid-pass inserts
- `igor2-bgt.3.5` — Permission-reply regex-drift fallback
- `igor2-bgt.4` — harden-telegram skill (operator runbook)
- `igor2-bgt.3.6` — This file (originally shipped as a separate durable-telegram skill)
- `igor2-bgt.6` — Folded into harden-telegram/design.md (this file's current home)
