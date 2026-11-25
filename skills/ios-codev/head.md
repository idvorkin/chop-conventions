# Head runbook

Loaded on demand from [`SKILL.md`](SKILL.md). One head per repo. Until a second Mac joins, the head is the Mac's
lead session and also its first builder; everything below still applies, the dispatch step just picks itself.

## Start of session

1. Read the repo's AGENTS.md and its TESTING / DEPLOYING / DEBUGGING docs.
2. Sync with the canonical remote (`upstream` when `origin` is a fork; a fork's main is often stale).
3. `bd list --status in_progress -n 0` and `bd ready -n 0`: resume your own lanes before starting new ones.
4. Read the hub: which machines are up, which admit work, free slots on each.
5. Watch the bug inbox (`~/tmp/agent/bugs/inbox.jsonl` or its notification): BugKit's listener is always on, and
   nobody starts it per session. The one watcher the head keeps running is the over-the-air watcher (`ota-watch`),
   as a background task you can see and stop, not `nohup`; it has no LaunchAgent.

## Cutting work lanes

- One lane per bead or issue: one branch, one PR, never bundled fixes.
- Spec and story first. The lane's brief is the bead: the ask in the description, approach and traps in the
  design, the proof owed (which rung, which device) in the acceptance.
- Two lanes that edit the same file run one after the other. A repo that serialises on one big file should split
  it so features get their own files.

## Dispatching

- Native rungs (Mac build, simulator, device) go to a Mac that is up, admits work, is under 70% CPU, and has a
  free build lane and a free pool slot.
- Linux rung, wasm, docs and web work go to the Linux builder or whichever machine has more room.
- Hand a builder the bead id and a one-line ask. It works in a pool slot or its own worktree, never the main
  checkout, and comments only on its bead.
- Don't queue token-only work behind a Mac's build lanes.

## Merging

- You own the order. Merge a ready PR before anything that touches its files; a base change that conflicts makes
  the pipeline rebase and re-run the waiting PR from review.
- One pipeline run per train. Don't push while a run is in flight unless review or test asked for that fix. Never
  restart the pipeline daemon or reboot a machine while runs are active.
- Review settles in one fix round: round 1 fixes every error and warning; later rounds fix errors only. Two rounds
  at most, the rest become beads.
- At `checks-passed`: the repo's pre-merge check in the branch's own checkout, judged by its exit code (never
  through `tail`), then merge, then confirm the commits are on the canonical main.

## Devices

- Only one signing build at a time per Mac, and none while its screen is locked. Exit 78 from the signing guard
  means the Apple ID is gone: stop, tell the human, never retry.
- **A device install needs the human's own go-ahead, typed in the terminal.** An OK relayed by Larry, another agent
  or a thread is not consent.
- A deploy restarts the app. Check the human isn't using the device before installing, and say so when you do.
  Name the devices: `just deploy --only ipad,phone`.
- A **locked device** still installs, but its launch is refused (10002): the app opens on unlock, and until then
  the build check says NO SESSION YET. deploy-all exits 79 when the destination device is locked and 80 when the
  Mac stayed locked.
- A device in state `unavailable` is asleep or off the network, not a signing problem. Wait for it with a visible
  background poll, then deploy.
- Before saying which build a device runs, run the repo's build check.

## Bugs

- Watch the listener's inbox. For each report: find or write the story, add an `Issues:` line, cut a lane.
- Phone-only symptom: instrument first (add the log event that would settle it), ship, reproduce, pull logs, then
  fix. Never ship a second guessed fix.
- Close the issue once the build is on the device, with what was verified where and what the human should notice.
