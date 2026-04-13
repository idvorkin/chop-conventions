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
  # `gh repo view` takes an OWNER/REPO slug positional, NOT a path, and
  # there is no top-level `gh -R` flag. Parse the slug from origin URL.
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

i=1
candidate="$slug"
while git -C "$T" show-ref --verify --quiet "refs/heads/delegated/$candidate"; do
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
# 5. Ensure .worktrees/ is gitignored.
# -----------------------------------------------------------------------------
# check-ignore needs to run with the target as cwd — it resolves
# against the index of the repo containing cwd; `git -C` handles that.
# Returns 0 if the path WOULD be ignored, 1 if not.
if ! git -C "$T" check-ignore -q .worktrees; then
  # The ignore entry MUST land on the default branch. Otherwise it
  # lives on a feature branch and disappears the moment the target
  # checks out anything else. Refuse on a non-default branch — this
  # also catches detached HEAD, where `git branch --show-current`
  # prints empty.
  current=$(git -C "$T" branch --show-current)
  if [ "$current" != "$default_branch" ]; then
    echo "STOP: target HEAD is '${current:-<detached>}', not '$default_branch'."
    echo "The .worktrees/ gitignore entry must land on the default branch."
    echo "Ask the user to either (a) check out $default_branch in $T and"
    echo "retry, or (b) land the ignore via their normal PR flow first."
    exit 1
  fi

  # On the default branch. If it is branch-protected (PR-only, required
  # reviews, required status checks), we still cannot commit-and-push
  # directly. Run the protection probe BEFORE mutating the working tree.
  #
  # `gh api` returns 0 even on 404 ({"message":"Not Found",...}), so
  # check the JSON shape rather than the exit code. False negatives
  # (auth account lacks admin read → 404 → silent no-op) are accepted:
  # the worst case is the local commit succeeds and the worktree
  # branch's eventual push is unaffected.
  origin_url=$(git -C "$T" remote get-url origin)
  slug_repo=$(printf '%s\n' "$origin_url" \
    | sed -E 's#(\.git)?$##; s#^.*[/:]([^/:]+/[^/:]+)$#\1#')
  protection_json=$(gh api "repos/$slug_repo/branches/$default_branch/protection" 2>/dev/null || true)
  if printf '%s' "$protection_json" | grep -q '"required_pull_request_reviews"\|"required_status_checks"'; then
    echo "STOP: $slug_repo's $default_branch is branch-protected. The"
    echo ".worktrees/ gitignore entry needs to land via the target repo's"
    echo "normal PR flow first; this skill will not bypass branch protection."
    exit 1
  fi

  echo ".worktrees/" >> "$T/.gitignore"
  git -C "$T" add .gitignore
  git -C "$T" commit -m "chore: gitignore .worktrees/"
  # Note: this commit is local-only on the default branch. Whether to
  # push it is the target's workflow concern, not this skill's. The
  # subagent's PR branch will contain it as part of its base.
fi

# -----------------------------------------------------------------------------
# 6. Create the worktree.
# -----------------------------------------------------------------------------
# `worktree add` is the only step that mutates the working set. If it
# fails (path collision, ref missing), STOP and report — do NOT clean
# up the gitignore commit above, because that commit is independently
# valuable and will be reused on retry.
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
- (Maybe) one new commit on the target's default branch adding
  `.worktrees/` to `.gitignore`

Pass `$path` and `$branch` forward to Phase 3 (brief construction).
