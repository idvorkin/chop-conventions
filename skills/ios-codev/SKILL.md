---
name: ios-codev
description: How several agent sessions build one iOS app together — one HEAD session per repo that plans, owns story ids and merges, and BUILDER machines (N Macs plus a Linux VM) that take work by free capacity and run Xcode, simulators, signing, device installs, and the Linux/wasm rungs. Use when starting a head or builder session on an iOS repo, adding a Mac to the pool, or making a repo ready to join.
allowed-tools: Bash, Read
---

# iOS co-development: one head, many builders

An iOS repo is worked by one **head** session and any number of **builders**. The head decides what gets built
and in what order; builders do the work their machine is good at. Today the setup is one Mac whose lead session
is head and builder at once, plus a Linux VM builder. More Macs (minis) join as builders; nothing about a repo
changes when they do.

Runbooks, loaded on demand: [`head.md`](head.md) for the head session, [`builder.md`](builder.md) for a builder
session and for adding a Mac. Signing traps are in
[`deployment/ios-device-signing.md`](../../deployment/ios-device-signing.md); where a bug-reporting bug gets fixed
is in the BugKit README ("Where a bug gets fixed").

## Terms

| Term           | Means                                                                                                                                 |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| **work lane**  | one unit of work the head hands out: one bead or issue → one branch → one PR                                                          |
| **build lane** | one xcodebuild + simulator slot on a Mac; default one per 12 GB of RAM, at most four, and `BUILD_LANES` overrides with no cap         |
| **host slot**  | one swift-format / SwiftLint / `swift test` job slot on a machine (a quarter of the cores, at most four)                              |
| **rung**       | one step of the repo's test ladder: lint → host → Mac build → simulator → device; off the Mac, the Linux rung (1L) stands in for host |
| **hub**        | the capacity dashboard every builder heartbeats to (the Cockpit, via `mate.py`)                                                       |
| **train**      | a stack of PRs pushed through the merge pipeline as one run                                                                           |

Work lanes are the head's unit; build lanes and host slots are a builder's. Don't mix them up in a hand-off.

## Roles

| Role              | Does                                                                                                                                                   | Never                                                                                            |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------ |
| **Head** (1/repo) | splits a request into work lanes; dispatches each to a builder by capacity; owns merge order and the train; says what is on which device; triages bugs | runs two signing builds at once; lets two PRs that edit one file merge out of order              |
| **Mac builder**   | Xcode, simulators, the Mac build, signing, device installs (cable and network), the over-the-air package                                               | signs with the screen locked; boots simulators outside its build lanes                           |
| **Linux builder** | Linux Swift tests (rung 1L), every wasm build and test, token-heavy work (docs, web, research), xtool device builds when every Mac is busy             | reports "no Xcode" as a blocker; it pushes the branch and asks a Mac builder for the Apple rungs |

The Mac side never reports "no wasm SDK" as a blocker either: it pushes the branch and asks the Linux builder.

## Where work runs

Read the hub before dispatching. Dispatch only to a machine that is **up** and **admits** work, and prefer the one
with the most free slots. On top of that:

- Anything that needs Xcode, a simulator, signing or a device goes to a Mac.
- wasm and Linux Swift go to the Linux builder.
- Everything else (docs, specs, web pages, research) goes wherever has more room, which is usually the Linux VM.
  Keep Mac slots for the work only a Mac can do.
- **CPU admission.** Aim each machine at 80–90% busy (1-minute average). Start a lane only while it is under 70%;
  pause the lowest-priority lane once it goes over 90%. `BUILD_LANES` and `HOST_LANES` are caps on top of this.
  The hub does not compute this threshold, so the head applies it.
- **Pool slots, not only build lanes.** A lane needs a free pool slot (treehouse) as well as a build lane, and
  slots run out first when lanes wait on review. Dispatch on free slots.

A machine in state `unknown` is asleep or offline, not dead: don't dispatch to it and don't act on it.

Head and builders talk on the bead (the brief and its outcome) and on the head's hub thread (cross-machine asks,
such as "build the wasm for this branch", and build announcements). In this setup that is a Cockpit thread:
`larry post --thread <thread> --id <idempotency-key>` sends, `larry replies` reads, and a `Monitor` on the thread's
stream wakes the session when an answer lands.

## The flows

1. **Plan.** The head writes or finds the spec and the story for each change (spec first, story always) and cuts
   one work lane per bead. Two lanes that edit the same file are ordered, not parallel.
2. **Story ids.** Whoever writes a story reserves its id from the repo's shared ledger (`reserve <journey> [n]`),
   never "the next free id" on their branch. The ledger is a ref on the canonical remote, so any machine can
   reserve. Reserve when the story is written: an unused reservation lapses after a day. A full journey stops the
   lane: say so to the human; nobody adds a range by hand.
3. **Build and verify.** The builder climbs the ladder cheapest rung first, through the repo's lane wrappers, and
   says which rung verified what. Phone-only behaviour (sensors, audio session, widgets, HealthKit) is verified only
   on the device.
4. **Merge.** Every change goes through the no-mistakes pipeline. One run per train; don't push while a run is in
   flight. At `checks-passed`, run the repo's pre-merge check in the branch's own checkout (it refuses
   unless HEAD is the pushed branch): story ids against main as it is now, plus, when the branch touches the core
   package, a Release (`-O`) build of it, because an optimizer crash once showed up only in the archive. Judge it by
   its exit code; never pipe it through `tail`, which hides a FAIL. Then merge and confirm the commits reached the canonical main. Merge a ready PR before anything that touches its files. The
   pipeline is the review: nobody waits for PR comments.
5. **Install.** Every install goes through a recipe that records it in the deploys ledger. A **branch** build goes
   through the device-run recipe (context-grabber: `native-run-device`, ledger `cable`). The all-devices deploy
   (`just deploy`) builds the checkout BugKit's `apps.json` names (main), not the one it runs in, so from a pool
   slot it installs main. After a merge batch, a Mac publishes a development-signed package for over-the-air
   install. Before telling anyone which build a device runs, `pull-logs`, then run the build check: it reads the
   newest **pulled** session log and answers MATCH, MISMATCH (exit 1), NO SESSION YET or UNKNOWN. `just deploy`
   pulls and polls up to 120 s itself. Never answer from memory.
6. **Bugs.** BugKit's listener files shake reports and crashes as GitHub issues. The head finds or writes the
   story, hands the fix out as one work lane, and closes the issue once the build is on the device, saying what was
   verified where. Other work, review leftovers and flaky tests are beads, not issues. A bug in BugKit itself is
   fixed in bug-kit.

## Look things up, don't guess

This is the one copy; each repo's AGENTS.md points here.

- **CLIs, not MCP servers** (Igor, 2026-11-24: "I don't like MCPs"). Every tool below is a command an agent runs
  and reads; none is added with `claude mcp add`.
- **Apple APIs: read [Sosumi](https://sosumi.ai/) first.** Before writing or changing code that uses an Apple API,
  especially iOS 26/27 or anything new, fetch its docs through Sosumi (Apple's developer docs, HIG and WWDC
  transcripts as Markdown; developer.apple.com needs JavaScript, so a plain fetch sees nothing). Swap
  `developer.apple.com` for `sosumi.ai` in the URL and fetch it, e.g.
  `curl -s https://sosumi.ai/documentation/swiftui/view`. Every builder, Mac or Linux, does this.
- **Previews first, Simulator second, both from the CLI. Mac builders only**; the Linux VM has no Simulator or
  Xcode, so a Linux builder hands this work to a Mac builder. Source:
  [Natasha the Robot, "I gave Claude a Mac mini"](https://www.natashatherobot.com/p/claude-mac-mini).
  - Verify UI with a SwiftUI Preview; boot the simulator only for what a preview can't show (screen transitions,
    animations).
  - Every view an agent changes gets a `#Preview` on fixture data (no network, no live server), so it can be
    rendered without the app. Point the healthy previews at an in-process stand-in for the server (a
    `URLProtocol` that answers a fake address from the fixtures), not at "no address": a missing address leaks
    a real error banner into the picture. Add one preview that shows the error state on purpose, so a reviewer
    can tell "looks broken" from "is broken". Keep previews and their fixtures in Debug builds only.
  - Previews without opening Xcode, first choice: Xcode 27.2's own `xcrun mcpbridge`, a stdio JSON-RPC
    bridge driven from a shell script. Nothing is registered as an MCP server in the agent. Its
    `RenderPreview` tool takes a source file and returns a snapshot. One-time setup per Mac, by the human with
    sudo: `sudo xcrun mcp-server allow-folder <dir>` for each checkout and each worktree pool (e.g.
    `~/.treehouse`), then `xcrun mcp-server status` lists the pending caller id to approve. Keep the caller
    stable (Context Grabber always puts `/usr/bin/timeout` in front of the bridge) so one approval covers every
    run. A project under `/private/tmp` is refused outright, so render from a checkout, not a scratch folder.
    Context Grabber wraps it as `just render-preview <file.swift> [out.png] [index]`
    (scripts/native/render-preview.sh, #1451). Measured 2026-11-24 on its six previews (#1465): the first
    render waits for a build lane and a cold build (822 s that night, 35–105 s with a free lane), then about
    1 s each, about 19 s for a file that just changed. Copy that script for another repo; the sudo steps are
    in its docs/TESTING.md.
  - Fallback: [axe](https://github.com/k-kohey/axe)
    (`curl -fsSL https://raw.githubusercontent.com/k-kohey/axe/main/install.sh | sh`, then
    `axe preview report <File>.swift` for PNG, Markdown or HTML; Apple Silicon, needs `idb_companion`, pre-1.0).
    If both fail, run the app in the simulator and take a screenshot.
  - The simulator itself: Apple's own CLIs. `xcodebuild` builds and runs the app and its tests;
    `xcrun simctl` boots, installs, launches and screenshots (`xcrun simctl io booted screenshot out.png`).
  - Write small, targeted tests and run only those, not the full UI suite.
  - One simulator per agent, deleted when it finishes, slimmed with [simslim](https://github.com/MobAI-App/simslim)
    (`brew install mobai-app/tap/simslim`, then `simslim on <udid> --except <what the app's tests need>`).
    It stops daemons and saves memory, not disk. Measured on one iPad simulator (2026-11-24): processes
    180 → 84 and summed RSS 10.2 → 7.0 GB (-31%); disk barely moved, 5.1 → 4.8 GB.
  - A repo's own one-simulator and build-lane rules still apply on top.

## A repo joins: checklist

A repo is ready for head and builders when it has each of these. The table maps them onto the three repos that
practice this today; `—` is a gap, not a decision.

| Capability                                                   | context-grabber (reference)              | exercise-analyzer                      | audio-debugger              |
| ------------------------------------------------------------ | ---------------------------------------- | -------------------------------------- | --------------------------- |
| AGENTS.md naming the ladder, the rules below and the recipes | yes                                      | yes                                    | yes                         |
| host rung, one command                                       | `just test`                              | `just test`                            | `just test`                 |
| Linux rung (portable tests, Apple sources compiled)          | `just test-linux`, `wasm`, `wasm-test`   | `just test-linux`                      | —                           |
| one-command climb of the ladder                              | `just test-ladder`                       | —                                      | —                           |
| simulator rung inside a build lane                           | `native-test-sim` via `build-lane.sh`    | `test-sim` (one sim)                   | `ios-selftest` (build lock) |
| device install recorded in a deploys ledger                  | `just deploy`, `native-run-device`       | `run-device` (no ledger)               | `iphone` + `record-deploy`  |
| which build a device runs                                    | `device-build-check`                     | —                                      | —                           |
| over-the-air package after merges                            | `ota`, `ota-watch`                       | —                                      | —                           |
| logs and reports back from the device                        | `pull-logs`, `bugs-check`                | `pull-logs`, `bugs-check`, `file-bugs` | `session-log`, `logs`       |
| story-id ledger and pre-merge check                          | `story-ids.py reserve`, `just pre-merge` | —                                      | —                           |
| no-mistakes gate, host-only test step                        | yes                                      | yes                                    | yes                         |
| device ids out of the repo (gitignored `*.local` or env)     | yes                                      | —                                      | —                           |
| BugKit linked; reports filed by its listener, not by hand    | listener                                 | `bugs-monitor.sh` + `file-bugs`        | listener (`mac_dir`)        |

The scripts are per-repo, not shared tools: `story-ids.py` names its canonical remote, and the lane scripts know
the repo's schemes. Copy and adapt the reference; extract a shared version when a second repo needs the same one.

## Not settled

These have not run with more than one Mac yet. Decide before the second Mac takes signing or install work.

- **The deploys ledger is a file on one Mac.** A second Mac's installs are invisible to the build check there.
- **One BugKit listener per device.** Which Mac runs it, and how the others see its inbox.
- **One over-the-air watcher per repo.** Which Mac runs it.
- **Where the head sits** once it stops being the Mac's lead session (a Mac, or the Linux VM).
- **Device pairing.** A network install needs the device paired with that Mac; whether every Mac pairs every
  device or only one Mac installs.
- **xtool installs from Linux** are not in any deploys ledger.
- **BugKit's `apps.json` per Mac.** It names the checkout `deploy` builds; each Mac is another copy to keep right,
  beside its own deploys ledger (`~/tmp/agent/notes/lead-times/deploys.jsonl`).
