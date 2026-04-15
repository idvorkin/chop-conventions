---
name: yolo
description: YOLO mode knowledge — what `--dangerously-skip-permissions` does and doesn't bypass, plus helpers to set up `additionalDirectories` correctly. Use when the user asks "why is Claude still prompting me in bypass mode," "set up YOLO," "skip all permissions," or reports unexpected permission prompts despite using `--dangerously-skip-permissions`.
allowed-tools: Bash, Read, Edit, Grep, Glob
---

# YOLO Mode — What It Bypasses, What It Doesn't

## TL;DR

`--dangerously-skip-permissions` (a.k.a. `bypassPermissions`) does NOT bypass everything. Three categories always prompt:

1. **Tools requiring user interaction** — `ExitPlanMode`, `AskUserQuestion` (by design)
2. **`ask` rules in `settings.json`** — user-configured `permissions.ask` entries override bypass
3. **Hardcoded safety-check guardrails** (non-negotiable security):
   - Sensitive files (`.env`, credentials, `.git/`)
   - Writes outside allowed working directories
   - Shell metachars in file paths (`$VAR`, `$(...)`, `%VAR%`, `>(...)`)
   - `/dev/tcp`, `/dev/udp`
   - `Remove-Item -Recurse` near `.git` / `.claude`
   - PowerShell

## How the resolver actually decides

Permission resolution (from 2.1.109 binary analysis) runs checks in this order, and returns on first match:

1. **`deny` rules** → block
2. **`requiresUserInteraction()` on the tool** → ask (category 1)
3. **`ask` rules in settings.json** → ask (category 2)
4. **`safetyCheck` hardcoded guardrails** → ask (category 3)
5. **`bypassPermissions`** → allow ← YOLO short-circuits HERE, not earlier
6. **`allow` rules** → allow
7. **Default** → ask

Bypass only wins after categories 1–3 have already had their say. That's the whole story.

## Most common complaint

> "I'm in YOLO mode and it keeps prompting me."

**Almost always:** a file write outside the current working directory. Blog edits from an igor2 session, dotfile edits, `~/.claude/*` changes, cross-repo work without `--add-dir`.

**Fix** — add all cross-repo dirs to `settings.json`:

```json
{
  "permissions": {
    "additionalDirectories": [
      "/home/developer/gits/larry-blog",
      "/home/developer/gits/chop-conventions",
      "/home/developer/.claude"
    ]
  }
}
```

Or per-launch without touching settings:

```bash
claude --dangerously-skip-permissions \
  --add-dir ~/gits/larry-blog \
  --add-dir ~/gits/chop-conventions \
  --add-dir ~/.claude
```

`additionalDirectories` merges from settings.json + `--add-dir` — set the stable ones in settings, use `--add-dir` for ad-hoc work.

## Diagnosing a prompt

When a prompt DOES fire in YOLO, the exact message text pins which guardrail hit:

| Message contains | Category | Recovery |
|---|---|---|
| "requires manual approval" | 1 — tool self-declared | Can't bypass. `ExitPlanMode` / `AskUserQuestion` always ask by design. |
| Matching `ask` rule in settings | 2 — user-configured ask | Remove the `permissions.ask` entry from `settings.json` if it's stale. |
| "is a sensitive file" | 3 — sensitive-path list | Not bypassable. Move credentials out of the write target, or do the edit manually. |
| "outside the allowed working directories" | 3 — cwd guard | Add the dir via `additionalDirectories` / `--add-dir`. |
| "contains shell expansion" / "contains unsafe characters" | 3 — static-validation guard | Rewrite the command to quote the path or avoid metachars. No setting bypasses this. |
| `/dev/tcp` / `/dev/udp` | 3 — network redirect guard | Not bypassable. Use a real networking tool (`curl`, `nc`). |
| PowerShell | 3 — deny-by-default | Not bypassable on Linux/macOS. Use `bash`. |

## What YOLO is NOT

There is **no** super-YOLO flag that bypasses category 3. Don't go looking for one. Running Claude as root with `IS_SANDBOX=1` exits immediately — that path is closed.

If a category-3 prompt is genuinely blocking you, the right moves are:
- Narrow the operation so it doesn't trip the guard (e.g. write to an allowed dir then `mv`).
- Do the one-off step by hand in the shell.
- File an issue upstream if the guard is a false positive on a legitimate pattern.

## Source

Research in [chop-conventions issue #122](https://github.com/idvorkin/chop-conventions/issues/122) · igor2 bead `bgt.14`.
