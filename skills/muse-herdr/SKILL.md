---
name: muse-herdr
description: Drive a Muse Code instance in a Herdr pane as a sub-agent — a precise brief in a file, a worktree of its own, a watcher that wakes you on its approval prompts, and headless `muse exec` for batch grading or image labelling. Muse is the cheap model; the heavy thinking, the decisions and anything the sandbox cannot run stay with the caller.
allowed-tools: Bash, Read, Write
---

# Muse through Herdr

Muse (Igor's Muse Code, contributor tier, model `muse-spark-*`) is a sub-agent that costs a few dollars a night
(2026-09-12: ~$3.90 for 408 sessions, 152 M input tokens, mostly cache reads; Sonnet-class API pricing would have
been ~$150). It can see images. It runs sandboxed: it reads and writes the repo and `/tmp`, cannot write `~/tmp`,
and may not be able to run Xcode, simulators or Core ML. It writes good analysis and test code from a precise
brief; it needs the caller to decide, to run anything heavy, and to answer its approval prompts.

Before any Herdr command, confirm you are inside a Herdr pane (`test "${HERDR_ENV:-}" = 1`), and read the
`herdr` skill for the CLI. Never restart the user's own Muse pane to change its profile.

## 1. Interactive Muse in a pane (design, code, refactors)

1. **Brief in a file, not in the prompt.** Write `/tmp/<scope>/<task>-brief.md` (Muse can read `/tmp`; it
   cannot read a note under `~/tmp`). `brief-template.md` next to this file is the shape: who it is, where to
   work, what to read first, the steps with file:line references, what it must not touch, how each step lands
   (commit format, tests, the rung it cannot run), where to write status. Decisions already taken are stated as
   decisions, not questions.
2. **Its own worktree.** `git worktree add .claude/worktrees/<name> -b <branch>`; the brief tells it to `cd`
   there and to pass `-C <path>` to every git command. Your uncommitted experiments in the main checkout stay
   yours.
3. **Start or reuse the pane.** An existing Muse pane: `herdr agent list` shows its name. A new one needs a
   free shell pane in the repo: `herdr agent start <name> --kind muse --pane <pane-id>`. Auto-approve flags on
   the agent's command line are refused by the auto-mode classifier; use the default profile.
4. **Prompt with the file.** `herdr agent prompt <name> "Read /tmp/<scope>/<task>-brief.md and do what it says,
starting with step 1."` Do not `--wait` on a long job.
5. **Watch for approvals.** Arm a Monitor on `bash <this skill dir>/watch-blocked.sh <name>` (persistent). It
   prints one line when the agent blocks (with the last screen lines) and one when it settles. Read the prompt,
   then answer it: `herdr agent send-keys <name> y` (or the key the prompt names). Igor: "You can always
   approve Muse." Still stop for anything destructive (`rm -rf`, `reset --hard`, a push).
   Do **not** run a parser that types keys on its own: the first auto-approver matched a sub-agent's output
   and typed a stray `y` into Muse's input, which then prefixed the next prompt.
6. **Read results from files.** Muse's terminal runs on the alternate screen, so `herdr agent read` recovers
   little of a long answer; the brief names a status file (`/tmp/<scope>/<task>-status.md`) and an output
   file, and you copy them to `~/tmp/agent/notes/` when done.
7. **Verification stays on the ladder.** Muse runs the host tests if the sandbox allows; the brief tells it to
   write the exact command into the status file and pause when it cannot (a simulator run, a device build), and
   you or the user run it. One commit per step, referencing the issue, never a push from the sandbox.

## 2. Headless Muse for grinding (grading, labelling, anything with an image)

```bash
muse exec --image <png> --reasoning-effort low --prompt-file <rubric.txt>
```

About 15 s a frame; the answer's JSON is the last `{…}` line. Run **two or three in parallel at most** on a
16 GB Mac beside an interactive Muse (six got killed for memory). Make the grader resumable (skip frames already
in the CSV) and print a per-clip summary. Reference: `exercise-analyzer/scripts/model-trials/grade-dots.sh`
with `grade-dots-rubric.txt`. Never route this to Sonnet or the Claude API: Igor, "No, I don't want you to use
Sonnet. I want you to use Muse. It's way cheaper."

Ten graded frames are noise (a rule judged on ten flipped when regraded); decide on 30 or more, and expect the
grader's own count to move by one or two between runs of identical frames.

## 3. Several Muses

One pane per job with its own name and brief (`bell-lab` doing a refactor while `watch-lab` answers a design
question). They share the machine: one heavy process at a time, and the headless graders count against the
same memory.

## What goes wrong

| Symptom                                                            | Cause                                     | Fix                                                                   |
| ------------------------------------------------------------------ | ----------------------------------------- | --------------------------------------------------------------------- |
| Muse's next prompt starts with a stray `y`                         | an auto-approver typed into its input     | stop the approver, backspace the input, use the watcher + `send-keys` |
| `agent_prompt_stalled`                                             | the pane was not at an interactive prompt | `herdr agent get <name>`, read the screen, clear a dialog first       |
| a long answer is not in `agent read`                               | alternate screen                          | ask for the answer as a file under `/tmp`                             |
| "keychain item for meta is unreadable (os status -67701)" on start | Muse's own warning                        | harmless; the agent still works                                       |
| the sandbox cannot run `xcodebuild` / the simulator                | sandbox                                   | the status file names the command; the caller runs it                 |
