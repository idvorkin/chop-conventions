# iOS device builds: signing when Xcode loses its account

How agents build, sign and install on Igor's iPhone and iPad from the command line, and what to do when
Xcode's Apple ID session is gone. Collected 2026-10-07 from the exercise-analyzer, context-grabber,
magic-monitor-native and voice-keyboard sessions, which hit (or dodged) this several times that month.

## The recipe every repo uses

Projects come from XcodeGen (`project.yml` with `DEVELOPMENT_TEAM: 45P6D3439T`, `CODE_SIGN_STYLE: Automatic`).
No API key, no fastlane, no manual profiles: the only credential is the Apple ID session the Xcode GUI holds.

```sh
xcodegen generate   # after any project.yml change, or the build silently uses the old project
xcodebuild -project App.xcodeproj -scheme App -derivedDataPath Build/ \
  -destination "platform=iOS,id=<udid>" \
  -allowProvisioningUpdates -allowProvisioningDeviceRegistration build
xcrun devicectl device install app --device <udid> Build/Build/Products/Debug-iphoneos/App.app
xcrun devicectl device process launch --device <udid> <bundle-id>
```

- Run the signing build outside the Claude Code sandbox (`dangerouslyDisableSandbox: true`).
- `devicectl` needs no account. A launch error "device was not, or could not be, unlocked" (10002) means a
  locked device; the install already succeeded.

## Which builds need the account

**No account needed:** the cached profiles in `~/Library/Developer/Xcode/UserData/Provisioning Profiles/`
already cover the device and every entitlement. `-allowProvisioningUpdates` is then a no-op. The wildcard
"iOS Team Provisioning Profile: \*" even covers a brand-new bundle id with no special capabilities.

**Account needed:** anything that changes the developer portal:

- a device not yet in the profile (the first iPad build), or
- a new capability or entitlement (App Groups, iCloud, HealthKit).

Without the account these fail with:

```
error: No Accounts: Add a new account in Accounts settings.
error: Provisioning profile "iOS Team Provisioning Profile: <id>" doesn't include the <X> capability.
```

## Checking whether the account is there

- **Don't trust** `defaults read com.apple.dt.Xcode DVTDeveloperAccountManagerAppleIDLists`. It has been
  populated while builds failed with No Accounts, and empty while builds signed and registered devices.
- **Don't** go looking in the keychain. There is no Xcode token item to find, and the permission classifier
  refuses `security` lookups anyway.
- **The only reliable check** is a build that needs the portal, with its output grepped for `No Accounts`.

## Fixing it

There is no CLI sign-in. Ask Igor to sign in at **Xcode → Settings → Accounts**, then right away run the
build that needs the portal, **first and alone**. It registers the device and adds the capability, and
Xcode caches the new profile. From then on, builds that need no new portal changes keep working even if the
account drops again.

Don't strip entitlements out of `project.yml` to get a build through. It has been done (App Groups,
restored with `git checkout`), but the app ships without the capability (the widget showed only its
placeholder), and the iPad still fails because it isn't in any cached profile.

## What drops the account: concurrent signing builds

**Never run two `-allowProvisioningUpdates` builds at the same time, across all agents on the Mac.**

On 2026-10-07, one agent's signing build ran from 06:35 to 06:40 while another agent's build that needed
the portal started at 06:40:37. The second build failed with No Accounts. At 06:41:18 Xcode's prefs were
rewritten with an empty account list, which had held the account at 06:09.

Before a signing build:

```sh
pgrep -fl '^[^ ]*xcodebuild .*-allowProvisioningUpdates'   # a signing build is running: wait
herdr workspace list    # which agents are active
```

Anchor the pattern on the binary. A bare `pgrep -f xcodebuild` also matches other agents' `zsh -c` wrappers,
heredocs and wait loops that merely mention the word. It reported 30 "builds" when there were 0–2, and it
held a build for 20 minutes.

An install via `devicectl` that ends with CoreDeviceError 10002 ("device was not, or could not be, unlocked")
succeeded. The device was locked, so only the launch failed. Report "installed, launches when unlocked" and
don't rebuild.

ponytail: the guard is a manual check, so two agents can still race. If this keeps happening, wrap
`xcodebuild` in every repo's device recipe with a shared lock (`fcntl.flock` on
`~/.cache/xcode-signing.lock`) so signing builds queue instead of colliding.
