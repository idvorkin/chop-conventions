# Repo Doctor: convert playbook

Loaded on demand from [SKILL.md](SKILL.md). Each section fixes exactly one doctor finding. Run the
doctor again after the PR merges; never batch several findings into one PR, because the judgment items
(vision, stories, JTBD) wait on Igor and the mechanical ones should not wait with them.

## Ground rules

1. **Read the target repo's `AGENTS.md` (or `CLAUDE.md`, or `README.md` if neither exists) first.** Its
   workflow wins over anything here: igor2 has no PRs, cockpit merges its own, blog PRs target the
   idvorkin canonical from the fork.
2. **Branch + PR, never the default branch.** `git -C <repo> switch -c repo-doctor/<check>` (or a
   worktree under `.worktrees/` when the repo's instructions ask for one), commit, `gh pr create` against
   the repo's canonical. If the clone is a fork, push to `origin` and PR to the `idvorkin/*` canonical.
3. **Re-run the doctor on the branch before opening the PR** and paste the before/after lines for that
   check into the PR body.
4. **Nothing is generated blind.** Vision, stories and JTBD are drafts for Igor; the PR says so in its
   title (`draft:`) and the doctor stays red until he approves and it merges.
5. **Do not touch other findings in passing.** A `WARN` noticed on the way is a bead in the target repo
   (`bd -C <repo> create ...`), not a second hunk in this PR.

## `agents_md`

- Missing: write `AGENTS.md` from the repo's README and any `CLAUDE.md` (fold the latter in). Shape from
  exercise-analyzer: a one-paragraph "what this is", a _Read these before working_ table, a _Rules_ list.
  Then `ln -s AGENTS.md CLAUDE.md` (cockpit) or the two-line import exercise-analyzer uses:

  ```
  Read AGENTS.md in full before doing anything in this repo. It is imported below so it loads with this file.

  @AGENTS.md
  ```

- Separate `CLAUDE.md` (drift): diff the two, move every rule that only lives in `CLAUDE.md` into
  `AGENTS.md`, replace `CLAUDE.md` with the symlink or import. Say in the PR which rules moved.

## `agents_pointers`

Add one line per missing convention to `AGENTS.md`, in the table the repo already has (or a short
_Conventions_ list): `just --list` for the recipes, `bd` for issues, `docs/stories/README.md` as the spec,
`VISION.md` as the acceptance policy, `.no-mistakes.yaml` as the gate. Pointers, not prose; the doctor
only greps for the names.

## `beads`

```bash
cd <repo>
bd init -p <prefix> --skip-hooks --skip-agents --non-interactive
echo '.beads/' >> .git/info/exclude
git config beads.role maintainer          # fork clones: otherwise bd routes creates to ~/.beads-planning
bd config set routing.mode explicit
```

Prefix = the repo's short name (`cockpit`, `blog`, `bugkit`). `.git/info/exclude` and the two config
lines are machine-local, so the PR for this finding is only the `AGENTS.md` pointer (above); the init
itself is done on the box and reported, not committed. Beads stay local to the machine (Igor 2026-09-19);
do not add a Dolt remote.

## `beads_local`

- Not excluded: `echo '.beads/' >> .git/info/exclude` on the box (no PR).
- Tracked files: `git rm -r --cached .beads` on a branch, PR it, and tell Igor which files were tracked;
  a repo that committed `.beads/` on purpose (a shared tracker) is a question for him, not a silent fix.

## `beads_hooks`

```bash
git -C <repo> config --unset core.hooksPath
```

and delete any `bd`-written script in `.git/hooks/`. No PR: both are local. If the repo relies on a
pre-commit framework (`prek`, `pre-commit`), reinstall it afterwards (`prek install`). Never let `bd init`
install hooks again: its pre-commit ignored prek's exit status in larry-blog for months.

## `justfile`

Missing: add a justfile whose first recipe is

```just
default:
    @just --list
```

then `test`, and the repo's real build/run/install commands, each wrapping what the README already tells
a human to type (`uv run pytest`, `swift test`, `xcodebuild ...`, `npm run build`). Keep the recipe body
to the command; logic longer than a line goes in `scripts/` and the recipe calls it. Update
`.no-mistakes.yaml` `commands.test` to `just test` in the same PR only if it was already pointing at the
command you wrapped; otherwise leave the gate alone.

Missing `test` or build recipe: add the recipe, same rules. cockpit's `uv run pytest -q -n 4 -rs` is a
`test` recipe waiting to happen.

## `scripts_via_just`

One recipe per script the doctor lists, named after the script without its extension; a script no recipe
can justify is a candidate for deletion, which is Igor's call (bead it).

## `vision`

Run the vision skill in the target repo (`/vision`; `~/gits/vision/skills/vision/SKILL.md`). It mines
merged PRs and commits, drafts `VISION.md`, and reviews it with Igor on a lavish board. The skill owns the
process; the conversion is only: start it, and open the PR as `draft: VISION.md` once Igor approves on the
board. Never write a `VISION.md` by hand from the README.

## `no_mistakes`

Copy the shape from exercise-analyzer or lcd-timer (`commands.test: "just test"`, `no_ci` when there are
no workflows, `review.path_instructions` for the one or two files with non-obvious rules) or cockpit
(lint/format commands, `protected_paths`, `document` and `test` instructions). Gate-control fields are
read from the default branch, so the PR body says the gate turns on at merge. A repo that has no tests yet
gets `commands.test` pointing at whatever `just test` does today, even if that is a build.

## `stories`

Drafts, for Igor. Copy `docs/stories/README.md` from exercise-analyzer (iOS app), cockpit (web page) or
lcd-timer (Mac app), change the persona paragraph and the journey table, then write one journey file per
surface with the stories the repo's README, issues and commits already imply. Format per story, in order:
Summary, Status, Why (optional), Use Case (As a / I want to / so that), Acceptance Criteria (one When and
one Then per scenario), Notes (optional), Issues. Status names real commits only; a story without one is
`not implemented`. A repo with stories in another layout (`docs/user-needs.md`) is migrated, not
duplicated: the old file becomes a pointer to `docs/stories/README.md`.

## `jtbd`

Draft, for Igor. `docs/jtbd.md` in context-grabber's shape: front matter (date, version, sources,
status), the product in one sentence, the jobs (functional, emotional, social) each tied to a source Igor
wrote (a blog post, a journal line he quoted), and the non-jobs. When `VISION.md` exists, a
`## Jobs to be done` section there is acceptable instead; ask Igor which he wants before writing either.

## `bug_reporting`

- iOS without bug-kit: follow `bug-kit/docs/integration.md` (package dependency, `BugKitCommit` in
  Info.plist, the reporter at launch). PR to the app repo; a story in `docs/stories/` for the shake and
  the report, as exercise-analyzer's 06-feedback.md.
- Non-iOS (`GAP`): nothing to do here. The bug-kit-like library for non-iOS repos is its own project
  (Igor 2026-10-11, report only); if he asks for it, it starts as a bead in bug-kit, not as a conversion.
