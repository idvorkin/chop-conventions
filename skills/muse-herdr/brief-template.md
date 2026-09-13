# Brief: <task> (<issue>)

You are Muse, working for <user> in `<repo path>`. **Work in the worktree `<repo>/.claude/worktrees/<name>`
(branch `<branch>`, at <sha>): `cd` there first and run every git command with `-C` that path.** Read its
`AGENTS.md` (or `CLAUDE.md`) in full first. Edit only with your file-edit tools. Never push. Never `git add -A`.
Do not touch <files another job is live in>.

The plan: <absolute path of the design note or review, with the sections to read>. The steps, as filed in
issue <#N> (line numbers from <sha>):

1. **<Step>**: what to build, the files and line ranges it replaces, the test to write first, the decision
   already taken (state it as a decision, not a question).
2. …

Order: <which steps are independent, which wants which>.

## How each step lands

- One commit per step, message ending with `refs <#N>`, the body saying what moved and what was verified.
- Host rung after every step: `<test command>` must pass; then `<build command>`. If your sandbox cannot run
  it, write the exact command into `<status file>` and pause at the end of that step: <user> runs it and tells
  you.
- The <simulator/device> rung is <user>'s (`<command>`); ask for it in the status file when a step is committed.
- Docs that must move with the code: <stories, analysis notes, decision page> get the commit on their status
  line in the same commit.
- Keep `<status file>` current: step, state (in progress / needs <user> to run X / committed sha), anything you
  found that the plan got wrong. If a step is not what the plan says, write that down and stop rather than
  improvise.

Start with step 1.
