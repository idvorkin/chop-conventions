# Skill Grouping Migration

This document captures the pattern used for the `image/` pilot migration
(2026-04-13) so remaining groups can follow the same steps.

## Background

`skills/` held 15 flat top-level skills. We grouped related skills into
subdirectories (`skills/<group>/<skill>/`) to improve discoverability
and signal relationships at a glance.

## Key insight: the loader uses symlink names, not source paths

Claude Code discovers skills from `~/.claude/skills/<name>` (or
`<project>/.claude/skills/<name>`). Those are **symlinks** into this
repo. The loader never walks into a group subdirectory — it only sees
the flat names the symlinks expose.

**Consequence**: grouping is purely a source-repo concern. No loader
changes are required. Symlinks just need to be updated to point at the
new location.

This was verified empirically: after moving `gen-image`, `gist-image`,
and `image-explore` into `skills/image/`, skills continue to work when
symlinks are re-pointed at the new paths.

## Migration steps (repeat per group)

### 1. Move files

```bash
mkdir -p skills/<group>
git mv skills/<skill-a> skills/<group>/<skill-a>
git mv skills/<skill-b> skills/<group>/<skill-b>
```

### 2. Update hardcoded repo-relative paths

Skills often reference sibling scripts via an absolute path assembled
from `$CHOP_ROOT`. After moving, update every occurrence of
`skills/<skill-name>/` in SKILL.md files and helper scripts.

**Common pattern in SKILL.md:**

```bash
# Before
GEN="$CHOP_ROOT/skills/image-explore/generate.py"

# After
GEN="$CHOP_ROOT/skills/image/image-explore/generate.py"
```

**Common pattern in .py helpers:**

```python
# Before
script = chop_root / "skills" / "gen-image" / "gemini-image.sh"

# After
script = chop_root / "skills" / "image" / "gen-image" / "gemini-image.sh"
```

Run this grep after each move to catch remaining stale references:

```bash
grep -rn "skills/<old-skill-path>" .
```

### 3. Create `skills/<group>/README.md`

Describe the group's purpose, list members, and show the updated
install commands.

### 4. Update consumer docs

- `README.md` — update the Available Skills table
- `CLAUDE.md` — update any `skills/<name>/` path references or prose
- Any other `.md` files surfaced by the grep in step 2

### 5. Update symlinks on each machine

After a `git pull`, re-point the affected symlinks:

```bash
CHOP="$HOME/gits/chop-conventions"
ln -sf "$CHOP/skills/<group>/<skill>" ~/.claude/skills/<skill>
```

The `up-to-date` skill's post-pull delta check will surface newly moved
skills so the user is reminded to update their symlinks.

## Completed migrations

| Group | Skills | Date |
|---|---|---|
| `image/` | `gen-image`, `gist-image`, `image-explore` | 2026-04-13 |

## Proposed remaining groups

| Group | Candidate members |
|---|---|
| `git/` | `up-to-date`, `delegate-to-other-repo` |
| `telegram/` | `harden-telegram` |
| `claude-code/` | `background-usage`, `machine-doctor` |
| `dev/` | `architect-review`, `learn-from-session`, `docs`, `showboat`, `build-bd` |
| `scheduling/` | `clock` (promote to group when ≥2 members) |
| top-level (personal) | `ammon` |
