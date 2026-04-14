# Sharing `~/.claude/CLAUDE.md` Content Across Machines

**Date:** 2026-04-14
**Status:** Draft for review

## Problem

`~/.claude/CLAUDE.md` holds cross-project rules Igor wants present on every
machine running Claude Code, but the file currently lives only on this dev VM.
A single flat file also cannot express the three real tiers of scope:

- **Universal** — applies on every machine (e.g. "never run `git push`")
- **Host-class-specific** — applies only on machines of a given class, of
  which there is currently one axis with a boolean answer: "is this machine
  served to Igor over Tailscale?" Machines for which the answer is yes need
  rules about how to share URLs; machines for which the answer is no do not.
- **Machine-type-specific** — applies only on machines of a given type
  (e.g. macOS laptop vs. OrbStack Ubuntu dev VM) where the rules encode
  facts about paths, hostnames, architecture, or toolchain defaults.

Additionally, a fourth tier already exists implicitly and should be
preserved: **this-machine-only overrides** that never belong in a checked-in
file.

Putting these into `chop-conventions` unlocks git-tracked history, PR review,
and propagation to every machine via the existing skill-symlink workflow.

## Goals

1. Move the universal and category-specific portions of `~/.claude/CLAUDE.md`
   into `chop-conventions` so they are version-controlled and shared.
2. Support three independent composition layers (universal, host-class,
   machine-type) plus a local override file, without losing the ability to
   add per-machine-only rules.
3. Make machine-type detection a pure-Python computation with no shelling
   out, folded into the existing `/up-to-date` diagnose script rather than a
   new standalone tool.
4. Extend `/up-to-date` so that running it on any machine (a) sets up or
   repairs the CLAUDE.md symlinks as needed, and (b) runs a per-repo
   `post-up-to-date.md` hook if one exists.
5. Stay consistent with the existing skills-sharing model: per-file symlinks
   under a directory under `~/.claude/`, set up by `/up-to-date`, never
   auto-installed.

## Non-goals

- Automatic propagation without user confirmation — `/up-to-date` offers,
  user approves.
- Supporting machine-type files beyond the two Igor currently uses
  (`mac`, `orbstack-dev`). `unknown` is a real return value that surfaces
  a clear error.
- Cross-machine sync of `this-machine-only` overrides — those stay local to
  each machine by design.
- Replacing the existing skills-symlinking flow — the CLAUDE.md flow mirrors
  it but stays independent.

## Architecture

### Directory layout (source of truth in `chop-conventions`)

```
chop-conventions/claude-md/
  README.md              # explains the split + install commands + where to
                         # add new content
  global.md              # universal rules — applies on every machine
  dev-machine.md         # rules for machines served over Tailscale
  machines/
    mac.md               # rules for macOS laptops
    orbstack-dev.md      # rules for OrbStack Ubuntu dev VMs (/home/developer,
                         # c-500X hostnames, aarch64)
```

`global.md` and the files under `machines/` are always expected to exist.
`dev-machine.md` is also always present in the repo; what varies is whether a
given machine symlinks to it.

### Installed state on any machine

```
~/.claude/claude-md/global.md       → chop-conventions/claude-md/global.md
~/.claude/claude-md/machine.md      → chop-conventions/claude-md/machines/<detected>.md
~/.claude/claude-md/dev-machine.md  → chop-conventions/claude-md/dev-machine.md   (only when dev_machine=true)
```

The first two are always installed once the user opts in. The third is
conditional on the detected `dev_machine` flag.

### Loaded `~/.claude/CLAUDE.md`

On each machine the real file becomes a thin composer:

```markdown
# Global CLAUDE.md

@~/.claude/claude-md/global.md
@~/.claude/claude-md/machine.md
@~/.claude/claude-md/dev-machine.md

## This-machine overrides
- <rules that apply only to this specific host>
```

An `@-import` line whose target does not exist is silently ignored by
Claude Code, which means the `dev-machine.md` line is safe to include
unconditionally in a template — though the design will only include it on
machines where detection actually set up the symlink (confirmed by
`step 0` — see Open Questions).

### Why three symlinks and not a single composite?

A single generated composite file would need to be regenerated every time
any source file changes. Three symlinks delegate that job to the filesystem:
editing a source file in `chop-conventions` is immediately visible to every
machine that has the corresponding symlink, with no regeneration step. This
matches the existing skills-sharing pattern exactly.

## Content categorization

### `global.md` — universal

- **Never run `git push`** — Igor pushes.
- **Never run destructive commands without confirmation** — `rm -rf`,
  `git reset --hard`, `DROP TABLE`, force push, etc.
- **Nice any ML/embedding work** with the full
  `nice -n 19 ionice -c 3 env OMP_NUM_THREADS=2 ...` prefix.
- **Don't use `claude-agent-sdk` for batch/pipeline extraction** — switch to
  `anthropic.AsyncAnthropic` or the batches endpoint.
- **Agent tool background dispatches cannot be aborted** — plus the
  shared-repo corollary.
- **`isolation: "worktree"` shares `.git/`** — namespace outputs per agent.
- **Debug third-party library oddities via the `docs` skill first**.
- **Non-interactive `git rebase -i` via scripted editors** — the
  `GIT_SEQUENCE_EDITOR=cp ...` recipe.
- **Session token / context usage** — read `~/.claude/statusline_last_input.json`.
- **`/reload-plugins` does NOT restart running MCP server processes** —
  `pkill -f '<server>'` first.
- **`ls → eza, du → dua, ps → procs`** (moved here from dev-machine —
  aliases are installed on every machine Igor uses, not dev-only).
- **Side-edit / side-run via `rmux_helper`** (moved here from dev-machine —
  `rmux_helper` is installed on every machine Igor uses).

### `dev-machine.md` — machines served over Tailscale

- **Sharing dev-server URLs with Igor:** use `http://$(hostname):<port>` (or
  the `.squeaker-teeth.ts.net` form), **never `localhost`**. Igor is on a
  different device reaching in over Tailnet; `localhost` URLs are
  unreachable from his browser.
- **Bind dev servers to `--host 0.0.0.0`**, not `127.0.0.1`, so Tailscale
  clients can reach them. Applies to Jekyll, Vite, `python -m http.server`,
  any local HTTP tool Igor might want to visit.
- **Source reference:** `idvorkin.github.io/CLAUDE-CODING.md:70` and
  `idvorkin.github.io/.claude/commands/serve.md:22`.

### `machines/orbstack-dev.md` — OrbStack Ubuntu dev VMs

- `$HOME == /home/developer`, user is `developer`.
- Architecture is aarch64; anything that downloads prebuilt binaries must
  match.
- Hostname pattern `C-500X`; Tailnet name `c-500X.squeaker-teeth.ts.net`.
- OS is Ubuntu (currently 25.10); systemd is available.

This file starts small and grows as OrbStack-only gotchas are discovered.

### `machines/mac.md` — macOS laptops

- Homebrew paths under `/opt/homebrew/` on Apple Silicon.
- Default shell is `zsh`; `/bin/bash` is 3.2.

Starts near-empty. Ready to fill as macOS-only gotchas are discovered.

### What stays in `~/.claude/CLAUDE.md` itself

Only rules genuinely specific to a single host. Currently, for the dev VM,
this is empty — every rule in the current file moves into one of the
categorized files above. On the Mac, `this-machine overrides` is where
laptop-only oddities would land that are too Igor-specific to generalize
into `machines/mac.md`.

## Detection logic (inside `diagnose.py`)

A new `detect_machine()` function returns a small dataclass:

```python
@dataclass
class MachineInfo:
    machine: str          # "mac" | "orbstack-dev" | "unknown"
    dev_machine: bool     # True iff served over Tailscale
    reasons: list[str]    # human-readable evidence used for detection
```

### `machine` classification

1. `platform.system() == "Darwin"` →
   - Cross-check `platform.mac_ver()[0]` is non-empty (this reads
     `/System/Library/CoreServices/SystemVersion.plist`, the same source
     `sw_vers` uses). Non-empty confirms macOS.
   - Return `"mac"`.
2. `platform.system() == "Linux"` AND `pathlib.Path("/home/developer").is_dir()` →
   return `"orbstack-dev"`.
3. Otherwise → return `"unknown"` and append a reason.

No subprocess calls. `platform` module wraps `uname(2)` for `system()` and
parses `SystemVersion.plist` for `mac_ver()`.

### `dev_machine` classification

A machine is a "dev machine" iff it is served to Igor over Tailscale.
Signal: Tailscale is installed AND the hostname matches Igor's dev-VM
pattern.

1. `shutil.which("tailscale") is not None` (present in PATH — works on both
   Mac-with-Homebrew and Linux), OR
   `pathlib.Path("/usr/bin/tailscale").exists()` OR
   `pathlib.Path("/opt/homebrew/bin/tailscale").exists()`.
2. AND `socket.gethostname().lower()` matches `^c-\d+$` (case-insensitive).

Both conditions must hold. A Mac with Tailscale installed but a human
hostname (`igor-mbp`) stays `dev_machine=false` — Igor is at the Mac, not
reaching it remotely. An OrbStack VM without Tailscale would also stay
false, though none of Igor's VMs currently lack it.

### Pure-function tests

Each classification step is a pure function that takes inputs and returns a
value, so the existing `test_diagnose.py` can cover it without mocking OS
calls:

- `classify_machine(system: str, is_home_developer: bool, mac_ver: str) -> str`
- `classify_dev_machine(tailscale_present: bool, hostname: str) -> bool`

The I/O-touching `detect_machine()` wraps these and is tested via one
integration test that runs on the current machine and asserts `machine ==
"orbstack-dev"`, `dev_machine == True`.

## `diagnose.py` JSON additions

Two new top-level fields are added to the output. The existing consumer
(`SKILL.md`) reads the JSON and acts on them.

```json
{
  "remotes": {...},
  "branch": {...},
  "worktree": {...},
  "pr": {...},
  "shared_claude_md": {
    "machine_info": {
      "machine": "orbstack-dev",
      "dev_machine": true,
      "reasons": ["Linux + /home/developer present", "tailscale in PATH", "hostname=c-5004 matches ^c-\\d+$"]
    },
    "expected_symlinks": {
      "global": {"path": "/home/developer/.claude/claude-md/global.md",
                 "target": "/home/developer/gits/chop-conventions/claude-md/global.md",
                 "should_install": true},
      "machine": {"path": "/home/developer/.claude/claude-md/machine.md",
                  "target": "/home/developer/gits/chop-conventions/claude-md/machines/orbstack-dev.md",
                  "should_install": true},
      "dev_machine": {"path": "/home/developer/.claude/claude-md/dev-machine.md",
                      "target": "/home/developer/gits/chop-conventions/claude-md/dev-machine.md",
                      "should_install": true}
    },
    "actual": {
      "global":      {"exists": false, "is_symlink": false, "resolves_to": null, "drift": true},
      "machine":     {"exists": false, "is_symlink": false, "resolves_to": null, "drift": true},
      "dev_machine": {"exists": false, "is_symlink": false, "resolves_to": null, "drift": true}
    },
    "actions": [
      {"kind": "create_symlink", "path": ".../global.md",      "target": ".../global.md"},
      {"kind": "create_symlink", "path": ".../machine.md",     "target": ".../machines/orbstack-dev.md"},
      {"kind": "create_symlink", "path": ".../dev-machine.md", "target": ".../dev-machine.md"}
    ]
  },
  "post_up_to_date_path": "/home/developer/gits/chop-conventions/.claude/post-up-to-date.md",
  "errors": []
}
```

### Drift semantics

For each of the three symlink slots:

- `exists=false` → `drift=true` if `should_install=true`, else `drift=false`.
- `exists=true, is_symlink=false` → `drift=true` (user replaced with real
  file, keep hands off and report).
- `is_symlink=true, resolves_to != target` → `drift=true` (stale or wrong
  machine-type, should be repaired).
- `is_symlink=true, resolves_to == target` → `drift=false` (correct).

Drift of type "user replaced with real file" is surfaced but `actions` does
NOT contain a `delete_file` or `replace_file` action — that would risk
deleting hand-written content. The skill reports the drift and leaves it to
Igor.

Drift of type "exists but `should_install=false`" (e.g. machine went from
dev to non-dev) also gets reported, not auto-removed.

### Resolution of `should_install`

For the `dev_machine` slot, `should_install` comes directly from
`machine_info.dev_machine`.

For the `global` and `machine` slots, `should_install=true` unconditionally
once the user has opted in on this machine. Opt-in state is detected by
"either slot currently exists as a symlink, OR the user just approved
setup" — the skill handles the prompt flow.

## `post-up-to-date.md` per-repo hook

### Path & presence

`<repo-root>/.claude/post-up-to-date.md`. `diagnose.py` reports
`post_up_to_date_path` as the absolute path when present, `null` otherwise.

### Contract

- Content is markdown instructions, read and followed by the Claude session
  running `/up-to-date`. Not a script — interpretation happens in the LLM.
- Fires on every `/up-to-date` run where the file exists, regardless of
  whether the sync pulled any commits. The markdown is responsible for its
  own idempotency ("if X already done, skip; otherwise do Y").
- Runs after all sync operations complete: `pull`, `push` for fork mirror,
  absorbed-branch cleanup, worktree prune, CLAUDE.md symlink setup. Runs
  before the "`/clear` context?" post-sync prompt.

### Why fire unconditionally

A "fire only on delta" contract sounds cheaper but couples the hook to
`/up-to-date`'s internal state machine and creates a fragile boundary: what
counts as a delta? Pulling a commit? Creating a symlink? Deleting a
merged branch? Unconditional firing plus an idempotency requirement in the
markdown is simpler and makes the hook's behavior inspectable from the
markdown alone.

## `/up-to-date` skill changes

### `diagnose.py` changes

1. Add `detect_machine()` and the two `classify_*` pure functions.
2. Add `check_shared_claude_md(repo_root, home)` that computes the
   `shared_claude_md` block by stat-ing each expected symlink path.
3. Add `check_post_up_to_date(cwd)` that looks for `.claude/post-up-to-date.md`
   in the current working directory.
4. Wire both into the top-level JSON output.

### `SKILL.md` changes

Insert a new step between **Act** and **Post-Sync**:

> **Step 3.5 — Shared CLAUDE.md setup / resync.** Consult
> `shared_claude_md`. If any slot has `drift=true` and a
> non-destructive action exists (`create_symlink` or
> `replace_stale_symlink`), report the drift and the proposed fix to the
> user and wait for approval. For each approved action, run `ln -sfn
> <target> <path>` (the `-n` prevents following an existing symlink pointing
> at a directory). Slots with drift that would require deleting user
> content (`exists=true, is_symlink=false`) are reported but never
> auto-fixed. Stay silent when every slot has `drift=false`.

Also add a new trailing step after Post-Sync:

> **Step 5 — Post-hook.** If `post_up_to_date_path` is non-null, read the
> file and follow its instructions. The content is markdown; the hook is
> expected to be idempotent.

### One-time setup flow

First run on a new machine, `shared_claude_md.actual` shows all three slots
missing. The skill:

1. Reports: "Shared CLAUDE.md symlinks not set up. Detected machine:
   `<machine>`, dev_machine: `<bool>`. Proposed symlinks: [list]."
2. Asks: "Set these up?"
3. On approval, creates each symlink. Also prints the template
   `@`-import lines Igor must add to `~/.claude/CLAUDE.md` manually — the
   skill does not edit the real local file automatically, since that file
   may contain machine-local overrides the skill should not touch.

### Re-setup flow (detection changed / slot renamed)

Re-running `/up-to-date` after detection logic changes (e.g. hostname was
`c-5004`, now `c-5004a`) causes the existing `machine.md` symlink to resolve
to a different path than expected. Drift is reported; the skill offers to
repair via `ln -sfn`. `-sfn` is safe here because the slot was already a
symlink (not a real file).

### `.git/info/exclude` additions

The new worktree and the eventual merged state do not add anything that
needs ignoring. No changes to `.gitignore` or `.git/info/exclude`.

## Migration: populating `chop-conventions/claude-md/` from the current file

One-time manual step, performed as part of the PR:

1. Copy each bullet from the current `~/.claude/CLAUDE.md` into the file its
   categorization dictates.
2. Keep the current file's original form in the repo as a single atomic
   commit (`chore: snapshot current ~/.claude/CLAUDE.md before split`) so
   the migration is auditable.
3. Replace `~/.claude/CLAUDE.md` with the thin composer template — this
   edit happens outside the repo (on each machine), not as part of the PR.

## Testing plan

- Unit tests in `skills/up-to-date/test_diagnose.py`:
  - `classify_machine` covers Darwin, Linux+`/home/developer`,
    Linux-without, unknown system.
  - `classify_dev_machine` covers all four combinations of tailscale-present
    and hostname-matches.
  - `check_shared_claude_md` covers: no symlinks, correct symlinks,
    stale symlink target, symlink replaced by real file, partial
    installation (1 of 3 present).
  - `check_post_up_to_date` covers present / absent.
- Integration test: run `diagnose.py` in this worktree and assert the
  detection fields match the current machine (`orbstack-dev`, `true`).
- Manual verification:
  - Run `/up-to-date` from the worktree on this VM, walk through the
    one-time setup flow end to end.
  - `@`-import resolution test (see Step 0 in the plan) — write a dummy
    `~/.claude/claude-md/global.md` symlink pointing at a test file and
    confirm Claude Code picks up its content via `@` from the real
    `~/.claude/CLAUDE.md`. If it doesn't, fall back to direct-path
    `@-imports` as documented in Step 0.

## Risks and open questions

### Risk: `@`-imports may not follow symlinks

Claude Code's `@path.md` syntax in `CLAUDE.md` is the linchpin of the
design. If it does not follow symlinks, the design still works — swap each
`@~/.claude/claude-md/<name>.md` for `@~/gits/chop-conventions/claude-md/<target>.md`
— but the symlink-installed layer becomes a "setup is complete" marker
rather than the load path, and `diagnose.py`'s drift detection becomes
the primary reason the symlinks exist.

This risk is addressed by **Step 0** of the implementation plan: before
writing any of the files, create a throwaway test in `~/.claude/claude-md/`
and confirm that Claude Code resolves `@~/.claude/claude-md/test.md` to the
symlink's target. The plan does not start the real work until this is
verified.

### Risk: `/home/developer` is not unique to OrbStack

Any Docker container or other Ubuntu install with a `developer` user would
match. In Igor's environment this is fine because only OrbStack VMs do
that, but the check would misfire on a generic Ubuntu box. Mitigation: the
`reasons` array captures the evidence, so misdetection is inspectable; if it
becomes an issue, add a secondary check (`/etc/orbstack-guest`, kernel
command line, etc.). Not fixing it pre-emptively.

### Risk: detection on the Mac is untested until first run

The Mac branch of `classify_machine` is covered by the unit test
(which takes synthetic inputs) but cannot be integration-tested from this
VM. First real execution happens on Igor's Mac, at which point any edge
case (e.g. `platform.mac_ver()` returning `("", ("",""), "")` on some macOS
versions) is surfaced as a test failure rather than a silent fallback to
`"unknown"`.

### Risk: drift detection false positives

If Igor manually points `machine.md` at a different machine file (e.g.
because he's on the Mac but wants `orbstack-dev` rules), re-running
`/up-to-date` will flag this as drift and offer to "fix" it. Mitigation:
the offer is interactive, never automatic. Igor can decline. If this proves
annoying, the next iteration could add a `.claude-md-override` marker file.
Not building that now.

### Open question: Does `machines/mac.md` ship empty or omitted?

**Proposal:** ship as a near-empty file with a single header and a comment
explaining what belongs there. This means the Mac branch of detection can
always symlink a real file, and `machines/mac.md` is immediately
grep-able and editable when the first Mac-specific rule is discovered.
Alternative (omit until non-empty) makes first Mac run on
`/up-to-date` fail with a confusing "target does not exist" error.

### Open question: Fourth machine type in the future

Adding a new machine type requires: (a) a new file under `machines/`,
(b) a new branch in `classify_machine`, (c) a new unit test. All three are
local to `skills/up-to-date/diagnose.py` and `chop-conventions/claude-md/`,
which is tight enough that "no framework, just add the branch" is the
right default. Not building a registry.

## Order of implementation

See the accompanying implementation plan
(`docs/superpowers/plans/2026-04-14-claude-md-sharing-plan.md`, to be
written after this spec is approved). The plan starts with Step 0: verify
`@`-import symlink resolution.

## Success criteria

1. `detect.py` — no, `diagnose.py` — reports
   `machine=orbstack-dev, dev_machine=true` on this VM without shelling
   out.
2. Running `/up-to-date` on this VM after the PR lands sets up all three
   symlinks and prints the `@`-import lines to add to `~/.claude/CLAUDE.md`.
3. Running `/up-to-date` on Igor's Mac after the PR lands detects
   `machine=mac, dev_machine=false` and sets up `global.md` and `machine.md`
   symlinks but not `dev-machine.md`.
4. Creating `.claude/post-up-to-date.md` in any repo and running
   `/up-to-date` from that repo triggers the markdown's instructions.
5. Editing `chop-conventions/claude-md/global.md` on the primary checkout
   propagates to every machine on its next Claude Code session without any
   additional action.
