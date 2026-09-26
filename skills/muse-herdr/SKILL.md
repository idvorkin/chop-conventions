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
2. **Its own worktree, from Herdr.** `herdr worktree create --cwd <repo> --branch <name> --base main --no-focus`
   makes the checkout under `~/.herdr/worktrees/<repo>/<name>` and a pane whose shell starts there; the agent is
   started in that pane (step 3), so its process lives in the worktree and never `cd`s into it, and its commits
   cannot land on the main checkout's branch. Review its commits, `git merge --ff-only <name>`, then
   `herdr worktree remove`. Your uncommitted experiments in the main checkout stay yours. **Provision the worktree's gitignored assets before anything builds** (downloaded models, fixtures,
   `.env`): a fresh worktree has none of them, the build succeeds anyway, and the app then fails in a way that
   looks like the Muse's change (an afternoon's five 240 s timeouts in exercise-analyzer were a missing
   `.mlpackage`, not the refactor). Copy them from the main checkout or run the repo's fetch recipe.
3. **Start or reuse the pane.** An existing Muse pane: `herdr agent list` shows its name. A new one goes in
   the worktree's root pane (`root_pane.pane_id` in the create output):
   `herdr agent start <name> --kind muse --pane <pane-id>` (`--kind codex` works the same). Auto-approve flags on
   the agent's command line are refused by the auto-mode classifier; use the default profile. `agent_not_ready`
   or `agent_blocked` at start: read the pane first; a CLI self-update prompt (Codex) gets Skip
   (`send-keys <name> 2`, then `Enter`), then prompt again.
4. **Prompt with the file.** `herdr agent prompt <name> "Read /tmp/<scope>/<task>-brief.md and do what it says,
starting with step 1."` Do not `--wait` on a long job.
5. **Watch it.** Arm a persistent Monitor on `<this skill dir>/watch.py <name>`. It prints a line when the
   agent blocks on a prompt (`BLOCKED`, with the last screen lines), when its model call fails (`FAILED`), when
   its screen stops moving for five minutes (`STALLED`), when it settles, and a `WATCHER` line whenever a
   `herdr` call fails, so silence never means the watcher went blind. It exits when the agent is gone. Read
   the prompt, then answer it: `herdr agent send-keys <name> y` (or the key the prompt names). Igor: "You can always
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

## 3. Several Muses, and the panes are yours

One pane per job with its own name, brief and worktree (`bell-lab` doing a refactor while `watch-lab` answers a
design question). Igor (2026-09-13): "Feel free to use multiple Muses. Remember you can manage all Herdr panes.
You're the manager. Drive it." So: split new panes for new jobs (`herdr pane split --pane <id> --direction
right --cwd <repo> --no-focus`), close panes whose agent is done and whose output is saved, reuse an idle
Muse for the next brief instead of starting another, and keep one status file per job. The only pane not to
restart is the user's own interactive one (its profile is theirs). They share the machine: one heavy process
at a time, and the headless graders count against the same memory.

One Monitor watches them all: `watch.py` with no names follows every named Muse in `herdr agent list`
(`--kind` for another agent), so a new pane joins without re-arming it. Igor (2026-09-13): "they're all stuck;
your monitor needs to check for all the stuck agents."

**"◆ keychain item for meta is unreadable (os status -67701)"** after a prompt, with no work following: the
session is dead (its keychain access expired). Igor: "When you see this, the session is dead. You need to
kill Muse with a bunch of Control-Cs and then resume that same conversation. You can see what to resume by
reading the prompt text." So: `herdr agent send-keys <name> ctrl+c` three or four times until the shell
prompt is back, start `muse` again in that pane with its resume option, pick the conversation whose first
prompt text matches the brief you sent, and re-send the last prompt. Do not hand the brief to another Muse
first unless the resume fails: the dead session holds the context of everything it read.

When several Muses commit on branches, the manager merges: host tests on the branch, merge into main, the
simulator or phone rung for what the host cannot see, then close the issue with what was verified where.

**Review every Muse change before it merges; the ladder is not a review.** Igor (2026-09-14, after three
Muse branches merged on green rungs each needed a compile fix, one carried three behaviour bugs a later review
found, and one starved a feature on its first real use): "From now on, you need to code review Muses'
changes. Especially any changes to stories, documents, or architecture. They get a lot shittier." So the
prose is the manager's: write stories, docs, READMEs and architecture notes yourself, and when a Muse must
touch them (a Status line, a scenario for the code it built) rewrite its diff rather than accept it (the
README of 2026-09-14 went back to Fable: "Who wrote that? Use Fable to rewrite it humanized"). When the
status file says committed: (1) `git diff main..<branch> -- docs/ '*.md'` and read every story, doc and
architecture change yourself: a Muse's story edit becomes the spec and its doc edit becomes what the next
agent believes, so fix wording that overstates, drops a scenario, or records a status the rungs have not
earned; (2) run a read-only
reviewer agent over the code (findings with file:line, scenario, fix, and a verdict line, to
`~/tmp/agent/notes/<date>-<topic>-review.md`), fix the real findings yourself or send them back to the Muse;
(3) only then merge and climb the rungs, and say in the issue comment that the review happened. Muses cannot
build the app or a second module, so anything crossing a module boundary (access levels, an `init(from:)`
that clashes with `Decodable`, an implicit `self` in a closure) only shows on your rung: expect one compile
fix per branch and budget for it. Read `git log main..<branch>` before merging: a branch can carry an older
job's commits under the one you asked for.

## What goes wrong

| Symptom                                                                          | Cause                                     | Fix                                                                                                                    |
| -------------------------------------------------------------------------------- | ----------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| Muse's next prompt starts with a stray `y`                                       | an auto-approver typed into its input     | stop the approver, backspace the input, use the watcher + `send-keys`                                                  |
| `agent_prompt_stalled`                                                           | the pane was not at an interactive prompt | `herdr agent get <name>`, read the screen, clear a dialog first                                                        |
| a long answer is not in `agent read`                                             | alternate screen                          | ask for the answer as a file under `/tmp`                                                                              |
| "keychain item for meta is unreadable (os status -67701)" and nothing follows    | the session's keychain access expired     | ctrl+c until the shell is back, `muse resume --last` in that pane, re-send the last prompt (§3)                        |
| the sandbox cannot run `xcodebuild` / the simulator                              | sandbox                                   | the status file names the command; the caller runs it                                                                  |
| "Reviewing approval request (N min · esc to interrupt)" for minutes              | Muse's own approval reviewer, stalled     | send `enter` (it approves); `y` lands in the input line; `esc` cancels the command and the turn, so re-prompt after it |
| `herdr agent prompt` fails with `agent_blocked` on an idle pane                  | a leftover task-selection state           | `herdr pane run <pane> "<prompt>"` types it in raw                                                                     |
| prompts truncated in the watcher's output                                        | three panes side by side                  | one tab per Muse: `herdr pane move <pane> --new-tab --workspace <ws> --no-focus`                                       |
| `herdr agent get` says blocked, the screen says "(ctrl+b to send to background)" | a long command running                    | nothing to answer                                                                                                      |
