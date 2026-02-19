# Trim Redundant Conventions — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Remove 6 convention files superseded by Claude Code plugins, preserving unique content in surviving files.

**Architecture:** Move unique nuggets first, then delete redundant files, then update the index. Order matters — preserve before delete.

**Tech Stack:** Git, markdown files only. No code changes.

---

### Task 1: Add refactoring guardrail to guardrails.md

**Files:**

- Modify: `dev-inner-loop/guardrails.md:9-13`

**Step 1: Add the guardrail line**

In `dev-inner-loop/guardrails.md`, add after the "Any action that loses work" bullet (line 13):

```markdown
- **Big refactors during bug fixes** - If you discover an architectural issue while fixing a bug, ask user before refactoring: "I found [issue]. Address now or just fix the immediate bug?"
```

**Step 2: Commit**

```bash
git add dev-inner-loop/guardrails.md
git commit -m "Add refactoring guardrail from bug-investigation.md"
```

---

### Task 2: Move status line config to beads.md

**Files:**

- Modify: `dev-setup/beads.md` (append before Resources section at line 284)

**Step 1: Add Claude Code integration section**

Before the `## Resources` section in `dev-setup/beads.md`, add:

```markdown
## Claude Code Integration

### Status Line Configuration

Show current branch, in-progress issue ID, and truncated title in Claude Code's status line.

Add to `~/.claude/settings.json`:

\`\`\`json
{
"statusLine": {
"type": "command",
"command": "input=$(cat); cwd=$(echo \"$input\" | jq -r '.workspace.current_dir'); cwd_short=$(echo \"$cwd\" | sed \"s|^$HOME|~|\"); branch=\"\"; bd_issue=\"\"; if [ -d \"$cwd/.git\" ]; then branch=$(cd \"$cwd\" && git branch --show-current 2>/dev/null); fi; if command -v bd >/dev/null 2>&1 && [ -d \"$cwd/.beads\" ]; then bd_line=$(cd \"$cwd\" && bd list --status=in_progress 2>/dev/null | head -1); if [ -n \"$bd_line\" ]; then bd_id=$(echo \"$bd_line\" | awk '{print $1}'); bd_title=$(echo \"$bd_line\" | sed 's/.*- //' | cut -c1-30); bd_issue=\" [${bd_id}: ${bd_title}]\"; fi; fi; printf '\\033[01;32m%s@%s\\033[00m:\\033[01;34m%s\\033[00m \\033[33m(%s)\\033[00m%s' \"$(whoami)\" \"$(hostname -s)\" \"$cwd_short\" \"$branch\" \"$bd_issue\""
}
}
\`\`\`

**Requires:** `jq` installed, `bd` in PATH, project has `.beads/` directory.

### Session Hooks

Auto-prime beads context on session start and before compaction:

\`\`\`json
{
"hooks": {
"PreCompact": [
{
"matcher": "",
"hooks": [{ "type": "command", "command": "bd prime" }]
}
],
"SessionStart": [
{
"matcher": "",
"hooks": [{ "type": "command", "command": "bd prime" }]
}
]
}
}
\`\`\`
```

**Step 2: Commit**

```bash
git add dev-setup/beads.md
git commit -m "Move Claude Code integration config from beads-claude-code.md to beads.md"
```

---

### Task 3: Delete redundant files

**Files:**

- Delete: `dev-inner-loop/bug-investigation.md`
- Delete: `dev-inner-loop/before-implementing.md`
- Delete: `dev-inner-loop/workflow-recommendations.md`
- Delete: `dev-setup/beads-claude-code.md`
- Delete: `dev-setup/chop-logs.md`
- Delete: `marketplace.md`

**Step 1: Remove all 6 files**

```bash
git rm dev-inner-loop/bug-investigation.md dev-inner-loop/before-implementing.md dev-inner-loop/workflow-recommendations.md dev-setup/beads-claude-code.md dev-setup/chop-logs.md marketplace.md
```

**Step 2: Commit**

```bash
git commit -m "Remove conventions superseded by Claude Code plugins"
```

---

### Task 4: Update the index file

**Files:**

- Modify: `dev-inner-loop/a_readme_first.md`

**Step 1: Replace contents of a_readme_first.md**

Replace the entire file with:

```markdown
This directory contains instructions to follow.
Do not generate .cursorrules from it.
Do not talk about how you'll use the rules, just use them

## Core Conventions (read and follow)

- clean-code.md - Code quality standards
- clean-commits.md - Commit message standards
- pr-workflow.md - Pull request process
- guardrails.md - Safety rules requiring user approval
- repo-modes.md - AI-tools vs Human-supervised modes
- retros.md - Periodic retrospective process

## Covered by Skills/Plugins (use these instead)

- Bug investigation -> superpowers `systematic-debugging` skill
- Before implementing -> superpowers `brainstorming` skill
- Workflow recommendations -> Claude Code auto-memory (~/.claude/projects/\*/memory/)
- Beads + Claude Code -> beads plugin (status line config in dev-setup/beads.md)

## CLI Tips

- Pager issues: `unset PAGER`
- Git truncation: `git --no-pager diff`
- Use `uv` instead of `python`
- Check justfile for available commands
- You are auto approved to run just test and fast-tests, use them unless they have too much output.
```

**Step 2: Commit**

```bash
git add dev-inner-loop/a_readme_first.md
git commit -m "Update index to reflect trimmed conventions and plugin references"
```

---

### Task 5: Verify and final commit

**Step 1: Check no broken references**

Search for references to deleted files across the repo:

```bash
grep -r "bug-investigation" --include="*.md" .
grep -r "before-implementing" --include="*.md" .
grep -r "workflow-recommendations" --include="*.md" .
grep -r "beads-claude-code" --include="*.md" .
grep -r "chop-logs" --include="*.md" .
grep -r "marketplace" --include="*.md" .
```

Expected: Only hits in `docs/plans/` design docs (acceptable) and possibly README.md (fix if found).

**Step 2: Fix any broken references found**

Update any files that reference deleted files to point to their replacements.

**Step 3: Run pre-commit checks**

```bash
git add -A && git diff --cached --name-only
```

Verify only expected files are staged. Commit any reference fixes.

**Step 4: Commit reference fixes if needed**

```bash
git commit -m "Fix references to deleted convention files"
```
