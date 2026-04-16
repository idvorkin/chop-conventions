# 2026-04-16 — Inbound Edit History

Dogfood of the reasoning-audit-trail format that the parallel delegate-skill
PR is adding. Six sections: **user request → interpretation → plan →
decisions → outcomes → deferred**. Each section should be readable in
isolation; prefer concrete references (file paths, commit SHAs, line numbers)
over prose.

---

## 1. User request

Igor (via orchestrator agent dispatched from `igor2`): add edit-history
linkage to the Telegram inbound pipeline so when Igor edits a Telegram
message, the prior text is preserved alongside the new text.

Scope called out:

- `skills/harden-telegram/server/telegram_bot.py` — edit-tracking logic
- Schema: add columns to `inbound.db` (`previous_text`, `edit_count`, `edited_at`)
- `skills/harden-telegram/server/server.ts` — surface edit history to MCP
- Migration must be idempotent; DB is live; back up first; do not restart bot
- Keep this reasoning doc at this exact path as the first dogfood

Workflow constraints: fork-branch-PR via `idvorkin-ai-tools`, draft PR into
`idvorkin/chop-conventions`, no signature (idvorkin/* target).

## 2. Interpretation

Igor confirmed that `telegram_bot.py` already receives `edited_message`
events — the `filters.ALL` MessageHandler forwards them alongside fresh
messages, and `update.effective_message` collapses both. Today the
handler's INSERT path writes the edited text as a fresh row, which is
wrong: the original `(chat_id, message_id)` row already exists and its
`text` gets silently orphaned (the old row stays delivered, the new one
gets a new `id` and gets delivered as if it were a distinct message).

What Igor wants:

1. **Preserve the pre-edit `text` as `previous_text`** on the same DB row,
   not as a sibling row.
2. **Re-deliver the edited row to Claude** so Larry sees the diff — that's
   the whole point of the feature.
3. **Only surface edit meta when `edit_count > 0`** so the 99% fresh-message
   path doesn't gain noise-floor attributes.
4. Keep only the **most recent** pre-edit version. Full edit history is
   explicitly out of scope.
5. Idempotent migration. Old DBs (no columns) and migrated DBs (columns
   present) must both boot clean.

Key questions to double-check before implementing:

- **Does `MessageHandler(filters.ALL)` actually dispatch edited messages?**
  Yes, per PTB wiki (confirmed via `ctx7`): `filters.ALL` covers `message`,
  `edited_message`, `channel_post`, and `edited_channel_post`.
- **How do we distinguish edit vs fresh in the handler?** `update.message`
  is set for fresh, `update.edited_message` is set for edits;
  `effective_message` collapses both. Test: `is_edit =
  update.edited_message is not None and update.message is None`.
- **Is `ALTER TABLE ADD COLUMN` safe against a live writer?** Yes, in WAL
  mode with `DEFAULT` clauses that don't rewrite rows (our three columns
  all use constant defaults → table-scan-free).

## 3. Plan

Four commits on a fresh worktree off `upstream/main`:

1. **`feat(telegram_bot)`** — schema additions + `migrate_inbound_sync` +
   edit-detection branch in `handle_any_message`.
2. **`feat(server)`** — `InboundRow` type widened, `selectUndelivered`
   projects new columns, `deliverMessage` emits `previous_text` /
   `edit_count` / `edited_at` attrs when `edit_count > 0`, with an
   XML-attribute escaper for `previous_text`.
3. **`docs(agent-notes)`** — this file.

(The fourth "commit" in the original spec is this file's commit; merged
with the third.)

Run the migration against the live `inbound.db` after backing it up.
Smoke-test by counting rows / confirming columns. Do NOT restart
`telegram_bot.py` — Igor does lifecycle.

## 4. Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | **Detect edit via `update.edited_message is not None and update.message is None`** rather than `update.edited_message is not None`. | `effective_message` can coexist with `edited_message` in odd update shapes (channel-post edits). The `message is None` half pins us to the "this is specifically an edit of a DM/group message" case we care about. |
| 2 | **UPDATE the existing row by `(chat_id, message_id)`, not by `id`.** | The bot never sees the row id — only the Telegram message_id. A composite lookup on `(chat_id, message_id) ORDER BY id DESC LIMIT 1` is correct and narrow. |
| 3 | **Reset `delivered = 0` on edit.** | Without it, server.ts's catchup query (`WHERE delivered = 0 AND gate_action = 'allow'`) skips the updated row and Larry never sees the edit. With it, Larry gets a re-delivery carrying the diff via `previous_text`. |
| 4 | **Fall back to INSERT when the existing row isn't found.** | If bot state was wiped or the original arrived before this code was deployed, a fresh row for the edit is strictly better than dropping the message. Row gets `edit_count=1`, `previous_text=NULL` — the consumer can see "this was edited but we don't have the original" without silent data loss. |
| 5 | **Only emit edit-history attrs when `edit_count > 0`.** | Noise-floor. 99%+ of messages are never edited; gating the meta entries means fresh messages don't grow the tag. |
| 6 | **XML-attribute-escape `previous_text` only, not the other edit fields.** | `edit_count` is an integer string, `edited_at` is bot-generated ISO-8601 — neither can contain attribute-breaking characters. Only `previous_text` carries user-typed arbitrary bytes. |
| 7 | **Inline the migration in `telegram_bot.py`'s schema-init path**, not as a separate `migrate_*.py` script. | Per spec and prior convention in this repo (the `journal_pipeline` migrations are separate because those schemas have view dependencies and CHECK constraints that can't be widened via `ALTER TABLE`; ours don't). Keeps the migration lockstep with the schema. |
| 8 | **Use `ALTER TABLE ADD COLUMN` with `DEFAULT`** rather than recreating the table. | SQLite doesn't rewrite existing rows when a column is added with a constant `DEFAULT` — the scan stays O(1). Recreate-copy-drop-rename would need to drop the `idx_inbound_*` indexes and any shadow views, and would hold a write lock for ~O(rows). Not worth it here. |
| 9 | **`edit_count INTEGER NOT NULL DEFAULT 0`** — chose `NOT NULL` over nullable. | Distinguishing "never edited" (0) vs "unknown" (NULL) buys nothing; the default is always 0 and the semantics are identical. |

## 5. Outcomes

**Migration:** ran clean against `~/larry-telegram/inbound.db` (303 rows,
backed up to `inbound.db.bak-1776345224`). Three columns added. All
existing rows default to NULL/0. No row count change. No indexes needed
modification.

**Tests:** 22/22 pass in `tests/test_telegram_bot.py`, including a new
`test_migrate_inbound_adds_missing_columns` that (a) creates a v1-shape
DB, (b) seeds a row, (c) confirms the migration adds the three columns
and preserves existing data and (d) confirms a second call is a no-op.

**Code changes:**

- `skills/harden-telegram/server/telegram_bot.py`
  - `SCHEMA` grew three columns.
  - New `_INBOUND_MIGRATIONS`, `_existing_inbound_columns`,
    `migrate_inbound_sync`.
  - `init_db_sync` now calls `migrate_inbound_sync` after `executescript`.
  - `handle_any_message` branches on `is_edit`: UPDATE path preserves
    `previous_text`, bumps `edit_count`, sets `edited_at`, resets
    `delivered = 0`. Fresh-message path unchanged except for emitting
    `edit_count` / `edited_at` (both default-equivalent) in the INSERT
    to keep the row shape consistent.
- `skills/harden-telegram/server/server.ts`
  - `InboundRow` type gained the three new fields.
  - `selectUndelivered` projects the new columns.
  - New `escapeAttr()` helper.
  - `deliverMessage` emits `edit_count`, `previous_text` (escaped), and
    `edited_at` to the meta record only when `edit_count > 0`.
  - `INBOUND_SCHEMA` grew three columns (server.ts's defensive
    `CREATE TABLE IF NOT EXISTS` won't fire in practice — bot owns
    creation — but keeping it in lockstep protects against server.ts
    starting before the bot on a fresh host).

**Restart implications:** `telegram_bot.py` needs a restart to pick up
the new handler logic (`is_edit` branch). Without a restart the old
handler will keep INSERTing fresh rows on edits — not destructive, but
the feature doesn't activate. `server.ts` needs a restart too (via
`/reload-plugins`) to pick up the widened `SELECT` and meta emission;
but since it's ephemeral and the plugin cache auto-spawns a fresh
server on the next MCP call, this is cheap. **Per spec, I did NOT
restart either process — Igor owns lifecycle.**

## 6. Deferred

Not in scope for this PR, logged here so the next person knows what's
open:

1. **Full edit history (not just most-recent-prior).** Would need an
   `inbound_edits` child table with `(inbound_id, edit_idx, text, ts)`
   rows. Igor explicitly scoped to most-recent only; defer until he
   asks.
2. **Edit-re-delivery ordering.** If Claude has already seen row N and
   row N gets edited, the re-delivery re-uses row N's original `id` —
   so it arrives in the undelivered queue "out of order" relative to
   later messages (rows N+1 through current). This matches what Igor
   asked for (he wants to see the diff against the exact original), but
   if the coaching use case ever wants "fire a separate notification
   for the edit," we'd want an `inbound_events` append-only log.
3. **`edited_at` from Telegram's own `edit_date`.** Right now we stamp
   `datetime.now(UTC)` at the moment the bot observes the edit. The
   Telegram API actually supplies `edit_date` on edited messages (UNIX
   timestamp) — capturing that would distinguish "user edited 3s after
   sending" from "bot caught up from a queue after being down an hour."
   Defer; low value for coaching (time-of-observation is close enough).
4. **MCP instructions about edit history.** The server.ts `instructions`
   block that Claude sees on connect doesn't mention `previous_text` /
   `edit_count`. Could add a sentence: "If the tag has `edit_count > 0`
   and `previous_text`, the sender edited the message; the current tag
   body is the new text." Defer to a follow-up doc-only commit after
   Igor confirms the wire-format works end-to-end.
5. **Attachment edits.** Telegram lets users edit captions on photos /
   docs without changing the file itself. Today we read
   `msg.text or msg.caption` for the new text, so caption edits flow
   through correctly — but `previous_text` will carry the old caption,
   not the old photo. Attachment-level edit history is out of scope.
