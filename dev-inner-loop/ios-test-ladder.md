# iOS test ladder

Verify every change on the cheapest rung that can see it, and **name the rung** in the commit, the issue
comment and the handoff ("verified on the simulator, not on the phone"). An unnamed rung makes the next
session re-verify.

| Rung      | Cost    | What it sees                                         |
| --------- | ------- | ---------------------------------------------------- |
| Host      | ~1 s    | Pure logic in a Swift package, `swift test`          |
| Simulator | minutes | The app running: launch hooks, assertions on the log |
| Device    | a human | Camera, sensors, signing, real iCloud, feel          |

Simulator checks assert on log events (`jq` over the session log, see `device-debug-loop.md`), not on
screenshots or UI automation.

## Simulator hooks instead of tapping

- **Launch hooks.** An env var performs the action N seconds after launch and logs an event:
  `SIMCTL_CHILD_<APP>_<ACTION>=1 xcrun simctl launch …`. simctl strips the prefix, so the app reads
  `<APP>_<ACTION>`. A launch argument can feed media (the simulator
  has no camera).
- **Pre-answer system prompts:** `xcrun simctl privacy <udid> grant photos|microphone|location <bundle-id>`
  (there is no `camera` service, and the simulator has no camera anyway).
- **Pre-answer app prompts:** terminate the app first (`xcrun simctl terminate <udid> <bundle-id>`).
  - For a key the app has **never** written, `xcrun simctl spawn <udid> defaults write <bundle-id> <key> <value>`
    works. It writes the simulator-wide preferences, which the app reads.
  - For a key the app has **already** written, that write is silently ignored, because the app's own
    container plist wins. Edit that plist instead:
    `plutil -replace <key> <type-flag> <value> "$(xcrun simctl get_app_container <udid> <bundle-id> data)/Library/Preferences/<bundle-id>.plist"`,
    where `<type-flag>` is `-bool`, `-date`, `-string` and so on.
  - The check deletes what it set.
- **The look of a screen:** `xcrun simctl io <udid> screenshot out.png`.
- **Services the simulator lacks** (iCloud container, the watch, a server): one env var points the app at a
  plain local stand-in (a folder, a fake id). Two simulators sharing one folder test a two-device feature end
  to end. Keep the stand-in a few lines and mark it as the simulator-only path.
- **Deep links:** open them from an XCUITest (`XCUIApplication().open(url)`). `simctl openurl` stops at an
  "Open in…?" prompt no script can answer.

## Simulator hygiene with several agents on one Mac

- **One named simulator per repo or task** (`xcrun simctl create <repo>-ipad <device-type>`), booted headless
  with `xcrun simctl bootstatus <udid> -b`. Never `open -a Simulator`. Delete throwaway ones when done.
- **Boot explicitly before `xcodebuild test`** when other agents' simulators are up, or the test dies with
  "Timed out trying to boot simulator after 60 s".
- **Erase for first-run state.** A test that depends on first-run state needs
  `xcrun simctl shutdown <udid> && xcrun simctl erase <udid>` (erase refuses a booted simulator). `simctl uninstall`
  fails on a shut-down simulator and leaves the old settings behind.
- **Isolate order-sensitive tests.** A test that hands off to another app (Safari) can break the next test in
  the same run. Run it on its own.
- **Rerun the first run on a fresh simulator** before believing a failure: it can stall ~10 s on cold start.
- **Waiting for the app to exit:** capture `simctl spawn <udid> launchctl list` into a variable, then grep it.
  Piping it to `grep -q` under pipefail ends the wait early (see the `curl | grep -q` rule in `claude-md/global.md`).

## Long builds and tests

The Bash tool caps a foreground command at 10 minutes, and `xcodebuild test` on a busy Mac runs longer. Run it
with `run_in_background`, writing to a log file, and wait for the task notification. If you poll the log
instead, delete or rotate it first: a loop that waits for `TEST SUCCEEDED|FAILED` will match the previous
run's result.
