---
name: learn-from-session
description: Extract durable lessons from a completed Claude session and codify them in the right CLAUDE.md files. Use at the end of a long session, after a bug hunt that surfaced an environmental quirk, or when the user asks "what can we learn from this session". Proposes brief, filtered additions and asks for approval before editing.
allowed-tools: Bash, Read, Edit, Glob, Grep
---

# Learn From Session

Extract durable lessons from the current session and apply them to the right CLAUDE.md file(s). Igor-specific counterpart to `claude-md-management:revise-claude-md` — aware of this dev environment's repo layout (larry-blog / settings / chop-conventions) and quirks (OrbStack VM, no systemd, fork workflow).

## When to use

- User says "what can we learn from this session", "apply to CLAUDE.md", or similar
- End of a long session where multiple corrections piled up
- After a bug hunt that uncovered a non-obvious environmental constraint
- After shipping a new pattern worth standardizing (e.g., `~/.zshrc` boot hook, exclude list for signal-based scripts)

**Do NOT use** when the session was routine — not every session produces CLAUDE.md-worthy material. If the reflection step yields nothing durable, say so and stop.

## The Iron Rule: Brevity

CLAUDE.md files are loaded into every prompt on every turn. Every line costs tokens on every future call. Additions must earn their cost.

Target: **≤5 lines per addition**, bullets preferred. One concept per line.

## Step 0: Identify Target Repos and Files

Known CLAUDE.md homes for Igor's environment:

```bash
find ~/gits/larry-blog ~/settings ~/gits/chop-conventions -maxdepth 3 -name CLAUDE.md 2>/dev/null
```

Scope routing:

| Type of lesson | Home |
|---|---|
| OrbStack/VM environment quirk, bash pattern, tool alias, shell pitfall | `~/settings/CLAUDE.md` |
| Skill format / agent rules / general coding rules / process-safety / PR hygiene | `~/gits/chop-conventions/CLAUDE.md` |
| Blog content workflow / post placement / TOC / AI slop label / permalink rules | `~/gits/larry-blog/CLAUDE.md` |
| Something specific to a project you were working in | That project's `CLAUDE.md` |

If a lesson spans scopes, pick the most specific home.

## Step 1: Reflect

Walk back through the session and answer these prompts explicitly. Each should have either a concrete answer or "none in this session":

1. **What environmental constraint surprised us?** (e.g., `/sys/fs/cgroup` read-only, no systemd as PID 1, tool aliased to a non-standard binary, file doesn't exist on first run)
2. **What safety gotcha almost shipped?** (e.g., lifeline process missing from an exclude list, race condition, signal handler timing, destructive default)
3. **What was the *right* place for content we initially put in the *wrong* place?** (e.g., blog post choice, skill size, file hierarchy, module boundary)
4. **What pattern worked well enough to codify?** (e.g., idempotent boot hook, dependency check before main loop, smoke test with low thresholds, rebase-before-PR)
5. **What tool invocation ate time before landing on the right one?** (e.g., `ps --sort` failed because `ps` is aliased to `procs`; `pgrep` matched the current shell because the pattern wasn't anchored)

If fewer than two prompts have real answers, the session likely doesn't need CLAUDE.md updates. Tell the user and stop.

## Step 2: Filter by Durability

Keep only lessons that are:

- **Durable** — will be true in future sessions, not specific to this bug or commit
- **Non-obvious** — not already in the default Claude Code system prompt (e.g., "prefer editing existing files" is already there)
- **Actionable** — tells a future Claude what to do or not do, not just a retrospective story

Discard:

- One-off fix narratives ("in this session we fixed X by doing Y")
- Anything already in the repo's CLAUDE.md, the default system prompt, or chop-conventions guardrails
- Warnings about behavior that a self-aware Claude would discover in one tool call
- Things that would be true of any Linux box (CLAUDE.md should capture what's special here)

## Step 3: Draft Additions

For each surviving lesson, write the addition in this shape:

```
### Update: <absolute path to CLAUDE.md>

**Why:** <one-line justification citing this session's cost or risk>

​```diff
+ <the addition — ≤5 lines, prefer bullets, no preamble>
​```
```

Style rules:

- **No narrative.** Don't say "in this session...". Write as a durable rule.
- **No preamble.** Jump straight to the fact or the instruction.
- **Use code fences for commands and file paths.** Make them skimmable.
- **Bold only the one key term** a reader would grep for. Not the whole bullet.
- **Avoid duplication.** If a nearby CLAUDE.md section already touches the topic, extend it instead of adding a new section.

## Step 4: Present and Approve

Show all proposed diffs in one message, grouped by target file. End with a single explicit ask: "Apply these?" Wait for explicit approval before editing any file. Don't partial-apply unless the user picks specific ones.

## Step 5: Apply

On approval:

1. **Create a feature branch per repo** — naming: `claude-md-<short-topic>` or `claude-md-session-<date>`
2. **Apply the edits** using the Edit tool (not Write — these are targeted insertions)
3. **Commit per repo** with a descriptive message that cites the session learning, not "update CLAUDE.md". Include the standard `Co-Authored-By` trailer.
4. **Push to origin** (the fork) and **create a PR against upstream** — check `gh auth status` first to confirm the fork workflow applies
5. **Report PR URLs with `/files` appended** so the user can skim the diff directly

If the user says "just commit locally, no PR" or "just do it on main", follow that instead — but ask if unclear.

## Step 6: Optional — Also Update a Related Skill

If the lesson is a recurring pattern that deserves automation (e.g., a new type of check, a new safety rule that should be enforced by tooling), consider whether an existing skill can codify it. Flag this to the user as a follow-up, don't action it in the same session without explicit direction.

## Anti-patterns

Red flags that you're adding noise to CLAUDE.md:

- **Narrative phrasing** — "We learned that X...", "In this session we discovered...", "Remember that..."
- **Vague generalities** — "Always test before deploying", "Be careful with X"
- **Already-covered territory** — "Use feature branches" (already in guardrails), "Prefer editing existing files" (already in system prompt)
- **One-off bug postmortems** — the bug is fixed; the narrative belongs in the commit message, not CLAUDE.md
- **Entries longer than 5 lines** without a strong reason
- **Verbatim code blocks** that exceed ~10 lines — link to the real file instead
- **Anything a future Claude would trivially discover** — `ls`, `pwd`, `git status` etc.

## Related

- `claude-md-management:revise-claude-md` — upstream generic version (this skill is an Igor-aware wrapper)
- `claude-md-management:claude-md-improver` — audits CLAUDE.md quality across repos; use when you want to *improve* existing content rather than *add* new content
