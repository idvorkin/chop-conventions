# delegate-to-other-repo — Changelog

Spec: /home/developer/gits/chop-conventions/skills/delegate-to-other-repo/SKILL.md

## Architect Review — 2026-04-17 03:22

### Convergence Tracking

| Pass | Changes |
| ---- | ------- |
| 1    | 16      |
| 2    | 7       |
| 3    | 0       |

### Pass 3 — 0 changes (converged)

Reviewed edge cases: `resolve_target_path` with trailing-slash input, whitespace in `--target`, non-ASCII `task` strings in JSON output, `resolve_unique_slug` timestamp-fallback collisions, module docstring vs code, phase numbering across SKILL.md / module docstring / CLI help, cleanup-command consistency across Phase 4 / Phase 5 / failure table, the 13-key JSON contract vs `run_prepare`'s result init, `allowed-tools` frontmatter keeping `Bash`, `_fake_git` fixture not pinning the pass-2 `cwd_physical` fix. All judged correct-as-is or genuinely out of scope.

No edits. **Converged at pass 3.** Total: 23 substantive changes across passes 1–2. Pass 4 not required.

### Pass 2 — 7 changes

1. **prepare_dispatch.py** — `_git(".", "rev-parse", "--show-toplevel")` → `_git(cwd_physical, ...)`. Toplevel lookup now uses caller's `cwd` arg, not process cwd. Real bug: tests/harnesses passing a different `cwd` silently read the wrong repo's toplevel.
2. **prepare_dispatch.py** — added `git check-ignore -q .worktrees/x` verify after `_ensure_exclude` writes. Matches load-bearing sanity check from worktree-recipe.md; surfaces `errors` entry on mismatch. Closes pass-1 open item.
3. **prepare_dispatch.py** — `_wrote, exclude_err = _ensure_exclude(...)` → `_, exclude_err`. Honest pyright fix: returned `wrote` bool is genuinely unused.
4. **prepare_dispatch.py** — `# pyright: ignore[reportUnusedFunction]` on inner Typer `main` callback. Pyright can't see Typer decorator registration; silencing false positive beats breaking the decorator pattern.
5. **prepare_dispatch.py** — added check-ignore verify line to module docstring's step list. Keeps SKILL.md failure table / worktree-recipe / docstring in agreement.
6. **SKILL.md** — added failure-table row for `check-ignore .worktrees/x` failing after exclude write. Parent has deterministic recovery if new verify step trips.
7. **test_prepare_dispatch.py** — added `test_upstream_fetch_failure_appends_warning_and_falls_back` + `test_owner_repo_slug_error_populates_stable_keys` (iterates all 13 top-level keys on error early-return), fixed pyright unused-name hits (`_s`, `_kw`, `_target`, `_ = check`), tightened worktree_path assertion.

**Pass 2 assessment:** ready for implementation. 39/39 tests pass, pyright clean. Agent declared convergence, but 7 > 2 threshold per skill rule — launching pass 3 for verification.

### Pass 1 — 16 changes

1. **prepare_dispatch.py** — split fatal `errors` from non-fatal `warnings`: upstream-fetch failure moved to `warnings` so parent doesn't abort on recovered condition. Contract said "non-empty errors ⇒ helper stopped" but code violated it.
2. **prepare_dispatch.py** — declared `target`, `slug`, `warnings` as stable top-level keys in the `result` shape. `target`/`slug` were populated mid-function without appearing in init; `warnings` needs to exist (empty list) uniformly.
3. **SKILL.md** — rewrote "Output shape" section: fully documented every stable JSON key (added `target`, `warnings`, `task`, `dry_run`), exit-code semantics (exit 1 iff errors, JSON always emitted), error-vs-warning split. JSON contract was underspecified.
4. **SKILL.md** — removed 12-bullet "what the helper does" list duplicating helper docstring and worktree-recipe.md. Three-way prose drift is guaranteed; replaced with pointer + diff-together reviewer instruction.
5. **SKILL.md** — added "If dispatch fails after the worktree exists" subsection in Phase 4. Helper is mutating; Agent-tool failure after helper success left orphan worktree+branch with no cleanup guidance.
6. **SKILL.md** — expanded Phase 5 cleanup: explicit branch teardown command alongside worktree removal. Old note only mentioned `git worktree remove`; `delegated/<slug>` branch was orphaned.
7. **SKILL.md** — made same-repo guard in Phase 1c-bis explicit as "must run BEFORE helper". Helper has no concept of parent cwd; without guard it sets up worktree in current repo.
8. **SKILL.md** — new "Concurrency — parallel delegations to same target" section: what's safe (slug collision, atomic worktree add, benign exclude-append race) vs what isn't (overlapping-file scoping, subagent-slot budget). Was unaddressed.
9. **SKILL.md** — expanded parent-side failure-handling table: `git fetch upstream` warning row, Agent-tool dispatch-failure row, rewrote existing rows to reference helper's `errors`/`warnings`/`null` fields so recovery is deterministic from JSON alone.
10. **SKILL.md** — replaced 25-line session-log bash block with pointer to helper's `session_log` field (full rule preserved in worktree-recipe.md for manual fallback). Helper already resolves; bash was dead rot-prone instruction.
11. **SKILL.md** — renamed Phase 2 heading to "Create the worktree (and finalize Phase 1 validation)" with lede clarifying helper covers Phase 1d+2, not just Phase 2.
12. **SKILL.md** — softened "unit tests pin pure-function behavior byte-for-byte" claim in Manual fallback. Byte-for-byte isn't true and tests don't enforce it; honest framing directs maintainers to diff.
13. **prepare_dispatch.py** — bumped `requires-python = ">=3.11"` → `">=3.13"`, dropped `from __future__ import annotations`. chop-conventions CLAUDE.md requires 3.13 as default for new uv-shebang scripts.
14. **test_prepare_dispatch.py** — dropped `__future__` import, added `result["warnings"] == []` assertion on happy-path dry-run. Codifies warnings key as stable contract.
15. **prepare_dispatch.py** — CLI `help=` and module docstring corrected: "Phases 1d + 2", not "Phases 1-3" (helper doesn't build brief or dispatch).
16. **brief-template.md** — clarified parent substitutes slug into brief (rather than subagent deriving from branch name). Reduces subagent error surface.

**Pass 1 assessment:** close to ready, one more pass recommended. Open items for pass 2: no non-dry-run integration test, helper doesn't `git check-ignore` after `_ensure_exclude` write, `allowed-tools` frontmatter still lists `Bash` (leaving alone — still needed for Phase 1c-bis + manual fallback), helper silent on slow fetch.
