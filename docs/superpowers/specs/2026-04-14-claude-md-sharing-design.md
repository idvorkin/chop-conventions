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

No `README.md` under `claude-md/`. The directory's shape and categorization
rules live in this spec and in the skill prose; a sibling README would
describe the split in one more place that can rot out of sync with the
skill and the actual files. Authors adding content edit the files
directly.

**Pre-commit formatters touch these files.** `chop-conventions` runs
prettier on every markdown file at commit time, which will reflow bullet
lines and may rewrite headings. This is fine — the files are authored
freely and normalized on commit — but authors of these files should not
fight the formatter. If a commit fails with "files were modified by this
hook," re-stage and re-commit.

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
Claude Code (confirmed by `Step 0` — see Open Questions). The template
**always** includes the `@~/.claude/claude-md/dev-machine.md` line
unconditionally. On non-dev machines the symlink is absent and the line
no-ops; on dev machines the symlink resolves and the rules load. This
keeps the template byte-identical across all machines, so `diff` between
two machines' `~/.claude/CLAUDE.md` files only shows legitimate
this-machine overrides.

### Why three symlinks and not a single composite?

A single generated composite file would need to be regenerated every time
any source file changes. Three symlinks delegate that job to the filesystem:
editing a source file in `chop-conventions` is immediately visible to every
machine that has the corresponding symlink, with no regeneration step. This
matches the existing skills-sharing pattern exactly.

## Content categorization

### `global.md` — universal

Rules that apply regardless of OS, hostname, or Tailscale status. Must be
true on a fresh macOS laptop with nothing installed and on a production
OrbStack VM alike.

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

Explicitly **not** in global:

- **`ls → eza, du → dua, ps → procs`** — the source bullet opens with "on
  this machine", meaning the dev VM's shell aliases. A fresh Mac with no
  custom rc files has plain coreutils; routing this to `global.md` would
  make Claude avoid `ls -t` on machines where `ls -t` is correct. Lives in
  `orbstack-dev.md` (and later in `mac.md` if/when Igor sets up the same
  aliases there).
- **Side-edit / side-run via `rmux_helper`** — depends on `rmux_helper`
  being installed and on a running tmux session (see
  `~/settings/rust/tmux_helper`). Lives in `orbstack-dev.md` until
  `rmux_helper` is verifiably present on the Mac with a working pane
  identity.

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
- **`ls → eza, du → dua, ps → procs`** — flags differ from coreutils; use
  `\ls`/`\du`/`\ps` to bypass the alias, or prefer Glob/Read tools.
- **Side-edit / side-run via `rmux_helper`** — full subsection (see current
  `~/.claude/CLAUDE.md` for the bullets).

This file starts with the tools-installed-here gotchas above and grows as
more OrbStack-only quirks are discovered.

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

- `classify_machine(system: str, mac_ver_nonempty: bool, home_developer_exists: bool) -> str`
- `classify_dev_machine(tailscale_present: bool, hostname: str) -> bool`

The classifier takes already-evaluated booleans, not paths or raw platform
tuples, so the test suite never has to mock `pathlib` or `platform`. The
thin I/O wrapper `detect_machine()` builds the three booleans (one
`platform.system()` call, one `platform.mac_ver()` call, one
`Path('/home/developer').is_dir()` call, plus the hostname and tailscale
probes) and hands them to the classifiers. `detect_machine()` itself is
covered by a single integration test that runs on the current machine and
asserts `machine == "orbstack-dev"`, `dev_machine == True`.

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
      {"kind": "create_symlink", "slot": "global",      "path": ".../global.md",      "target": ".../global.md"},
      {"kind": "create_symlink", "slot": "machine",     "path": ".../machine.md",     "target": ".../machines/orbstack-dev.md"},
      {"kind": "create_symlink", "slot": "dev_machine", "path": ".../dev-machine.md", "target": ".../dev-machine.md"}
    ]
  },
  "post_up_to_date_path": "/home/developer/gits/chop-conventions/.claude/post-up-to-date.md",
  "errors": []
}
```

### Action kinds

The example above shows a fresh-install state where all three slots
need `create_symlink`. In general each slot emits **at most one** action
per run, determined by the slot's current filesystem state crossed with
`should_install`:

| Kind                      | When emitted                                                                | Skill behavior                                                                            |
| ------------------------- | --------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| `create_symlink`          | slot missing, `should_install=true`                                         | `ln -s <target> <path>` — plain, not `-f`, so a race-condition real file is not clobbered |
| `replace_stale_symlink`   | slot is a symlink but points to the wrong target, `should_install=true`     | `ln -sfn <target> <path>` — safe because the existing entry is already a symlink          |
| `remove_obsolete_symlink` | slot is a symlink but `should_install=false` (machine went from dev to non-dev) | Report only; require user confirmation before `rm <path>`                                 |
| `report_user_file`        | slot is a real file (not a symlink), regardless of `should_install`         | Report only; the skill never deletes or overwrites user content                           |

A slot whose `actual` state is already correct (symlink exists and
resolves to the expected target, or missing with `should_install=false`)
emits no action at all. The `actions` array is therefore at most
three entries long and each `slot` value appears at most once.

Only `create_symlink` and `replace_stale_symlink` are auto-executed after
user approval at the prompt. `remove_obsolete_symlink` and
`report_user_file` are always surfaced as read-only diagnostics.

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

`should_install` is a **pure computation** from inputs visible to
`diagnose.py` — no runtime "user just approved" flag, since the diagnose
script runs once per `/up-to-date` invocation and produces a snapshot.

- `global` and `machine` slots: `should_install=true` **iff** the opt-in
  marker file `~/.claude/claude-md/.enabled` exists. The marker is a zero-
  byte sentinel created by the skill on first-run approval; `diagnose.py`
  only stats it. Absence means "user has never opted in on this machine"
  and every slot reports `drift=false` so the skill stays silent.
- `dev_machine` slot: `should_install = enabled AND machine_info.dev_machine`.

The skill, not the diagnose script, is responsible for creating the marker
file after the user approves first-time setup. This keeps `diagnose.py`
pure and avoids the "opt-in detected by presence of the thing we're about
to create" circular dependency.

### `.enabled` marker semantics

The marker lives at `~/.claude/claude-md/.enabled` on each machine. Three
properties follow from that location:

1. **Per-machine opt-in.** `~/.claude/` is not (and must never be)
   sync'd across machines. Enabling on the dev VM does not enable on the
   Mac; each machine prompts on its first `/up-to-date` run after the
   feature lands. This is the same model as the existing skill symlinks —
   `~/.claude/skills/<name>` is per-machine.
2. **`~/.claude/claude-md/` is a real directory, not a symlink.** The
   skill creates it with `mkdir -p` immediately before writing the
   marker. The three slot paths inside it (`global.md`, `machine.md`,
   `dev-machine.md`) are individual symlinks into the chop-conventions
   checkout; the directory itself is local filesystem state. Because
   `mkdir -p` silently follows a pre-existing symlink at that path, the
   skill MUST first check `Path("~/.claude/claude-md").expanduser().is_symlink()`
   and abort the enable flow with a clear error if it is a symlink
   — otherwise a compromised or well-meaning-but-wrong dotfiles setup
   could redirect `.enabled`, `hooks-trusted.json`, and every slot
   symlink into an attacker-controlled location without tripping any
   later check. Same check applies on every `/up-to-date` run before
   writing any file under that directory, not just on first enable.
3. **Marker without symlinks is a valid repair state, not an error.**
   If `.enabled` exists but one or more slot symlinks have been hand-
   deleted, `diagnose.py` still reports `should_install=true` for the
   missing slots → `drift=true` → `create_symlink` actions. Re-running
   `/up-to-date` and approving the actions restores the slots without
   re-prompting the opt-in question. The skill never implicitly deletes
   `.enabled`; removal is a manual step Igor performs to revoke opt-in
   (documented in the skill prose, not automated).

No cross-machine propagation path exists for the marker by design. If
Igor wants shared CLAUDE.md on all three machines, he runs `/up-to-date`
and approves on each.

## `post-up-to-date.md` per-repo hook

### Path & presence

`<repo-root>/.claude/post-up-to-date.md`, where `<repo-root>` is resolved
via `git rev-parse --show-toplevel` — **not** the current working
directory. Worktrees have their own toplevel, so a hook placed in the
primary checkout does not fire from a linked worktree unless also present
there. `diagnose.py` reports `post_up_to_date_path` as the absolute path
when present, `null` otherwise.

### Contract

- Content is markdown instructions, read and followed by the Claude session
  running `/up-to-date`. Not a script — interpretation happens in the LLM.
- Fires on every `/up-to-date` run where the file exists, regardless of
  whether the sync pulled any commits. The markdown is responsible for its
  own idempotency ("if X already done, skip; otherwise do Y").
- Runs after all sync operations complete: `pull`, `push` for fork mirror,
  absorbed-branch cleanup, worktree prune, CLAUDE.md symlink setup. Runs
  before the "`/clear` context?" post-sync prompt.

### Security: prompt-injection surface

`post-up-to-date.md` is executed as an LLM prompt every `/up-to-date` run
inside any repo that ships one. A malicious or compromised commit on a
repo Igor collaborates on could introduce arbitrary instructions (leak
secrets, exfiltrate tokens, push to fork, etc.) that fire the next time
`/up-to-date` runs in that repo. Mitigations baked into the skill:

1. **First-sight prompt.** The first time `diagnose.py` reports a
   `post_up_to_date_path` whose content hash is not in a local allowlist,
   the skill displays the full markdown to Igor and asks "trust this hook?"
   before executing.
2. **Re-prompt on content change.** If the hash changes from the
   allowlisted value, re-prompt before executing. A new commit touching the
   file forces re-approval.
3. **Never auto-approve.** The allowlist cannot be populated except via
   the interactive prompt — no flag, no env var, no config file edit path.
4. **Reject symlinks as hooks.** If
   `Path(".claude/post-up-to-date.md").is_symlink()` returns true for
   the repo's hook path, `diagnose.py` emits
   `{subsystem: "post_up_to_date", code: "hook_is_symlink", message:
   "...", path: "..."}` into `errors[]` and reports
   `post_up_to_date_path: null`. Following the symlink would let a
   tracked-in-git one-liner point at an arbitrary file on the local
   filesystem (another repo's hook, `~/.bashrc`, a shared cache
   directory) that the hash-allowlist cannot meaningfully cover —
   the contents change independently of the repo's commit history, and
   Igor's first-sight approval was for a file he saw resolved at one
   specific moment. Symlinks in `.claude/post-up-to-date.md` are
   refused outright; if Igor genuinely wants to share hook content,
   the file can `@`-import another file via the markdown's own
   import syntax, which makes the indirection visible to the
   first-sight prompt.
5. **Read once, hash once, execute once.** The skill reads the hook
   file into memory a single time, computes the hash over the
   in-memory bytes, compares against `hooks-trusted.json`, and — on
   trust match — feeds those same in-memory bytes to the LLM. It
   does NOT re-open the file between the hash check and execution,
   which closes a TOCTOU window where a concurrent process could
   swap the file after approval.

This is the same threat model as any tracked file Claude reads, but the
hook fires automatically on sync, so it deserves explicit handling rather
than silently trusting filesystem content.

#### `hooks-trusted.json` schema and lifecycle

**Location.** `~/.claude/claude-md/hooks-trusted.json`. Per-machine
(same rationale as `.enabled` — `~/.claude/` is never sync'd across
machines). Sharing approvals cross-machine would defeat the first-sight
prompt on the machine an attacker actually compromises.

**Schema.** A single flat JSON object, version tagged so we can migrate
later without guessing:

```json
{
  "version": 1,
  "entries": {
    "/home/developer/gits/chop-conventions": {
      "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      "approved_at": "2026-04-14T19:02:11Z",
      "hook_path": ".claude/post-up-to-date.md"
    }
  }
}
```

- Key is the absolute path returned by `git rev-parse --show-toplevel`
  for the repo. Worktree toplevels are distinct from the primary
  checkout, so each worktree gets its own entry — on purpose, because
  a worktree can carry a different branch with different hook content.
- `sha256` is over the raw bytes of `hook_path` resolved from the
  toplevel. No normalization (whitespace, line endings) — bytes in,
  bytes out.
- `approved_at` is ISO 8601 UTC, recorded for audit only; never read
  back as a trust input.
- `hook_path` is stored for future-proofing (if the hook path ever
  becomes configurable) but is currently always `.claude/post-up-to-date.md`.

**Creation.** The file is created lazily by the skill the first time a
hook is approved. Missing file is equivalent to `{"version": 1,
"entries": {}}` — the skill does not create an empty file on `/up-to-date`
runs where no hook is present. Parent directory (`~/.claude/claude-md/`)
is created by the `.enabled` setup flow; if the user approves a hook
before ever enabling shared CLAUDE.md, the skill runs the same
`mkdir -p` first — preceded by the same `is_symlink()` abort check
the enable flow uses (see `.enabled` marker semantics, property 2).

**Add / remove.**

- **Add:** interactive prompt only. Skill writes the file atomically
  (write to `hooks-trusted.json.tmp`, `os.replace` to final name) so a
  crash mid-write cannot leave a corrupt JSON that locks Igor out of
  subsequent approvals.
- **Remove:** manual. Igor edits the file or deletes it wholesale. The
  skill does not prune entries for deleted repos automatically — an
  orphan entry is harmless (the `post_up_to_date_path` simply never
  resolves to it again) and auto-pruning would require a filesystem
  crawl that is out of scope.

**Corrupt file handling.** If the file exists but cannot be parsed as
JSON or does not conform to the schema (missing `version`, wrong type
for `entries`), the skill does NOT re-prompt for trust on this run and
does NOT overwrite the corrupt file. Step 5 (post-hook execution) is
skipped and an error entry
`{subsystem: "post_up_to_date", code: "hooks_trusted_corrupt",
message: "...", path: "~/.claude/claude-md/hooks-trusted.json"}` is
appended to `errors[]`. Igor inspects and repairs the file manually
(fix the JSON, or `rm` it to reset all trust), then re-runs
`/up-to-date`. The rationale for refusing to re-prompt is that a silent
re-prompt path is indistinguishable from "user approves → skill
overwrites corrupt file with a single valid entry → previously-trusted
entries lost without notice". Forcing a manual inspection step surfaces
the bug loudly instead of masking it.

**Rotation.** None. Entries live until Igor removes them or the repo
toplevel path changes (e.g. rename). Trust is cheap to re-establish via
the interactive prompt, so a TTL would be annoyance without security
benefit.

**Why a JSON file and not `.git/info/` per-repo.** A per-repo storage
location would require the skill to write inside every repo Igor has a
hook in, spraying state into repo metadata and coupling trust to git
internals. Centralizing in `~/.claude/claude-md/hooks-trusted.json`
keeps trust state colocated with the other per-machine CLAUDE.md state
and inspectable in one place.

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
2. Add a thin resolver `resolve_chop_root(env, home)` that returns
   either an absolute `Path` to a checkout whose
   `claude-md/global.md` exists, or `None`. It checks
   `env.get("CHOP_CONVENTIONS_ROOT")` first, then `~/gits/chop-conventions`,
   and requires the `claude-md/global.md` file to exist before accepting
   the candidate. Pure on `(env, home)` — no filesystem writes, one
   stat per candidate.
3. Add `check_shared_claude_md(chop_root, home, enabled, machine_info)`
   that computes the `shared_claude_md` block by stat-ing each expected
   symlink path. Takes `chop_root` as an already-resolved `Path` (never
   `None`) and returns a `(block, errors)` tuple where `block` is the
   dict to embed under `"shared_claude_md"` and `errors` is a list of
   error entries to append to the top-level `errors[]`. Pure; no
   subprocess calls.
4. The top-level `diagnose.py` orchestrator calls `resolve_chop_root`
   first. If it returns `None`, the orchestrator appends
   `{subsystem: "shared_claude_md", code: "chop_root_unresolved",
   message: "...", probed: [...]}` to `errors[]` and **omits the
   `shared_claude_md` key entirely from the output JSON**
   (NOT an empty block). `check_shared_claude_md` is never called with
   an unresolvable root, so its signature stays narrow and its unit
   tests never exercise a `None` branch. The "chop root unresolved"
   unit test in the testing plan targets `resolve_chop_root`, not
   `check_shared_claude_md`.
5. Add `check_post_up_to_date(repo_toplevel)` that looks for
   `.claude/post-up-to-date.md` **inside the git repo's toplevel**, where
   toplevel is already computed by the existing diagnose flow (or a new
   `git rev-parse --show-toplevel` call added for this purpose). It
   must NOT use `os.getcwd()` — running `/up-to-date` from a subdirectory
   of a repo would otherwise miss the hook.
6. Wire all of the above into the top-level JSON output.

### `SKILL.md` changes

Insert a new step between **Act** and **Post-Sync**:

> **Step 3.5 — Shared CLAUDE.md setup / resync.** Skip entirely when
> `errors[]` contains a `shared_claude_md.*` entry (see "Error contract"
> under Risks). Otherwise consult `shared_claude_md.actions`. Before
> running any `create_symlink` action, verify `~/.claude/claude-md/`
> is not a symlink (`Path.is_symlink()`) and then ensure it exists
> (`mkdir -p`) — the parent directory is created by the enable flow
> but the check is idempotent and costs nothing. If the symlink check
> fails, abort Step 3.5 with the same error message as the enable
> flow. For each `create_symlink` action, run `ln -s <target> <path>`
> — plain `-s`, not `-sfn`, so a race-condition real file at `<path>`
> fails loudly instead of being silently overwritten. For each
> `replace_stale_symlink` action, run `ln -sfn <target> <path>` (the
> `-n` prevents following an existing symlink pointing at a directory);
> the preceding action kind already confirmed the entry is a symlink,
> so `-f` is safe. `remove_obsolete_symlink` and `report_user_file`
> actions are surfaced to Igor as read-only findings, never
> auto-executed. Wait for user approval before running any
> `create_symlink` or `replace_stale_symlink`. Stay silent when
> `actions` is empty **and** no `shared_claude_md.*` error is present.

Also add a new trailing step after Post-Sync:

> **Step 5 — Post-hook.** If `post_up_to_date_path` is non-null, read the
> file and follow its instructions. The content is markdown; the hook is
> expected to be idempotent.

### One-time setup flow

First run on a new machine, the opt-in marker `~/.claude/claude-md/.enabled`
is absent, so `diagnose.py` reports `should_install=false` and zero
actions for every slot. The skill special-cases this: when the marker is
absent AND the chop-conventions checkout contains `claude-md/`, it:

1. Reports: "Shared CLAUDE.md content is available but not yet enabled on
   this machine. Detected machine: `<machine>`, dev_machine: `<bool>`.
   Proposed symlinks: [list derived from machine_info]."
2. Asks: "Enable shared CLAUDE.md on this machine?"
3. On approval, first checks `~/.claude/claude-md` with
   `Path.is_symlink()`. If it is a symlink, aborts with the error
   "`~/.claude/claude-md` is a symlink to `<target>`; refusing to
   enable shared CLAUDE.md until that is resolved. Remove or
   replace the symlink with a real directory and re-run
   `/up-to-date`." Otherwise runs `mkdir -p ~/.claude/claude-md`
   (creating the real per-machine directory if it does not yet
   exist), creates `~/.claude/claude-md/.enabled` as an empty file,
   then re-runs `diagnose.py` so the next JSON reports
   `should_install=true` and the usual `create_symlink` actions.
4. Executes the `create_symlink` actions from the fresh JSON. Prints the
   template `@`-import lines Igor must add to `~/.claude/CLAUDE.md`
   manually — the skill does not edit the real local file automatically,
   since that file may contain machine-local overrides the skill should
   not touch.

`diagnose.py` never creates `.enabled`; only the skill does, and only
after explicit approval. Re-running `/up-to-date` on a machine where the
user declined at step 2 leaves the marker absent and produces no noise on
subsequent runs.

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
  - `check_shared_claude_md` covers: `enabled=false` (zero actions),
    `enabled=true` with no symlinks (three `create_symlink` actions),
    correct symlinks (empty actions), stale `machine` symlink
    (`replace_stale_symlink`), symlink replaced by real file
    (`report_user_file`), `dev_machine` slot present after machine became
    non-dev (`remove_obsolete_symlink`), partial installation (1 of 3
    present).
  - `check_post_up_to_date` covers present / absent and repo-root
    resolution from a subdirectory.
  - `hooks-trusted.json` handling (unit-level, mock filesystem):
    missing file → treated as empty; valid entry matching current hash →
    trusted; valid entry, different hash → re-prompt; corrupt/unparseable
    JSON → Step 5 skipped, `post_up_to_date.hooks_trusted_corrupt` error
    emitted, no re-prompt, corrupt file NOT overwritten (matches the
    corrupt-file contract in `hooks-trusted.json` lifecycle). Atomic-write
    path (`.tmp` → `os.replace`) is exercised once on the add-entry happy
    path.
  - **Hook TOCTOU contract** (`post-up-to-date.md` "read once, hash once,
    execute once"): mock `open()` / `Path.read_bytes` on the hook path and
    assert the skill opens it exactly once per `/up-to-date` run. The test
    feeds a trusted entry so execution proceeds, then asserts (a) `open`
    call count == 1, (b) the bytes handed to the hasher are identical
    (by `id()` or by mutation-probe) to the bytes handed to the LLM feed.
    A second test mutates the on-disk file between the mocked read and
    execution and asserts the in-memory bytes (not the mutated file) reach
    the LLM. Without this test the TOCTOU mitigation is unenforced.
  - `resolve_chop_root` covers: env var set and valid, env var set
    but path missing, env var unset and fallback valid, env var unset
    and fallback missing (returns `None`), env var points at a
    directory that exists but lacks `claude-md/global.md` (returns
    `None`).
  - Orchestrator-level test: when `resolve_chop_root` returns `None`,
    the emitted JSON contains an `errors[]` entry tagged
    `shared_claude_md.chop_root_unresolved` and has no
    `shared_claude_md` key.
- Integration test: run `diagnose.py` in this worktree and assert the
  detection fields match the current machine (`orbstack-dev`, `true`).
- Manual verification:
  - Run `/up-to-date` from the worktree on this VM, walk through the
    one-time setup flow end to end.
  - **Step 0** (`@`-import symlink resolution probe) — run the protocol
    documented in the Step 0 section under Risks. Must pass before any
    other manual verification. On fail, switch to absolute-path imports
    per that section and update the spec before re-running.

## Risks and open questions

### Risk: `@`-imports may not follow symlinks

Claude Code's `@path.md` syntax in `CLAUDE.md` is the linchpin of the
design. If it does not follow symlinks, the design still works — swap each
`@~/.claude/claude-md/<name>.md` for `@~/gits/chop-conventions/claude-md/<target>.md`
— but the symlink-installed layer becomes a "setup is complete" marker
rather than the load path, and `diagnose.py`'s drift detection becomes
the primary reason the symlinks exist.

This risk is addressed by **Step 0** of the implementation plan (below).
The plan does not start the real work until Step 0 passes.

### Step 0: `@`-import symlink resolution test

**Purpose.** Verify that a chain
`~/.claude/CLAUDE.md` → (via `@`-import) → `~/.claude/claude-md/test.md`
(symlink) → `<real file outside ~/.claude>` causes Claude Code to surface
the real file's contents in the next session's context.

**Protocol.**

1. Pick a unique marker string not present anywhere else in Igor's dotfiles
   tree: `CLAUDE_MD_IMPORT_PROBE_$(uuidgen | tr -d -)`. Store it in a
   shell var `MARKER` so it can be grepped later.
2. Write the real target outside `~/.claude/` so the test has nothing to
   do with any existing file:

   ```bash
   mkdir -p /tmp/claude-md-probe
   printf '# probe\n\nmarker=%s\n' "$MARKER" > /tmp/claude-md-probe/test.md
   ```

3. Create the real directory and symlink:

   ```bash
   mkdir -p ~/.claude/claude-md
   ln -s /tmp/claude-md-probe/test.md ~/.claude/claude-md/probe.md
   ```

4. Append (do **not** overwrite) one `@`-import line to `~/.claude/CLAUDE.md`:

   ```bash
   printf '\n@~/.claude/claude-md/probe.md\n' >> ~/.claude/CLAUDE.md
   ```

5. Start a **new** Claude Code session (the existing session has
   `~/.claude/CLAUDE.md` cached) and, as the first user message, ask
   literally: `What is the marker value in the CLAUDE.md context? Return
   only the marker string or the word MISSING.`
6. **Pass:** the assistant's reply contains the exact `$MARKER` value.
   **Fail:** the reply is `MISSING`, a refusal, or a different string.

**Cleanup (run whether Step 0 passes or fails).**

```bash
rm -f ~/.claude/claude-md/probe.md /tmp/claude-md-probe/test.md
rmdir /tmp/claude-md-probe 2>/dev/null
# remove only the probe @-import line, leave the rest of CLAUDE.md intact
sed -i.bak '/^@~\/\.claude\/claude-md\/probe\.md$/d' ~/.claude/CLAUDE.md
```

**On fail.** Do not proceed with the rest of the plan. Switch the whole
design to absolute-path `@`-imports (e.g.
`@~/gits/chop-conventions/claude-md/global.md`); the installed-symlink
layer remains as a drift-detection anchor but no longer participates in
loading. Update the spec before continuing.

**Why a UUID marker and not a fixed string.** A fixed string risks
false positives from prior probe runs leaking into Claude's context via
cache. A fresh UUID per run forces a round-trip through the live file
read path.

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

### Risk: chop-conventions checkout path varies per machine

Linux: `/home/developer/gits/chop-conventions`. Mac:
`/Users/idvorkin/gits/chop-conventions` (or wherever Igor clones it).
The symlink `target` stored in the JSON and materialized by `ln` must be
the resolved absolute path on each machine, not a hardcoded constant.
`resolve_chop_root` reads `CHOP_CONVENTIONS_ROOT` from the
environment and falls back to probing `~/gits/chop-conventions`. If
neither yields a directory containing `claude-md/global.md`, the
orchestrator emits an error entry in `errors[]` and omits the
`shared_claude_md` block entirely (no `expected_symlinks`, no `actions`)
rather than silently writing symlinks to a path that does not exist. The
JSON example in this spec hardcodes `/home/developer/...` only because
the spec is written on the dev VM; implementation must not hardcode.

**Error contract for the skill.** When `errors[]` contains an entry
tagged `shared_claude_md.chop_root_unresolved`:

1. The rest of `/up-to-date` (pull, push, branch cleanup, post-hook)
   **still runs**. Failing to find the chop checkout on one machine must
   not block syncing the current repo — the two subsystems are
   independent.
2. Step 3.5 (shared-CLAUDE.md setup) is **skipped**; the skill neither
   prompts nor creates symlinks nor asks to enable.
3. The error is surfaced once to Igor at the end of the run as:
   "Shared CLAUDE.md subsystem disabled this run: `<error.message>`.
   Set `CHOP_CONVENTIONS_ROOT=<path>` or clone to `~/gits/chop-conventions`
   to enable." No retry loop, no re-prompt — the user fixes it and
   re-runs.
4. If `.enabled` exists on this machine and the chop root is
   unresolvable, the skill stat-checks `.enabled` directly (one call,
   no dependency on the omitted `shared_claude_md` block) and appends
   a second line to the end-of-run notice: "`.enabled` is present but
   no chop checkout was resolved — existing slot symlinks are
   dangling until you set `CHOP_CONVENTIONS_ROOT` or clone the repo
   to `~/gits/chop-conventions`." The skill does not inspect or delete
   the slot symlinks themselves, does not delete `.enabled`, and does
   not re-attempt the setup flow. This is a warning, not a fatal error.

The error entry's schema is `{"subsystem": "shared_claude_md", "code":
"chop_root_unresolved", "message": "...", "probed": ["$CHOP_CONVENTIONS_ROOT",
"~/gits/chop-conventions"]}`. Other `errors[]` entries produced by the
shared_claude_md subsystem follow the same `{subsystem, code, message,
...}` shape.

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

1. `diagnose.py` reports `machine=orbstack-dev, dev_machine=true` on this
   VM without shelling out for detection.
2. First `/up-to-date` run on this VM after the PR lands prompts for
   enable, creates `~/.claude/claude-md/.enabled`, sets up all three
   symlinks, and prints the `@`-import lines to add to `~/.claude/CLAUDE.md`.
3. Second `/up-to-date` run on this VM is silent about shared CLAUDE.md
   (all slots `drift=false`, zero actions).
4. `/up-to-date` on Igor's Mac after the PR lands detects
   `machine=mac, dev_machine=false` and, after opt-in, sets up `global.md`
   and `machine.md` symlinks but not `dev-machine.md`.
5. Creating `.claude/post-up-to-date.md` in any repo and running
   `/up-to-date` from that repo triggers the markdown's instructions
   after the first-sight trust prompt. Re-running immediately skips the
   prompt (hash unchanged). Editing the file and re-running re-prompts
   (hash changed).
6. Editing `chop-conventions/claude-md/global.md` on the primary checkout
   propagates to every enabled machine on its next Claude Code session
   without any additional action.
7. Unsetting `CHOP_CONVENTIONS_ROOT` and moving the chop clone to an
   unexpected path makes `/up-to-date` surface a
   `shared_claude_md.chop_root_unresolved` error at the end of the run
   but does NOT block the repo sync itself.
