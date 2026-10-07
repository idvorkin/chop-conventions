# mac CLAUDE.md fragment — macOS laptops

Rules here apply only on macOS. Paths, shells, and toolchain defaults that
encode Apple-specific facts live here.

Loaded via `@~/.claude/claude-md/machine.md`, where the symlink points at
this file when `classify_machine` returns `"mac"`.

## Destructive commands: confirm before running

**Never run destructive commands without confirmation** — `rm -rf`, `git reset --hard`, `DROP TABLE`, force-push, etc. Show the command and ask before running.

## iOS device builds: one signing build at a time

**Never run two `xcodebuild -allowProvisioningUpdates` builds at once, across all agents** (`pgrep -fl xcodebuild` first). A concurrent signing build has emptied Xcode's account list, and only Igor can sign back in (Xcode → Settings → Accounts). With no account, builds still sign from cached profiles unless they need a new device or a new capability. After a sign-in, run the build that needs the portal first and alone. Don't trust the `defaults` account key. Details: `~/gits/chop-conventions/deployment/ios-device-signing.md`.
