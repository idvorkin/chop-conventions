# /delegate-to-other-repo Skill — Design Changelog

Spec: /home/developer/gits/chop-conventions/.worktrees/change-other-repo/docs/superpowers/specs/2026-04-12-delegate-to-other-repo-design.md

## Architect Review — 2026-04-12 23:28

### Convergence Tracking

| Pass | Changes |
| ---- | ------- |
| 1    | 14      |
| 2    | 13      |
| 3    | 8       |
| 4    | 0       |

### Pass 4 Changes

None. Convergence pass verified the spec as correct:

- Fork-detection decision tree correctly routes all four cases (vanilla two-remote, direct-push, chop-conventions' fork-as-origin pattern, canonical clone without fork).
- Shell recipes verified copy-paste correct via live shell: grep alternation on protection JSON, `symbolic-ref` failure path, slug-extraction sed against four URL forms, slug pipeline on English/Japanese/punctuation/>40-char inputs, collision loop math.
- `git -C` discipline holds — only two `cd` occurrences remain, both in subagent instructions (diagram + brief), none in parent path.
- Decoupling from `using-git-worktrees` still documented with rationale, no leak-back.

One deliberate non-edit: Case D's "look for any other remote" description sits inside the "one remote only" branch — label imprecision that degrades to correct behavior (empty search → STOP). Rewording would be gold-plating.

## Convergence Summary

**4 passes · converged** · Pass 1: 14 · Pass 2: 13 · Pass 3: 8 · Pass 4: 0

Arc: pass 1 caught a cascade (decoupling from `using-git-worktrees` + associated hardening), pass 2 propagated `git -C` discipline and resolved open questions, pass 3 caught five concrete correctness bugs in the shell recipes, pass 4 verified and found nothing.

**Assessment:** Ready for implementation.

### Pass 3 Changes

1. **Fixed broken Case C taxonomy in fork detection** (Brief → Git workflow + decision tree). Pass 2 described "auth = idvorkin-ai-tools but origin = idvorkin/chop-conventions" — backward. Verified empirically: chop-conventions' actual `origin` is `idvorkin-ai-tools/chop-conventions` (fork matching auth), no upstream, PRs go to `idvorkin/chop-conventions` (parent). Pass 2's tree would have routed this to Case B (direct push), silently landing the PR on the user's own fork. Restructured into 4 cases (A: two remotes; B: canonical-origin matching auth; C: fork-origin matching auth → PR to parent; D: canonical-origin NOT matching auth → look for sibling fork) with a `gh repo view --json isFork,parent` disambiguation step.
2. **Removed bogus `gh -R "$T"` invocations** (Target Resolution validation, Worktree Creation). Verified: there is no top-level `gh -R` flag, and `gh repo view` doesn't accept a filesystem path. Replaced with `git -C "$T" remote get-url origin` → parse owner/repo → `gh repo view <slug>` positional. Added explicit warning note so a future reader doesn't reintroduce the broken form.
3. **Fixed pipe-precedence bug in default-branch fallback chain** (Worktree Creation). Pass 2's `cmd_a || cmd_b | sed | sed || echo main` chain silently produces empty string when both fail (`||` binds only to the last `sed`, which "succeeds" on empty input). Verified: `T=/tmp/nonexistent ... echo main` returned `''`. Replaced with step-by-step assignment + `[ -z "$default_branch" ]` guards + final `${default_branch:-main}` default.
4. **Added `git remote set-head origin --auto` after fetch** (Worktree Creation + validation). Verified: plain `git fetch origin` does NOT refresh `refs/remotes/origin/HEAD`. A target whose default branch was renamed since clone yields a stale value from `symbolic-ref`. `set-head --auto` is idempotent when origin/HEAD already matches.
5. **Made the protection probe an actually-executed step** (Worktree Creation). Pass 2 described dry-run-push idea in a comment but the recipe never ran it. Replaced with executed `gh api repos/$slug/branches/$default/protection` that greps for `required_pull_request_reviews`/`required_status_checks` in JSON shape (not exit code, since `gh api` returns 0 even on 404). Documented the false-negative case (auth account lacks admin read → 404 → silent no-op) as accepted — worst case the local commit succeeds and the worktree push is unaffected.
6. **Pinned precise reproducible slug derivation rule** (Worktree Creation). Pass 2 left it as "kebab-case, ≤40 chars." Replaced with concrete shell pipeline (`tr` lowercase → `sed` non-alnum→`-` → trim → `cut -c1-40` → re-trim), explicit empty/non-ASCII fallback to `task-<timestamp>`, and collision loop probing `git show-ref --verify` against `refs/heads/delegated/<candidate>` with `-2`...`-9` suffixes. Verified all three branches empirically (English → slug, empty → timestamp, Japanese → timestamp).
7. **Detached-HEAD case made explicit** (Worktree Creation safety check + Failure Handling). Verified: `git branch --show-current` returns empty string (rc=0) on detached HEAD. Updated STOP message to print `'<detached>'` placeholder and added dedicated failure-mode entry so users get an actionable error instead of confusing "is on '', not 'main'".
8. **Open Questions section refreshed** to reflect new closed defaults (precise slug rule, branch-protection probe via gh api shape match, corrected fork-detection cases).

### Pass 2 Changes

1. **Parent uses `git -C <target>` instead of `cd <target>`** (User Experience step 2). Pass 1 left parent stranded in target's cwd, polluting shell state. Only the subagent cds now.
2. **Architecture diagram updated** to match — parent box shows `git -C <target> fetch origin` + "parent does NOT cd"; subagent box still cds as its first action.
3. **Worktree Creation recipe rewritten with `git -C "$T"` throughout**, plus `gh -R "$T"` for `gh repo view` and a fallback chain for default-branch resolution (`gh` → `symbolic-ref refs/remotes/origin/HEAD` → literal `main`). Removes the hidden assumption that `gh` always works.
4. **Fixed the gitignore-commit-on-wrong-branch bug** in Worktree Creation. Previous recipe blindly committed `.gitignore` on the target's current branch, which would silently land on a feature branch and disappear. New code requires target on default branch and STOPs otherwise. Adds heuristic note about PR-only/protected default branches.
5. **Added `diagnose.py` reuse instructions** in Brief → Git workflow. Subagent invokes `~/.claude/skills/up-to-date/diagnose.py --pretty | jq .remotes` from the worktree as a shortcut for URL-based fork classification, then runs manual checks for uncovered cases.
6. **Documented the Case C gap in `diagnose.py`** explicitly. After reading `diagnose.py` and `test_diagnose.py`, the single-canonical-origin case returns `is_fork_workflow=False` with no issues — the script has no concept of `gh auth status` and silently misclassifies the chop-conventions pattern. Brief and decision tree both call this out.
7. **Added fork-detection ASCII decision tree** as a reference subsection of Brief Format — placed _outside_ the embedded markdown brief block to avoid the nested-fence problem pass 1 fixed. Pass 1's concern 2 resolved: yes, a diagram helps; prose alone left the four cases tangled.
8. **Removed inner triple-backtick fences almost reintroduced inside the brief.** When drafting the diagnose.py shortcut and decision tree inside the brief block, pass 2 forgot pass 1's nested-fence lesson. Caught on re-read; brief is pure prose again.
9. **Validation-after-resolution checklist tightened** to use `git -C` consistently, resolve default branch with the same fallback chain as Worktree Creation, and check `origin/<default>` rather than hardcoding `origin/main`.
10. **Failure Handling expanded** with a new mode for "target on a non-default branch when `.worktrees/` needs ignoring," distinct from the existing "PR-only protected default branch" mode.
11. **V1-limitation note about CLAUDE.md worktree-location preference.** `using-git-worktrees` honors a target's `worktree.*director` CLAUDE.md preference; this skill hardcodes `.worktrees/delegated-<slug>` and silently ignores any preference. Documented as a deliberate v1 punt with a path forward.
12. **Frontmatter description un-hardcoded `origin/main`** → "the target's default branch" — matches the rest of the spec post-pass-1.
13. **Open Questions section refreshed** with three new closed-defaults entries: worktree location punt, fork-detection reuse strategy, and the Case C gap in diagnose.py.

### Pass 1 Changes

1. **Decoupled from `using-git-worktrees`** (Architecture, Parent responsibilities, Worktree Creation, diagram). The spec claimed to delegate worktree creation to that skill with `origin/main` as base, but `using-git-worktrees` branches off current HEAD, auto-runs `npm install`/`cargo build`, and runs baseline tests — none of which are correct for delegating a change off a fresh `origin/main` that might be a doc-only edit. Replaced with explicit `git worktree add -b <branch> origin/<default>` commands.
2. **Default-branch resolution added** (Target Resolution, Worktree Creation). Hardcoding `origin/main` breaks on repos with `master`/`trunk`. Added `gh repo view --json defaultBranchRef` resolution before worktree creation.
3. **Self-contained `.worktrees/` gitignore check** (Worktree Creation, Failure Handling). Made the pre-check explicit via `git check-ignore` and added a failure mode: if the target repo requires a PR for `main` and `.worktrees/` isn't ignored, parent must stop instead of committing directly to `main`.
4. **Fork workflow detection hardened** (Brief → Git workflow). Added single-remote-with-account-mismatch case (the chop-conventions pattern: auth as `idvorkin-ai-tools`, origin as `idvorkin/...`) and explicit STOP rule if the fork remote isn't wired up.
5. **Session log resolution fixed for worktree parents** (Session log resolution). Previous recipe used `git rev-parse --show-toplevel` for the project hash, which breaks when parent session runs inside a worktree — Claude Code hashes the launch cwd, not the repo root. Added cwd-first/toplevel-fallback sequence and "omit escape hatch if missing" fallback.
6. **Inference requires confirmation** (User Experience, Target Resolution). Added "always confirms before dispatch when target was inferred rather than explicit."
7. **Pre-commit hook reformat guidance** (Brief → Target repo conventions). Added `.pre-commit-config.yaml` to read list + "re-stage and re-commit; don't fight the formatter" instruction from chop-conventions CLAUDE.md.
8. **Enumerate commands inlined** (Brief → Target repo conventions). Replaced nested `bash fence inside outer `markdown fence (which would prematurely terminate the outer fence when brief is embedded in SKILL.md) with prose referencing `ls -1 skills/` and `ls -1 .claude/skills/`.
9. **Security note about session log escape hatch** (What lives where). Flagged as accepted v1 risk with "don't use this for sensitive repos" caveat.
10. **Commit trailer requirement** (Brief → Git workflow). Added explicit `Co-Authored-By` trailer instruction with "repo convention wins" override.
11. **Validation tightened** (Target Resolution). Added explicit post-resolution checks: `origin` remote exists and resolves, `origin/<default>` is reachable after fetch.
12. **Parent-side failure modes expanded** (Failure Handling). Split "worktree creation fails" into: fetch fails, gitignore conflict on default branch, worktree add fails.
13. **Frontmatter example added** (Files). Showed concrete `allowed-tools` frontmatter including `Agent` — without which dispatch silently breaks.
14. **Success criterion 4 rewritten** (Success Criteria). Previously claimed clean composition with `using-git-worktrees` — no longer true after change #1. Replaced with a state-pollution criterion.
