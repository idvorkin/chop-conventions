---
name: bulk
description: Bulk-parallel CLIs — turn N sequential gh/bd/git/file tool calls into a single fan-out JSON call. Use when the session is about to fire ≥3 similar sequential calls (gh pr view, bd show, Read of small files, up-to-date diagnose across repos).
allowed-tools: Bash, Read
---

# Bulk Parallel Tools

One tool call firing N parallel sub-calls beats N sequential calls on
wall-clock almost every time, and it keeps main-thread context
cleaner. This skill ships five fan-out CLIs that shell out to `gh` /
`bd` / `diagnose.py` / filesystem across a `ThreadPoolExecutor` and
emit one JSON array.

## When to use

Trigger when you're about to make ≥3 similar sequential tool calls:

- "Check the state of these N PRs" → `bulk-gh-pr-details`
- "List open PRs across these N repos" → `bulk-gh-prs-open`
- "Show me these N beads" → `bulk-bd-show`
- "Diagnose git state across these N repos" → `bulk-up-to-date`
- "Give me quick text of these N files" (each likely < 1 MB) →
  `bulk-file-read`

If the count is 1 or 2, use the direct tool call — bulk wins on
wall-clock but loses on boilerplate for small Ns.

## Install

Packaged as `chop-bulk`. Install via:

```bash
cd ~/gits/chop-conventions
uv tool install --force --reinstall ./skills/bulk/
```

Once PR #169 (packaged skill CLIs) lands, `install-tools.py` will pick
up `chop-bulk` from its REGISTRY and install it alongside the others.

After install, five binaries land on `$PATH`:

- `bulk-gh-pr-details`
- `bulk-gh-prs-open`
- `bulk-bd-show`
- `bulk-up-to-date`
- `bulk-file-read`

## Usage

Every tool takes inputs three ways — pick whatever's convenient:

1. **Positional args** (most common when the list is in plain text):

   ```bash
   bulk-gh-pr-details idvorkin/chop-conventions#169 idvorkin/chop-conventions#168
   ```

2. **`--input-file path.json`** (when the list is already on disk as JSON):

   ```bash
   bulk-bd-show --input-file /tmp/beads.json
   ```

3. **stdin JSON array** (for piping from another tool):

   ```bash
   echo '["igor2-bgt","igor2-88g","igor2-5i1"]' | bulk-bd-show
   ```

All tools support `--max-workers N` (default 8) and `--pretty`.
Output is JSON to stdout; progress/errors go to stderr. Per-item
failures are captured inline as `{..., "error": "..."}`. The batch
never partial-fails.

## Tools

### `bulk-gh-pr-details`

Input: `owner/repo#N` specs. Output per PR:

```json
{"repo": "idvorkin/chop-conventions",
 "number": 169,
 "title": "feat(packaging): migrate ...",
 "state": "OPEN",
 "mergeable": "MERGEABLE",
 "mergeStateStatus": "CLEAN",
 "url": "https://github.com/idvorkin/chop-conventions/pull/169"}
```

### `bulk-gh-prs-open`

Input: `owner/repo` slugs. Output per repo:

```json
{"repo": "idvorkin/chop-conventions",
 "open_prs": [
   {"number": 169, "title": "feat(packaging)...", "headRefName": "feat/uv-tool-packaging"}
 ]}
```

### `bulk-bd-show`

Input: bead IDs. Output per bead:

```json
{"id": "igor2-bgt",
 "title": "Telegram Infrastructure",
 "status": "open",
 "priority": 1,
 "type": "epic",
 "parent": null,
 "blocks": [],
 "blocked_by": []}
```

Handles the `bd show --json` array-vs-object gotcha internally —
callers don't need to re-unwrap.

### `bulk-up-to-date`

Input: absolute repo paths. Output per repo:

```json
{"repo": "/home/developer/gits/chop-conventions",
 "diagnose_json": { ... full diagnose.py output ... }}
```

Invokes `up-to-date-diag` if packaged (PR #169) else the script at
`~/.claude/skills/up-to-date/diagnose.py`. `cwd` is set to the target
repo so diagnose introspects the right directory.

### `bulk-file-read`

Input: absolute file paths. Output `{path: {size_bytes,
content_utf8_or_null, error_or_null}}`. Files > `--max-bytes` (default
1 MB) are skipped with an error, not loaded — keeps output sane on
accidental big-file globs.

## Design

- **One `pyproject.toml`, five entry points.** Matches the per-skill
  package pattern from PR #169 (gen-tts, harden-telegram,
  up-to-date each bundle their own tools).
- **`_build_app()` lazy-import of Typer.** Tests and pre-commit hooks
  import the pure-function layer (`fetch_pr`, `fetch_bead`,
  `diagnose_repo`, `_read_one`) in system Python without
  `ModuleNotFoundError`. Reference: `skills/harden-telegram` and
  `skills/up-to-date` in chop-conventions.
- **Shared `common.py`** owns input parsing, the ThreadPoolExecutor
  wiring, and the "never partial-fail" contract. The abstraction is
  justified at N=5 (the "wait for N=2" rule from CLAUDE.md).
- **Subprocess injection for tests.** Each worker fn takes a
  `run=subprocess.run` kwarg so tests mock it without patching the
  module global. Matches the `test_diagnose.py` and
  `test_telegram_debug.py` style in this repo.

## Tests

```bash
python3 -m unittest discover -s skills/bulk/tests -p 'test_*.py'
```

Covered: `bulk-gh-pr-details` spec parsing + fetch + fan-out, and
`bulk-bd-show` normalization of the array-vs-object JSON + dependency
slicing. Other tools use the same `common.py` plumbing; the two tested
paths validate the shared contract.

## Related

- `up-to-date` — single-repo version of `bulk-up-to-date`.
- `learn-from-session` — reflection prompts now include a "≥3
  sequential similar calls → propose bulk" heuristic.
