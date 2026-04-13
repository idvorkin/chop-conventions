# Fork Detection (loaded on demand)

> Reference for the 4-case decision tree the subagent uses in Phase
> 3's brief. Read parent `SKILL.md` first. This file exists as a
> spec-reader reference — the _subagent_ receives the tree as prose
> inside `brief-template.md`.

## Why this needs its own file

The subagent operates in a fresh context with no conversational
history. Fork detection is the single most mistake-prone step of
cross-repo work:

- **Case C (fork-as-origin)** is the real-world pattern for
  chop-conventions itself. Misclassifying it as "direct push" silently
  lands PRs on the user's own fork rather than the canonical repo.
- **`up-to-date/diagnose.py` cannot detect Case C.** It classifies
  purely by URL owner against a hardcoded `FORK_ORGS` list and has no
  concept of `gh auth status` or `gh repo view --json parent`.

So the brief spells out the full tree explicitly, and this file
documents it with a diagram for humans reviewing the skill.

## The 4 cases

### Case A: Two-remote fork workflow

**Shape:** both `origin` and `upstream` remotes exist.
**Test:** `upstream` URL is canonical, `origin` URL is a fork.
**Action:** push branch to `origin`, open PR with
`gh pr create --repo <canonical-owner>/<repo>`.

If the remotes are swapped (origin canonical, upstream fork), STOP
and report — do not guess which direction the user intended.

### Case B: Direct-push workflow (single canonical origin)

**Shape:** only `origin` exists.
**Test:** `gh repo view <owner/repo> --json isFork` returns `false`,
AND the `owner` segment of `origin`'s URL matches the active
`gh auth status` account.
**Action:** push branch to `origin`, open PR with plain
`gh pr create` (no `--repo` flag).

### Case C: Fork-as-origin (the chop-conventions pattern)

**Shape:** only `origin` exists, and it IS a fork.
**Test:** `gh repo view <owner/repo> --json isFork` returns `true`,
AND the `owner` of `origin` matches the active auth account.
**Action:** push branch to `origin`, open PR with
`gh pr create --repo <parent-owner>/<parent-repo>` where `parent`
comes from `gh repo view <owner/repo> --json parent`.

**Real example:** chop-conventions' `origin` is
`idvorkin-ai-tools/chop-conventions` (a fork, matching the
`idvorkin-ai-tools` auth account), and PRs go to the parent
`idvorkin/chop-conventions`. There is NO separate `upstream` remote.

### Case D: Canonical-only origin, no fork wired up

**Shape:** only `origin` exists.
**Test:** `gh repo view <owner/repo> --json isFork` returns `false`,
AND `origin`'s owner does NOT match the auth account.
**Action:** look for any other remote whose URL owner segment matches
the auth account. If found, push to it and
`gh pr create --repo <canonical>`. If none found, STOP and fail with:

> "cannot push to a repo this auth account does not own; set up the
> fork first with `gh repo fork --remote --remote-name=fork`"

Note: "any other remote" is a label imprecision — if only one remote
exists (the `origin` we just classified), the search trivially returns
empty and falls to the STOP path. This is correct behavior.

## Decision tree diagram

```text
                       +------------------+
                       | How many remotes |
                       |  (origin + other)|
                       +--------+---------+
                                |
              +-----------------+----------------+
              |                                  |
          2 remotes                          1 remote
              |                                  |
   +----------+----------+            +----------+----------+
   | upstream = canonical|            | gh repo view origin |
   | origin   = fork?    |            |   --json isFork,    |
   +----------+----------+            |       parent        |
              |                       +----------+----------+
      +-------+--------+                         |
     yes              no             +-----------+-----------+
      |                |             |                       |
      v                v         isFork=true            isFork=false
  CASE A:         Remotes are        |                       |
  TWO-REMOTE      swapped; STOP      v                       v
  FORK WORKFLOW   and report     +-------+              +---------+
  push origin     (don't guess)  | owner | == auth?     | owner   | == auth?
  PR --repo                      +---+---+              +----+----+
  canonical                          |                       |
                              +------+-----+          +------+------+
                              |            |          |             |
                             yes           no        yes            no
                              |            |          |             |
                              v            v          v             v
                          CASE C:      Look for   CASE B:       CASE D:
                          FORK-ORIGIN  any other  DIRECT-PUSH   CANONICAL-
                          push origin  remote     WORKFLOW      ORIGIN, NO
                          PR --repo    matching   push origin   FORK WIRED
                          parent       auth org;  PR (no        Look for any
                          (slug from   if found,  --repo)       remote matching
                          .parent in   push there,               auth org. If
                          gh JSON)     PR --repo                 found, push
                                       <canonical>               there, PR
                                       Else STOP:                --repo origin.
                                       "set up                   Else STOP:
                                       fork first"               same fail msg.
```

## Interaction with `up-to-date/diagnose.py`

The subagent is told to run `diagnose.py` as a shortcut if it exists:

```bash
~/.claude/skills/up-to-date/diagnose.py --pretty | jq .remotes
```

### What `diagnose.py` covers

- **Case A reliably.** `classify_remotes` detects two-remote fork
  workflows by URL pattern and sets `is_fork_workflow: true` with
  `source: "upstream"`.
- **Simple Case B reliably.** Single canonical origin → `is_fork_workflow: false`.

### What `diagnose.py` MISSES

- **Case C entirely.** The script has no concept of `gh auth status`.
  It sees a single fork-as-origin and returns `is_fork_workflow: false`
  with no issues — silently misclassifying as direct-push.
- **Case D entirely.** Same reason — no auth-vs-owner comparison.

### Rule for the subagent

Treat `diagnose.py`'s `is_fork_workflow: true` as authoritative for
Case A routing. Treat `is_fork_workflow: false` as "probably Case B,
but verify" — ALWAYS run the manual `gh repo view --json isFork,parent`
check before routing to Case B or Case C. Never skip that check on
the single-remote path.

## Common misclassifications (test these if changing the tree)

1. **chop-conventions itself.** origin =
   `idvorkin-ai-tools/chop-conventions`, auth = `idvorkin-ai-tools`,
   PRs to `idvorkin/chop-conventions`. Expected: Case C. A naive
   classifier routes to Case B.
2. **Vanilla OSS contribution.** origin =
   `your-user/project`, upstream = `project-org/project`, auth =
   `your-user`. Expected: Case A.
3. **Personal project.** origin = `your-user/notes`, no upstream,
   auth = `your-user`, origin is NOT a fork. Expected: Case B.
4. **Drive-by collaborator fork.** origin =
   `maintainer-org/project`, auth = `your-user`, origin is NOT a
   fork. Expected: Case D, STOP with "set up fork first" message
   (because we can't push to `maintainer-org`).
