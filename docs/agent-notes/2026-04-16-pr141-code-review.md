# 2026-04-16 — PR 141 Code Review (inbound edit history)

Reasoning trail for the review of [PR #141](https://github.com/idvorkin/chop-conventions/pull/141).
Follows the six-section schema from the dogfood doc at
`2026-04-16-inbound-edit-history.md`: user request → interpretation →
plan → decisions → outcomes → deferred.

---

## 1. User request

Igor (via Telegram msg id 2031): "Run full code review on this:
<https://github.com/idvorkin/chop-conventions/pull/141>". Brief cited
the `inbound-edit-history` branch, enumerated eight review angles
(schema migration, edit detection, concurrency, XML escaping, tests,
rollback, observability, privacy), and instructed that blockers and
should-fix items be applied in the worktree with atomic commits, while
nice-to-have items are deferred as PR comments. PR must remain DRAFT;
no signature (idvorkin/* target).

## 2. Interpretation

The feature under review preserves the pre-edit text when a Telegram
user edits a message, exposing it to Claude via new `previous_text` /
`edit_count` / `edited_at` XML attrs on the `<channel>` block. The
review scope is correctness + observability, not new features.

Reading the branch end-to-end, the schema migration is clean
(idempotent `ALTER TABLE ADD COLUMN` with constant defaults; WAL-safe
against live readers). The reasoning doc is thorough. Tests cover the
migration path well.

Two correctness gaps stood out:

1. **Gate-action on edit was unchecked.** If Igor revoked a sender's
   access between the original (allowed) message and the edit,
   `delivered = 0` would be reset on the existing allow-gated row and
   server.ts would re-deliver an unauthorized edit. Low-likelihood,
   high-impact.
2. **`is_edit` only covered `edited_message`.** Per the reasoning
   doc's own citation, `filters.ALL` dispatches `edited_channel_post`
   too. For those updates `is_edit` would evaluate False, taking the
   INSERT path and creating a duplicate row with the same
   (chat_id, message_id). Silent data-shape corruption.

Also: the review brief explicitly asked for tests covering the
edit-UPDATE code path (not just the migration). The PR had none — the
migration test is the only new test. I took the "add them, don't just
flag" instruction literally.

## 3. Plan

1. Read all four files end-to-end before forming findings.
2. Severity-sort findings into BLOCKER / SHOULD-FIX / NICE-TO-HAVE.
3. Fix SHOULD-FIX items in-place:
   - Gate-guard the `delivered = 0` reset on edits.
   - Extend `is_edit` to cover `edited_channel_post` / `channel_post`.
   - Add `prev_len` / `new_len` to the edit log line.
4. Add tests for the edit-UPDATE SQL path (allow + denied-redelivery)
   and for the `is_edit` detection against all four update shapes.
5. Update the reasoning doc with the new decisions (4a, 4b).
6. Three atomic commits: fix, test, doc. Push to the PR branch.
7. Post deferred items as a PR comment.

## 4. Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | **Fix privilege-escalation-light scenario by checking current gate on edit.** | Rare edge case but low-effort fix. Preserves the diff for audit (UPDATE still runs) while blocking re-delivery to Claude. |
| 2 | **Extend `is_edit` to `edited_channel_post` too.** | Matches the `filters.ALL` dispatch behavior documented by PTB. Without it, channel-post edits silently INSERT duplicates. |
| 3 | **Test the edit SQL directly, not via `handle_any_message`.** | Mocking PTB Update objects is brittle; the database-side contract (what columns the UPDATE touches + leaves alone) is what matters for the "row shape on disk" invariant. Pure-SQL test is more robust to refactors in handler wiring. |
| 4 | **Do NOT change the BEGIN-IMMEDIATE-without-try-rollback pattern.** | Pre-existing in this file (callback_query handler, attachment UPDATE path). Not introduced by this PR. Worth tracking separately; out of scope for a review pass. |
| 5 | **Defer rollback-script ask.** | SQLite can drop columns via create-new/copy/drop/rename (per CLAUDE.md). If Igor needs to roll back, the `journal_pipeline/migrate_*.py` files are the reference shape. Writing a rollback script speculatively wastes effort; he can ask when he needs one. |
| 6 | **Defer `edited_at` from Telegram's `edit_date`.** | Already in the reasoning doc's deferred list (§6 item 3). The bot's observation timestamp is close enough for coaching. |
| 7 | **Defer MCP `instructions` update about edit meta.** | Already in the reasoning doc's deferred list (§6 item 4). Worth doing after end-to-end wire-format validation, not speculatively. |
| 8 | **Do NOT add TypeScript tests for `escapeAttr`.** | No existing TS test infra in this repo; setting it up for one function is over-kill. Function is 7 lines, tested by Claude's own XML parser in production. Defer. |

## 5. Outcomes

**Findings table:**

| Severity | Issue | Status |
|----------|-------|--------|
| SHOULD-FIX | Edit UPDATE resets `delivered=0` even when current gate is drop/pair | Fixed (commit b364e28) |
| SHOULD-FIX | `is_edit` misses `edited_channel_post` → silent duplicate INSERT | Fixed (commit b364e28) |
| SHOULD-FIX | No tests for edit-UPDATE SQL or channel-post detection | Fixed (commit cf00cd1) |
| SHOULD-FIX | Edit log lacks `prev_len`/`new_len` observability | Fixed (commit b364e28) |
| NICE | Rollback script not present | Deferred (PR comment) |
| NICE | `edited_at` uses bot-observation time, not Telegram `edit_date` | Deferred (already in reasoning doc) |
| NICE | `previous_text` has no max-length truncation (can expand ~6× under escapeAttr) | Deferred (PR comment) |
| NICE | MCP `instructions` block doesn't mention edit meta | Deferred (already in reasoning doc) |
| NICE | BEGIN-IMMEDIATE paths lack explicit rollback on partial failure | Pre-existing pattern; noted in review but not fixed in this PR |

**Tests:** 22 → 25. All pass.

**Commits added on top of the PR head (`66bde93`):**

- `b364e28 fix(telegram_bot): gate-guard re-delivery on edits + detect channel-post edits`
- `cf00cd1 test(telegram_bot): cover edit-UPDATE path + channel-post detection`
- `caa052e docs(agent-notes): record gate-guard + channel-post edit decisions`

**Privacy:** this doc (per reasoning-audit convention) intentionally
paraphrases Igor's request rather than quoting. No Telegram message
bodies are stored here. The underlying schema change stores user text
in the local DB, which is expected — that's the whole point.

## 6. Deferred

Posted as a single PR comment after push. Full list:

1. **Rollback script.** SQLite's `ALTER TABLE DROP COLUMN` requires
   3.35+; on older runtimes use the journal_pipeline migrate_*.py
   idiom (create-new/copy/drop/rename). Speculative effort without
   clear signal Igor needs one.
2. **`edited_at` from Telegram's `edit_date` field.** Already tracked
   in the PR's own reasoning doc §6 item 3. Time-of-observation is
   good enough for coaching; Telegram's own timestamp would only
   distinguish "edit within 3s" from "bot caught up from queue."
3. **`previous_text` max-length truncation.** A pathological 4096-char
   edit with all `"` characters would `escapeAttr` to ~24KB. Claude's
   XML parser handles it, but it's a noise-floor concern. Cap at,
   e.g., 2000 chars + ellipsis if this becomes a problem.
4. **MCP `instructions` update.** The on-connect prompt Claude sees
   doesn't mention `edit_count` / `previous_text`. Worth adding a
   sentence after end-to-end validation confirms the wire format.
5. **`escapeAttr` unit tests.** Requires bootstrapping a TS test
   harness for this repo. Function is small; defer until a second TS
   function needs testing.
6. **BEGIN-IMMEDIATE rollback on exception.** Pre-existing pattern
   across three sites in `telegram_bot.py`. A failing UPDATE/INSERT
   between `BEGIN IMMEDIATE` and `commit()` leaves the long-lived
   connection in a dangling transaction, causing the NEXT
   BEGIN IMMEDIATE to error. Worth a dedicated bead — not this PR.
