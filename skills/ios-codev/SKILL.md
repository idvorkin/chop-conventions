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

| Term           | Means                                                                                                    |
| -------------- | -------------------------------------------------------------------------------------------------------- |
| **work lane**  | one unit of work the head hands out: one bead or issue → one branch → one PR                             |
| **build lane** | one xcodebuild + simulator slot on a Mac, sized from its RAM (about one per 12 GB, at most four)         |
| **host slot**  | one swift-format / SwiftLint / `swift test` job slot on a machine (a quarter of the cores, at most four) |
| **rung**       | one step of the repo's test ladder: lint → host → Linux → Mac build → simulator → device                 |
| **hub**        | the capacity dashboard every builder heartbeats to (the Cockpit, via `mate.py`)                          |
| **train**      | a stack of PRs pushed through the merge pipeline as one run                                              |

Work lanes are the head's unit; build lanes and host slots are a builder's. Don't mix them up in a hand-off.

## Roles

| Role              | Does                                                                                                                                                                   | Never                                                                                            |
| ----------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| **Head** (1/repo) | splits a request into work lanes; dispatches each to a builder by capacity; owns journey ranges, merge order and the train; says what is on which device; triages bugs | runs two signing builds at once; lets two PRs that edit one file merge out of order              |
| **Mac builder**   | Xcode, simulators, the Mac build, signing, device installs (cable and network), the over-the-air package                                                               | signs with the screen locked; boots simulators outside its build lanes                           |
| **Linux builder** | Linux Swift tests (rung 1L), every wasm build and test, token-heavy work (docs, web, research), xtool device builds when every Mac is busy                             | reports "no Xcode" as a blocker; it pushes the branch and asks a Mac builder for the Apple rungs |

The Mac side never reports "no wasm SDK" as a blocker either: it pushes the branch and asks the Linux builder.

## Where work runs

Read the hub before dispatching. Dispatch only to a machine that is **up** and **admits** work, and prefer the one
with the most free slots. On top of that:

- Anything that needs Xcode, a simulator, signing or a device goes to a Mac.
- wasm and Linux Swift go to the Linux builder.
- Everything else (docs, specs, web pages, research) goes wherever has more room, which is usually the Linux VM.
  Keep Mac slots for the work only a Mac can do.

A machine in state `unknown` is asleep or offline, not dead: don't dispatch to it and don't act on it.

## The flows

1. **Plan.** The head writes or finds the spec and the story for each change (spec first, story always) and cuts
   one work lane per bead. Two lanes that edit the same file are ordered, not parallel.
2. **Story ids.** Whoever writes a story reserves its id from the repo's shared ledger (`reserve <journey> [n]`),
   never "the next free id" on their branch. The ledger is a ref on the canonical remote, so any machine can
   reserve. A full journey stops the lane: the head adds the range.
3. **Build and verify.** The builder climbs the ladder cheapest rung first, through the repo's lane wrappers, and
   says which rung verified what. Phone-only behaviour (sensors, audio session, widgets, HealthKit) is verified only
   on the device.
4. **Merge.** Every change goes through the no-mistakes pipeline. One run per train; don't push while a run is in
   flight. At `checks-passed`, run the repo's pre-merge check (story ids against main as it is now), merge, and
   confirm the commits reached the canonical main. Merge a ready PR before anything that touches its files. The
   pipeline is the review: nobody waits for PR comments.
5. **Install.** Every network or cable install goes through the repo's deploy recipe, so the deploys ledger records
   it. After a merge batch, a Mac publishes a development-signed package for over-the-air install. Before telling
   anyone which build a device runs, run the build check (it reads the device's own session log), never memory.
6. **Bugs.** BugKit's listener files shake reports and crashes as GitHub issues. The head finds or writes the
   story, hands the fix out as one work lane, and closes the issue once the build is on the device, saying what was
   verified where. Other work, review leftovers and flaky tests are beads, not issues. A bug in BugKit itself is
   fixed in bug-kit.

## A repo joins: checklist

A repo is ready for head and builders when it has each of these. The table maps them onto the three repos that
practice this today; `—` is a gap, not a decision.

| Capability                                                   | context-grabber (reference)              | exercise-analyzer                      | audio-debugger              |
| ------------------------------------------------------------ | ---------------------------------------- | -------------------------------------- | --------------------------- |
| AGENTS.md naming the ladder, the rules below and the recipes | yes                                      | yes                                    | yes                         |
| host rung, one command                                       | `just test`                              | `just test`                            | `just test`                 |
| Linux rung (portable tests, Apple sources compiled)          | `just test-linux`, `wasm`, `wasm-test`   | —                                      | —                           |
| one-command climb of the ladder                              | `just test-ladder`                       | —                                      | —                           |
| simulator rung inside a build lane                           | `native-test-sim` via `build-lane.sh`    | `test-sim` (one sim)                   | `ios-selftest` (build lock) |
| device install recorded in a deploys ledger                  | `just deploy`, `native-run-device`       | `run-device`                           | `iphone` + `record-deploy`  |
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
