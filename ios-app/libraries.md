# iOS libraries, tools and agent skills

Recommended libraries for Igor's iOS, iPadOS and watchOS apps: SwiftUI apps written mostly by agents, with log-first debugging, host tests before simulators, one simulator at a time, and device deploys through `xcodebuild` and `devicectl`.

Picked by Igor on 2026-10-11 in a Lavish review, with a second opinion from Codex (GPT-6 Astra). Stars are GitHub counts from 2026-10-11. Savings are estimates, not measurements: to measure, take a one-week baseline (time from bug report to evidence, deploy cycles per fix, simulator minutes, signing failures by cause, Igor's minutes).

## Rules

- **No custom controls unless really required.** Use system controls and the HIG first, then SwiftUIX or swiftui-introspect to fill gaps or tweak a system control. Build your own only as a last resort, and say why.
- **Adopt** means use it in every app where it fits. **Consider** means use it when the need is real. **As required** means only when a feature needs it.
- **One per job.** Don't add a second library for a job already covered (for example, Kingfisher for images, twostraws' skill for SwiftUI).
- **Bug reports, crash capture and What's new come from [bug-kit](https://github.com/idvorkin/bug-kit),** not Sentry or Firebase.
- Repos under 1,000 stars are left out. The exception is swift-clocks (★343), which comes with swift-dependencies.

## Logging & debugging

| Verdict  | Repo                                                  |  Stars | Value prop                                                                   | Savings for Igor                                                                                                                              |
| -------- | ----------------------------------------------------- | -----: | ---------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| Adopt    | [apple/swift-log](https://github.com/apple/swift-log) | ★ 4.1k | A logging API; it needs a backend such as OSLog or bug-kit's JSON Lines.     | Live logs only once a backend is set up and tested on the phone. Keep bug-kit's JSONL for crashes and disconnected devices (Astra).           |
| Consider | [kean/Pulse](https://github.com/kean/Pulse)           | ★ 7.2k | Local inspector for logs and URLSession traffic, with swift-log integration. | Only worth it if HTTP failures cost debugging time. It doesn't see CloudKit, HealthKit or AVFoundation. Keep bug-kit JSONL canonical (Astra). |

## Testing

| Verdict             | Repo                                                                                        |   Stars | Value prop                                                          | Savings for Igor                                                                                                                                |
| ------------------- | ------------------------------------------------------------------------------------------- | ------: | ------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| Adopt               | [mobile-dev-inc/Maestro](https://github.com/mobile-dev-inc/Maestro)                         | ★ 16.0k | UI flows in YAML on iOS simulators (not physical iPhones or iPads). | Simpler smoke flows than XCUITest, if stable accessibility IDs are in place. 'Less flaky' is a hypothesis to measure (Astra).                   |
| Adopt               | [pointfreeco/swift-dependencies](https://github.com/pointfreeco/swift-dependencies)         |  ★ 2.2k | Inject clock, Health, files and network so logic runs on the host.  | Moves checks from the simulator rung to the host rung: seconds instead of minutes, and no simulator load spikes.                                |
| Consider (top pick) | [pointfreeco/swift-snapshot-testing](https://github.com/pointfreeco/swift-snapshot-testing) |  ★ 4.4k | Snapshot a view or value; a diff fails the test.                    | Start small: value snapshots on the Mac, plus a few reviewed UI states. Agents must never re-record baselines just to make a test pass (Astra). |
| Consider            | [swiftlang/swift-testing](https://github.com/swiftlang/swift-testing)                       |  ★ 2.2k | @Test and #expect: parameterized, parallel, readable tests.         | Already in use. One fixture table instead of many copy-pasted test functions.                                                                   |

## Code quality

| Verdict | Repo                                                                |   Stars | Value prop                            | Savings for Igor                                                                                                                            |
| ------- | ------------------------------------------------------------------- | ------: | ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| Adopt   | [realm/SwiftLint](https://github.com/realm/SwiftLint)               | ★ 19.8k | Enforce Swift style and catch smells. | Consistent code across agents. Turn on the opt-in force_unwrapping rule explicitly, the kind of bug behind the HeadCalibrate crash (Astra). |
| Adopt   | [swiftlang/swift-format](https://github.com/swiftlang/swift-format) |  ★ 3.0k | Official formatter.                   | Zero formatting debates in reviews; pair it with SwiftLint or pick one.                                                                     |

## Build & release

| Verdict | Repo                                                              |   Stars | Value prop                                                                                             | Savings for Igor                                                                                                                                                                                  |
| ------- | ----------------------------------------------------------------- | ------: | ------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Adopt   | [fastlane/fastlane](https://github.com/fastlane/fastlane)         | ★ 42.2k | Automate signing, builds, TestFlight and screenshots; match keeps certificates and profiles in a repo. | May make signing reproducible when missing or inconsistent signing assets are the cause. It won't fix account login or device-registration failures. Classify a real failed deploy first (Astra). |
| Adopt   | [yonaskolb/XcodeGen](https://github.com/yonaskolb/XcodeGen)       |  ★ 8.8k | Generate .xcodeproj from YAML.                                                                         | Already in use: fewer project-file conflicts, though agents can still collide in the YAML (Astra).                                                                                                |
| Adopt   | [cpisciotta/xcbeautify](https://github.com/cpisciotta/xcbeautify) |  ★ 1.5k | Formats xcodebuild output and can write JUnit reports.                                                 | Agents find the first real build error faster. Keep raw output with tee and pipefail (Astra: adopt as a CLI tool).                                                                                |

## Swift building blocks

| Verdict | Repo                                                                            |  Stars | Value prop                                                                  | Savings for Igor                                                                        |
| ------- | ------------------------------------------------------------------------------- | -----: | --------------------------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| Adopt   | [apple/swift-algorithms](https://github.com/apple/swift-algorithms)             | ★ 6.3k | chunks, windows, uniqued, combinations: sequence algorithms done right.     | Shorter, tested rep and set grouping code in ExerciseCore.                              |
| Adopt   | [apple/swift-collections](https://github.com/apple/swift-collections)           | ★ 4.5k | Deque, OrderedDictionary, Heap, BitSet: tested data structures Swift lacks. | Stops agents hand-rolling ring buffers (replay, breadcrumbs) and their off-by-one bugs. |
| Adopt   | [apple/swift-argument-parser](https://github.com/apple/swift-argument-parser)   | ★ 3.8k | Typed CLIs with help text for free.                                         | Mac tools like posetrack get --help and validation with no hand parsing.                |
| Adopt   | [apple/swift-async-algorithms](https://github.com/apple/swift-async-algorithms) | ★ 3.7k | debounce, throttle, merge, combineLatest for AsyncSequence.                 | Replaces timer-plus-flag code for sensor, location and query streams; fewer race bugs.  |

## Architecture

| Verdict                | Repo                                                                                                      |   Stars | Value prop                                                            | Savings for Igor                                                      |
| ---------------------- | --------------------------------------------------------------------------------------------------------- | ------: | --------------------------------------------------------------------- | --------------------------------------------------------------------- |
| Spike: conversion cost | [pointfreeco/swift-composable-architecture](https://github.com/pointfreeco/swift-composable-architecture) | ★ 15.0k | Opinionated architecture: state, actions, reducers, exhaustive tests. | High upfront cost. Worth it only for a new large app; don't retrofit. |

## Data & storage

| Verdict  | Repo                                                    |  Stars | Value prop                                        | Savings for Igor                                                            |
| -------- | ------------------------------------------------------- | -----: | ------------------------------------------------- | --------------------------------------------------------------------------- |
| Consider | [groue/GRDB.swift](https://github.com/groue/GRDB.swift) | ★ 8.7k | SQLite with migrations, observation and real SQL. | A fallback if SwiftData fights you; replaces JSON-index files with queries. |

## Images

| Verdict | Repo                                                        |   Stars | Value prop                                                                             | Savings for Igor                                                                                                   |
| ------- | ----------------------------------------------------------- | ------: | -------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| Adopt   | [onevcat/Kingfisher](https://github.com/onevcat/Kingfisher) | ★ 24.4k | Image loading, cache and processing, including local files and video-frame thumbnails. | Thumbnails for workout clips and replays, with cache budgets. Astra corrected me: it isn't only for remote images. |

## UI design guidelines and components

Apple's own guidance comes first; it is free and authoritative. The GitHub rows are UI helpers and curated indexes.

| Verdict     | Repo                                                                                               |   Stars | Value prop                                                                                          | Savings for Igor                                                                                                                                                               |
| ----------- | -------------------------------------------------------------------------------------------------- | ------: | --------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Adopt       | [Human Interface Guidelines](https://developer.apple.com/design/human-interface-guidelines)        |   Apple | The rulebook: layout, navigation, typography, controls, per platform (iOS, iPadOS, watchOS, macOS). | Gives agents a citable standard ('follow the HIG for sheets'), so fewer rounds of 'this looks wrong'.                                                                          |
| Adopt       | [SF Symbols](https://developer.apple.com/sf-symbols/)                                              |   Apple | 5,000+ system icons that match the system font, weights and accessibility sizes.                    | Icons like the Kettlebility kettlebell without custom art; free dark mode and Dynamic Type.                                                                                    |
| Adopt       | [Accessibility (HIG)](https://developer.apple.com/design/human-interface-guidelines/accessibility) |   Apple | Dynamic Type, VoiceOver, contrast and 44 pt hit targets.                                            | Explains your iPad 'bigger tap targets' report (#233/#234) as a rule rather than a one-off fix.                                                                                |
| Consider    | [Apple Design Resources](https://developer.apple.com/design/resources/)                            |   Apple | Official Figma and Sketch kits of every system component.                                           | Mock a screen before an agent builds it, and settle the design in one pass.                                                                                                    |
| Consider    | [WWDC design sessions](https://developer.apple.com/videos/design/)                                 |   Apple | Apple's own talks on why the system looks the way it does.                                          | Background reading; skim one per new screen type.                                                                                                                              |
| Adopt       | [SwiftUIX/SwiftUIX](https://github.com/SwiftUIX/SwiftUIX)                                          |  ★ 8.2k | Fills SwiftUI's gaps: missing controls and UIKit bridges.                                           | Fewer hand-built UIViewRepresentable wrappers. Igor's rule: no custom controls unless really required. Use system controls first, then SwiftUIX, and only then build your own. |
| Adopt       | [siteline/swiftui-introspect](https://github.com/siteline/swiftui-introspect)                      |  ★ 6.6k | Reach the UIKit view behind a SwiftUI view.                                                         | A surgical escape hatch for scroll or text field quirks: tweak the system control rather than build a custom one (no custom controls unless really required).                  |
| As required | [airbnb/lottie-ios](https://github.com/airbnb/lottie-ios)                                          | ★ 26.9k | Render After Effects animations natively.                                                           | Polished animations (celebrations, empty states) without custom drawing code. Igor: as required.                                                                               |
| Reference   | [onmyway133/awesome-swiftui](https://github.com/onmyway133/awesome-swiftui)                        |  ★ 1.1k | A maintained index of SwiftUI articles, libraries and examples.                                     | Where to look before writing a custom component.                                                                                                                               |

## Agent skills for iOS/Swift (Claude Code, Codex)

Drop-in SKILL.md packs that teach agents current Swift and Apple APIs. They target the mistakes agents make: deprecated APIs, wrong concurrency, old-style tests.

| Verdict | Repo                                                                                            |  Stars | Value prop                                                                             | Savings for Igor                                                                            |
| ------- | ----------------------------------------------------------------------------------------------- | -----: | -------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| Adopt   | [twostraws/SwiftUI-Agent-Skill](https://github.com/twostraws/SwiftUI-Agent-Skill)               | ★ 5.7k | Paul Hudson's SwiftUI rules for agents: modern APIs, state, navigation, accessibility. | Fewer deprecated-API and bad-state bugs in SwiftUI code agents write; fewer review rounds.  |
| Adopt   | [AvdLee/Swift-Concurrency-Agent-Skill](https://github.com/AvdLee/Swift-Concurrency-Agent-Skill) | ★ 1.7k | Expert Swift 6 concurrency guidance: actors, Sendable, MainActor.                      | Targets bugs like the iPad sync watchdog crash (FileProvider XPC calls on the main thread). |

## Best-practice and style guides

Written conventions to point agents at, or to fold into chop-conventions.

| Verdict   | Repo                                                                          |   Stars | Value prop                                                                | Savings for Igor                                                                    |
| --------- | ----------------------------------------------------------------------------- | ------: | ------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| Consider  | [airbnb/swift](https://github.com/airbnb/swift)                               |  ★ 2.8k | Airbnb's Swift style guide, actively maintained, with swift-format rules. | Enforceable through its SwiftFormat config, so style is checked rather than argued. |
| Reference | [vsouza/awesome-ios](https://github.com/vsouza/awesome-ios)                   | ★ 53.6k | The largest index of iOS libraries and tools.                             | Look here before an agent writes a component from scratch.                          |
| Reference | [futurice/ios-good-practices](https://github.com/futurice/ios-good-practices) | ★ 11.0k | Broad iOS team practices: setup, architecture, testing, release.          | A checklist to compare chop-conventions against. Older (2024), so verify each item. |

## TCA spike (optional)

Budget 4 hours of agent work plus 45 minutes of Igor's review. Take one Context Grabber feature with async loading, debounce, cancellation and a detail sheet, and build it twice from the same baseline: the current Observation approach, and TCA. Record conversion effort, change surface, agent friction, build and test time, and the cost of one follow-up change in each. Go only if every acceptance case passes, the work stays inside the timebox and the feature, Igor can review it within budget, warm edit and test time regresses no more than 20%, and the follow-up change is cheaper. A tie keeps the current design.
