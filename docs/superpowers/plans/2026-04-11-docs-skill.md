# `docs` Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a machine-level `docs` skill that wraps `ctx7 library` + `ctx7 docs` so Claude fetches fresh third-party library documentation instead of relying on stale training memory.

**Architecture:** A single `SKILL.md` file under `skills/docs/` in chop-conventions, symlinked to `~/.claude/skills/docs`. No code, no tests — the skill is prose that gets loaded into Claude's context. Verification is a runtime sanity check against the `ctx7` CLI plus a README table update.

**Tech Stack:** Markdown + YAML frontmatter for the skill; `npx` + `ctx7` CLI as the wrapped tool; Bash for symlink + verification.

**Spec:** `docs/superpowers/specs/2026-04-11-docs-skill-design.md`

---

## File Structure

- Create: `skills/docs/SKILL.md` — the new skill file (prose guidance + frontmatter)
- Create: `~/.claude/skills/docs` — symlink into Claude's machine-level skill directory, pointing at the repo path
- Modify: `README.md` — add a row to the "Available Skills" table (line 82–90)

No test files. The skill has no executable behavior — it's prose loaded into context. Verification is done by (a) running `ctx7` against a real library to confirm the wrapped tool works, and (b) manually confirming in a future session that Claude fires the skill when expected.

---

### Task 1: Sanity-check that `ctx7 library` and `ctx7 docs` actually work

Before writing documentation that tells future-Claude to trust these commands, run them once against a known library so we're sure they return useful output and the doc examples in our SKILL.md are accurate.

**Files:** none (diagnostic only)

- [ ] **Step 1: Resolve a known library name to a Context7 ID**

Run:
```bash
npx ctx7 library react "useEffect cleanup function"
```

Expected: output that includes a library ID of the form `/facebook/react` (or similar) and a short description. If the command errors out or returns nothing useful, STOP the plan and investigate — the skill cannot exist if the underlying tool is broken.

- [ ] **Step 2: Fetch docs for the resolved ID**

Run:
```bash
npx ctx7 docs /facebook/react "useEffect cleanup function"
```

Expected: prose/code snippets about `useEffect` cleanup functions, several hundred to a few thousand tokens of docs. If this errors or returns junk, STOP and investigate.

- [ ] **Step 3: Confirm a stdlib query is NOT what we want**

Quick mental check: the skill tells Claude to SKIP stdlib questions. Verify that the examples above are actual third-party libraries, not stdlib. (`react` ✓, not stdlib.) No command needed — just confirming we're not accidentally encouraging ctx7 use for built-ins.

No commit in this task — diagnostic only.

---

### Task 2: Create the skill directory and SKILL.md

**Files:**
- Create: `skills/docs/SKILL.md`

- [ ] **Step 1: Create the directory**

Run:
```bash
mkdir -p /home/developer/gits/chop-conventions/skills/docs
```

Expected: no output, directory now exists.

- [ ] **Step 2: Write SKILL.md with exactly this content**

Create `/home/developer/gits/chop-conventions/skills/docs/SKILL.md`:

````markdown
---
name: docs
description: Use when answering questions about or writing code against a third-party library/framework. Fetches fresh, authoritative documentation via Context7 (`ctx7`) instead of relying on stale training data. Fires both reactively ("how do I X with library Y") and proactively (about to write library code and unsure of current API).
---

# Docs (Context7 Library Lookup)

Fetch fresh, authoritative third-party library documentation via the `ctx7` CLI instead of guessing from (possibly stale) training memory.

## When to use

**USE when:**
- The user asks "how do I X with library Y" for a named third-party library or framework.
- You are about to write code against a named third-party library and are not 100% sure the API still matches your training memory.
- A question is version-specific ("in the latest FastAPI…", "post-v18 React…").

**SKIP when:**
- The question is about a language stdlib (Python stdlib, JS built-ins, Go stdlib, etc.) — you already know these and ctx7 is wasted tokens.
- It's a general CS/programming concept, not a library.
- The code in question lives in the current repo — read the source directly instead.
- You've already fetched the same library+topic earlier in this session — reuse that answer.

## Two-step workflow

Context7 splits lookup into a name→ID resolution step and a docs-fetch step.

### Step 1: Resolve library name to a Context7 ID

```bash
npx ctx7 library <name> "<query>"
```

The query is optional but strongly recommended — Context7 uses it to rank candidate libraries by relevance to what you're actually asking. Example:

```bash
npx ctx7 library react "useEffect cleanup function"
# → /facebook/react (or similar)
```

### Step 2: Fetch docs for the resolved ID

```bash
npx ctx7 docs <libraryId> "<query>"
```

Example:

```bash
npx ctx7 docs /facebook/react "useEffect cleanup function"
```

Returns curated doc snippets ranked for the query.

### Shortcut

If you already know the library ID from earlier in the session or from an obvious mapping (`/facebook/react`, `/anthropics/anthropic-sdk-python`), skip step 1 and go straight to `ctx7 docs`.

## Efficiency rules

- **Don't re-query the same library+topic twice in one session.** Reuse the first result from earlier in the conversation.
- **Prefer specific queries over broad ones.** `"useEffect cleanup"` beats `"hooks"`; `"scan_parquet glob pattern"` beats `"polars io"`.
- **`--json` is available** on both commands if you need structured output, but plain text is fine for reading into your own context.

## ctx7 vs WebFetch

- Reach for `ctx7` first whenever the target is a **named library** — it returns curated, query-ranked snippets rather than raw HTML.
- Fall back to `WebFetch` for arbitrary URLs (blog posts, GitHub issues, RFCs, changelogs not yet indexed by Context7).

## Auth / setup

- The first `npx ctx7` invocation auto-installs the package; no manual setup needed.
- Works anonymously. If you hit rate limits or auth errors, run `npx ctx7 login` and retry.

## Common mistakes

- **Skipping the `library` step and guessing the ID.** IDs are not always predictable (`/pola-rs/polars`, not `/polars/polars`). Resolve first unless you're certain.
- **Using ctx7 for stdlib questions.** Wasted tokens — you already know `list.sort()`.
- **Re-running the same query multiple times in one session** instead of reusing the first output.
- **Forgetting the query argument** on `ctx7 library` — the rankings get noticeably worse without it.
````

Expected after writing: file exists at `/home/developer/gits/chop-conventions/skills/docs/SKILL.md`, starts with `---\nname: docs\n`, ends with the "Common mistakes" section.

- [ ] **Step 3: Verify the file parses as the expected shape**

Run:
```bash
head -5 /home/developer/gits/chop-conventions/skills/docs/SKILL.md
```

Expected first 5 lines:
```
---
name: docs
description: Use when answering questions about or writing code against a third-party library/framework. Fetches fresh, authoritative documentation via Context7 (`ctx7`) instead of relying on stale training data. Fires both reactively ("how do I X with library Y") and proactively (about to write library code and unsure of current API).
---

```

If the frontmatter block is malformed or `name` is wrong, fix the file.

No commit yet — we'll bundle the SKILL.md, symlink verification, and README update into a single commit at the end.

---

### Task 3: Symlink the skill into Claude's machine-level skill directory

**Files:**
- Create (symlink): `~/.claude/skills/docs` → `/home/developer/gits/chop-conventions/skills/docs`

- [ ] **Step 1: Check for any existing `docs` skill and abort if present**

Run:
```bash
ls -la ~/.claude/skills/docs 2>/dev/null
```

Expected: "No such file or directory" (or equivalent). If anything exists there, STOP and ask the user before proceeding — we might clobber an existing skill.

- [ ] **Step 2: Create the symlink**

Run:
```bash
ln -s /home/developer/gits/chop-conventions/skills/docs ~/.claude/skills/docs
```

Expected: no output.

- [ ] **Step 3: Verify the symlink resolves**

Run:
```bash
ls -la ~/.claude/skills/docs && cat ~/.claude/skills/docs/SKILL.md | head -5
```

Expected:
- `ls` shows `docs -> /home/developer/gits/chop-conventions/skills/docs`
- `cat` shows the same frontmatter block from Task 2 Step 3.

If either fails, the symlink is broken — fix before moving on.

No commit — nothing in git changed from the symlink (it's outside the repo).

---

### Task 4: Add the skill to the README skills table

**Files:**
- Modify: `README.md:82-90`

- [ ] **Step 1: Read the current skills table**

The current table lives at lines 82–90 of `README.md`:

```markdown
| Skill | Scope | Description |
|---|---|---|
| `gen-image` | machine | Generate illustrations via Gemini image API |
| `gist-image` | machine | Host images on GitHub gists for PRs/issues |
| `image-explore` | machine | Brainstorm and compare visual directions |
| `learn-from-session` | machine | Extract durable lessons from a session into the right CLAUDE.md files |
| `machine-doctor` | machine | Diagnose system health, kill rogue processes |
| `showboat` | machine | Create executable demo documents with screenshots |
| `up-to-date` | machine | Sync git repo with upstream |
```

- [ ] **Step 2: Insert a `docs` row in alphabetical order**

The alphabetical position is between (nothing above `d` currently exists — `gen-image` starts with `g`) — so `docs` goes FIRST in the table, right after the header separator.

Use Edit to replace the line:
```
| `gen-image` | machine | Generate illustrations via Gemini image API |
```
with:
```
| `docs` | machine | Fetch fresh library/framework docs via Context7 (`ctx7`) |
| `gen-image` | machine | Generate illustrations via Gemini image API |
```

- [ ] **Step 3: Verify the table renders in order**

Run:
```bash
sed -n '80,95p' /home/developer/gits/chop-conventions/README.md
```

Expected: the table with `docs` as the first data row, `gen-image` second, others unchanged.

---

### Task 5: Final verification and commit

**Files:** (none created/modified in this task — verification + commit only)

- [ ] **Step 1: Verify repo state is clean and contains only the expected changes**

Run:
```bash
cd /home/developer/gits/chop-conventions && git status
```

Expected output includes exactly:
- `new file: skills/docs/SKILL.md`
- `modified: README.md`

No other files should appear. If unexpected changes show up, investigate before committing.

- [ ] **Step 2: Diff-check the README change**

Run:
```bash
cd /home/developer/gits/chop-conventions && git diff README.md
```

Expected: exactly one added line, the `docs` row, in the skills table.

- [ ] **Step 3: Run a final sanity check through the symlink**

Run:
```bash
head -5 ~/.claude/skills/docs/SKILL.md
```

Expected: same frontmatter block as Task 2 Step 3. This confirms the symlink + file are both in place and consistent.

- [ ] **Step 4: Commit**

Run:
```bash
cd /home/developer/gits/chop-conventions && git add skills/docs/SKILL.md README.md && git commit -m "$(cat <<'EOF'
docs skill: wrap ctx7 library/docs for fresh library documentation

New machine-level skill that tells Claude to reach for Context7
(`npx ctx7 library` + `npx ctx7 docs`) when writing code against a
named third-party library or answering "how do I X with library Y"
questions. Avoids stale training-data guesses.

Installed via symlink at ~/.claude/skills/docs.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

Expected: commit succeeds, one commit created containing both files.

- [ ] **Step 5: Confirm final state**

Run:
```bash
cd /home/developer/gits/chop-conventions && git log -1 --stat
```

Expected: the new commit, showing `README.md` (1 insertion) and `skills/docs/SKILL.md` (new file).
