# Device debug loop: logs, in-app reports, pull, file

For any app whose bugs show up only on a device the agent can't see (phone, iPad, watch). Build this on day
one: after that, agents fix bugs from evidence instead of from the user's memory. All three of Igor's iOS
apps adopted it independently (2026-09/10), and almost every device bug since came in through it.

## The pieces

1. **A session log.** A JSON Lines file per launch, one named event per line (`{"t":…, "event":"set_saved", …}`),
   written unbuffered in the app container.
2. **An in-app report.** Shake anywhere, plus a button for when shaking is awkward. It saves a note, a
   screenshot, the screen name, the build, the current log file's name and the moment. Present it from the
   topmost view controller so it still opens over other sheets. Offer "Log it" and "Log it and another"
   (saves, then opens a fresh report).
3. **`just pull-logs`.** Copies logs, reports and crash logs to `~/tmp/agent/<app>-logs/`:

   ```sh
   xcrun devicectl device copy from --device <udid> --domain-type appDataContainer \
     --domain-identifier <bundle-id> --source "Library/Application Support/<App>" --destination <dir>
   xcrun devicectl device copy from --device <udid> --domain-type systemCrashLogs --source / --destination <dir>
   ```

   Also copy any system inbox the app depends on, for example `Documents/Inbox/com.apple.watchconnectivity/`
   (it held a watch app's twelve undelivered transfers).

4. **`just file-bugs`.** Files one GitHub issue per report and skips any report it already filed (keyed on the
   report's timestamp), so it is safe to re-run. Name the screenshot's path rather than uploading it to a
   public repo.

## Rules

- **Instrument before theorizing.** A device-only symptom gets a log event, a deploy and a pull before any fix.
  With no instrumentation there is no fix attempt: add the event and wait for the next report.
- **Log a periodic status line** (every few seconds: inputs, outputs, peaks, counters, route) for anything that
  can go quiet or get stuck. Events logged once miss when the problem started; the status line shows the
  minutes before every report.
- **Log the scene phase and interruptions** (going to the background, camera or audio interruptions). A log that
  stops mid-stream with no crash report means iOS suspended the app, and without these lines nobody can tell
  that apart from a crash.
- **Verify the fix with a log line**, such as `watch_inbox stuck 14 recovered 2`, not by asking the user.
