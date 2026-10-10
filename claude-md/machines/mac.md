# mac CLAUDE.md fragment — macOS laptops

Rules here apply only on macOS. Paths, shells, and toolchain defaults that
encode Apple-specific facts live here.

Loaded via `@~/.claude/claude-md/machine.md`, where the symlink points at
this file when `classify_machine` returns `"mac"`.

## The unified log: call it as `/usr/bin/log`

In zsh, `log` is a shell builtin. `log show …` runs the builtin and prints nothing, which looks like "nothing was logged". Always write `/usr/bin/log show`. The unified log keeps routine info only about a day and errors a few days, so capture it within the hour of the event.

## Destructive commands: confirm before running

**Never run destructive commands without confirmation** — `rm -rf`, `git reset --hard`, `DROP TABLE`, force-push, etc. Show the command and ask before running.

## iOS device builds: one signing build at a time

**Never run two `xcodebuild -allowProvisioningUpdates` builds at once, across all agents** (check first with `pgrep -fl '^[^ ]*xcodebuild .*-allowProvisioningUpdates'`; a bare `pgrep -f xcodebuild` also matches other agents' shells). A concurrent signing build has emptied Xcode's account list, and only Igor can sign back in (Xcode → Settings → Accounts). With no account, builds still sign from cached profiles unless they need a new device or a new capability. After a sign-in, run the build that needs the portal first and alone. Don't trust the `defaults` account key. Details: `~/gits/chop-conventions/deployment/ios-device-signing.md`.
