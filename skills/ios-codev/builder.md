# Builder runbook

Loaded on demand from [`SKILL.md`](SKILL.md). A builder takes one work lane at a time from the head, builds it on
the rungs its machine can climb, pushes the branch, and reports.

## Taking a lane

1. `bd show <id>` in the target repo: the bead is the brief.
2. Work in a pool slot (`treehouse get --lease -b <branch>`, `treehouse return` once pushed) or a worktree of your
   own, never the repo's main checkout. A pool slot keeps its build folder, so disk stays bounded.
3. Reserve story ids through the repo's ledger if the bead doesn't already carry them.
4. Climb the ladder cheapest rung first, through the repo's recipes and lane wrappers (never a raw `xcodebuild` or
   `swift test`), and stop at the first failure.
5. Push the branch, hand it to the pipeline as the repo says, comment on the bead: what changed, which rung
   verified it, what is left for a rung this machine cannot climb.

## On a Mac

- Build lanes and host slots are per machine (`BUILD_LANES`, `HOST_LANES` override the RAM and core defaults). One
  booted simulator per build lane, left booted between runs; boot only through the repo's boot wrapper.
- Never run a signing build with the screen locked, and never wrap a recipe that takes the signing lock in a second
  lock on it.
- UI tests that open the mic need an unlocked screen; the repo's wrapper skips them on a locked one and names them
  to rerun.
- Watch disk: each simulator boot leaves snapshots behind, and booted simulators past the RAM starve each other.

## On the Linux VM

- Linux Swift through swiftly, Apple sources compiled with the Darwin cross SDK, wasm through a toolchain with a
  wasm SDK. The repo's `test-linux` script header lists the environment it needs.
- xtool can build and install on a device from Linux (no simulator). Use it only when every Mac is busy, and say
  so on the bead: the deploys ledger lives on a Mac and won't record it.
- Before saying the app can't be built here, check `which xtool` and the swiftly toolchains.

## Adding a Mac

A new Mac joins as a builder. Steps marked _unverified_ have not been done for a second Mac yet.

1. **Toolchain.** Xcode, signed into the team's Apple ID (Xcode → Settings → Accounts), and the repo's
   `just setup`. Clone each repo it will build, with the canonical repo as a remote.
2. **First signing build, alone and unlocked.** Run the repo's device build once with nothing else signing. Xcode
   creates this Mac's development certificate and fetches profiles. _Unverified: whether profiles from the first
   Mac then need a refresh there._ See [`ios-device-signing.md`](../../deployment/ios-device-signing.md).
3. **Pair the devices** it will install on, by cable once, so network installs reach them.
4. **Heartbeat.** Mint the machine's token on the hub (`mates.py mint <host> --out FILE`), copy `mate.py` and the
   token to the Mac (owner-only), set `url`, `host` and `repos` in its config, then `mate.py doctor` until every
   line is `ok:`. `mate.py plist` prints the LaunchAgent that keeps `mate.py run` beating. The machine shows on
   the hub as `up`; the head can now dispatch to it.
5. **Pool.** Set up the repo's pool slots, sized to the machine, and create the lane simulators
   (context-grabber: `just native-sim-lanes`).
6. **Smoke.** One lane end to end: host rung, simulator rung, and a device install through the deploy recipe.
