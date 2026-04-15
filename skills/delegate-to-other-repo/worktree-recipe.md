# Worktree Recipe (loaded on demand)

> Phase 2 shell recipe for `delegate-to-other-repo`. Read the parent
> `SKILL.md` first. Follow this recipe verbatim — the ordering of
> `set-head`, the step-by-step default-branch resolution, and the
> gitignore-commit branch guard are all load-bearing.

## Inputs

Set these variables before running the recipe:

```bash
T=<absolute path to target repo>
task_description=<user's task description, raw>
```

## Recipe

```bash
# -----------------------------------------------------------------------------
# 1. Fetch origin and refresh cached origin/HEAD.
# -----------------------------------------------------------------------------
# Plain `git fetch origin` does NOT update refs/remotes/origin/HEAD.
# That ref is set at clone time and only refreshed by an explicit
# `set-head --auto`. Without this call, a target whose default branch
# was renamed (e.g. master → main) since it was cloned would yield a
# stale value below. `--auto` is a no-op if origin/HEAD already matches.
git -C "$T" fetch origin
git -C "$T" remote set-head origin --auto >/dev/null 2>&1 || true

# -----------------------------------------------------------------------------
# 2. Determine default branch.
# -----------------------------------------------------------------------------
# Step-by-step rather than a single `||`-chain because piping through
# `sed` swallows the upstream exit code: a chained `|| echo main` never
# fires when symbolic-ref fails, because the empty pipe output wins
# (verified: T=/tmp/nonexistent ... echo main returned '').
default_branch=""
ref=$(git -C "$T" symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null) && \
  default_branch="${ref#origin/}"
if [ -z "$default_branch" ]; then
  # `gh repo view` accepts OWNER/REPO as a positional argument only;
  # it has no `-R`/`--repo` flag on this subcommand (verified via
  # `gh repo view --help` — INHERITED FLAGS section shows only
  # `--help`). `gh repo view` also does not accept a filesystem path,
  # so we parse the slug from origin URL and pass it positionally.
  origin_url=$(git -C "$T" remote get-url origin 2>/dev/null)
  slug_repo=$(printf '%s\n' "$origin_url" \
    | sed -E 's#(\.git)?$##; s#^.*[/:]([^/:]+/[^/:]+)$#\1#')
  default_branch=$(gh repo view "$slug_repo" --json defaultBranchRef \
    -q .defaultBranchRef.name 2>/dev/null)
fi
default_branch="${default_branch:-main}"

# -----------------------------------------------------------------------------
# 3. Verify origin/<default> is reachable.
# -----------------------------------------------------------------------------
if ! git -C "$T" rev-parse --verify "origin/$default_branch" >/dev/null 2>&1; then
  echo "STOP: origin/$default_branch is not reachable in $T after fetch."
  exit 1
fi

# -----------------------------------------------------------------------------
# 4. Derive slug from task description.
# -----------------------------------------------------------------------------
# Reproducible rule:
#   1. Lowercase
#   2. Replace every char outside [a-z0-9] with `-`
#   3. Collapse repeated `-`, strip leading/trailing `-`
#   4. Truncate to 40 chars; re-strip trailing `-`
#   5. If the result is empty (non-ASCII, pure punctuation, empty
#      input), fall back to "task-$(date +%Y%m%d-%H%M%S)"
#   6. If a branch named `delegated/<slug>` already exists in $T,
#      append `-2`, `-3`, ... `-9`; beyond that, fall back to the
#      timestamp form.
slug=$(printf '%s' "$task_description" \
  | tr '[:upper:]' '[:lower:]' \
  | sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//' \
  | cut -c1-40 \
  | sed -E 's/-+$//')
[ -z "$slug" ] && slug="task-$(date +%Y%m%d-%H%M%S)"

# Collision check covers BOTH local branches and remote-tracking refs.
# Checking only refs/heads/ misses the case where the same name exists
# on the remote — worktree add would succeed locally, but the
# subagent's eventual push would be rejected as non-fast-forward
# (force-push is prohibited). We already fetched origin above, so
# refs/remotes/origin/ is up-to-date.
i=1
candidate="$slug"
while git -C "$T" show-ref --verify --quiet "refs/heads/delegated/$candidate" \
   || git -C "$T" show-ref --verify --quiet "refs/remotes/origin/delegated/$candidate"; do
  i=$((i + 1))
  if [ "$i" -gt 9 ]; then
    candidate="task-$(date +%Y%m%d-%H%M%S)"
    break
  fi
  candidate="$slug-$i"
done
slug="$candidate"
branch="delegated/$slug"
path="$T/.worktrees/delegated-$slug"

# -----------------------------------------------------------------------------
# 5. Ensure .worktrees/ is excluded from the parent's git status.
# -----------------------------------------------------------------------------
# Linked worktrees nested inside the parent repo show up in `git status`
# as untracked (verified empirically: `git worktree add .worktrees/wt1`
# leaves `?? .worktrees/` in the parent's status output). We need an
# ignore entry — but we deliberately avoid committing to `.gitignore`.
#
# Why not commit .gitignore on the default branch?
#   - The delegated branch is created from `origin/$default_branch`
#     (a clean remote ref) so a local-only commit on the default
#     branch wouldn't be in the delegated branch's base anyway.
#   - Committing on the target's *current* branch would pollute
#     whatever branch happens to be checked out — silently disappearing
#     on next checkout.
#   - Switching the target's current branch is destructive and
#     requires the user's local state to be clean, which we don't
#     enforce.
#   - Branch-protected default branches block direct commits.
#
# Instead: use `.git/info/exclude`, the local-only, untracked,
# branch-independent, per-repo exclude list documented in gitignore(5).
# It sits in the shared git dir, applies to all worktrees, survives
# branch switches, and never touches any branch's history.
git_common=$(git -C "$T" rev-parse --git-common-dir 2>/dev/null)
exclude_file="$git_common/info/exclude"
if ! grep -qxF '.worktrees/' "$exclude_file" 2>/dev/null; then
  mkdir -p "$git_common/info"
  printf '\n# Added by delegate-to-other-repo skill\n.worktrees/\n' >> "$exclude_file"
fi

# Sanity check — `.worktrees/` must now be ignored. `check-ignore`
# respects `.git/info/exclude` as well as `.gitignore`.
if ! git -C "$T" check-ignore -q .worktrees; then
  echo "STOP: failed to exclude .worktrees/ via $exclude_file."
  echo "This should be impossible; investigate manually."
  exit 1
fi

# -----------------------------------------------------------------------------
# 6. Create the worktree.
# -----------------------------------------------------------------------------
# `worktree add` is the only step that mutates the working set. If it
# fails (path collision, ref missing), STOP and report.
git -C "$T" worktree add "$path" -b "$branch" "origin/$default_branch"

echo "Worktree ready: $path"
echo "Branch:         $branch"
echo "Base:           origin/$default_branch"
```

## Why this skill does NOT call `superpowers:using-git-worktrees`

That skill:

- Branches off current HEAD — no way to pass a base ref like
  `origin/<default>`
- Auto-runs `npm install` / `cargo build` / `pip install` — noisy
  and wrong for doc-only or tiny changes
- Runs the target's test suite as a baseline — slow, and unnecessary
  before the subagent has done anything

Those are good defaults for same-repo feature work but wrong defaults
for cross-repo delegation off a fresh `origin/<default>`. This recipe
is the explicit alternative.

## Output

On success:

- Worktree at `$T/.worktrees/delegated-<slug>` checked out to
  `delegated/<slug>` branch, based on `origin/<default-branch>`
- Possibly one new line appended to `.git/info/exclude` ensuring
  `.worktrees/` is ignored. This is local-only, untracked, and
  shared across all worktrees via the common git dir — no branch
  history is mutated.

Pass `$path` and `$branch` forward to Phase 3 (brief construction).
