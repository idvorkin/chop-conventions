# GitHub Integration Spec

A reusable specification for integrating GitHub features into PWAs: displaying the source repo link and enabling users to file bugs directly from the app.

---

## Reference Implementation

This spec is implemented in [magic-monitor](https://github.com/idvorkin/magic-monitor):

| Component            | Latest                                                                                              | Permalink                                                                                               |
| -------------------- | --------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| useShakeDetector     | [latest](https://github.com/idvorkin/magic-monitor/blob/main/src/hooks/useShakeDetector.ts)         | [e4dcb70](https://github.com/idvorkin/magic-monitor/blob/e4dcb70/src/hooks/useShakeDetector.ts)         |
| useBugReporter       | [latest](https://github.com/idvorkin/magic-monitor/blob/main/src/hooks/useBugReporter.ts)           | [e4dcb70](https://github.com/idvorkin/magic-monitor/blob/e4dcb70/src/hooks/useBugReporter.ts)           |
| BugReportModal       | [latest](https://github.com/idvorkin/magic-monitor/blob/main/src/components/BugReportModal.tsx)     | [e4dcb70](https://github.com/idvorkin/magic-monitor/blob/e4dcb70/src/components/BugReportModal.tsx)     |
| SettingsModal        | [latest](https://github.com/idvorkin/magic-monitor/blob/main/src/components/SettingsModal.tsx)      | [e4dcb70](https://github.com/idvorkin/magic-monitor/blob/e4dcb70/src/components/SettingsModal.tsx)      |
| DeviceService        | [latest](https://github.com/idvorkin/magic-monitor/blob/main/src/services/DeviceService.ts)         | [e4dcb70](https://github.com/idvorkin/magic-monitor/blob/e4dcb70/src/services/DeviceService.ts)         |
| shakeDetection       | [latest](https://github.com/idvorkin/magic-monitor/blob/main/src/utils/shakeDetection.ts)           | [e4dcb70](https://github.com/idvorkin/magic-monitor/blob/e4dcb70/src/utils/shakeDetection.ts)           |
| bugReportFormatters  | [latest](https://github.com/idvorkin/magic-monitor/blob/main/src/utils/bugReportFormatters.ts)      | [e4dcb70](https://github.com/idvorkin/magic-monitor/blob/e4dcb70/src/utils/bugReportFormatters.ts)      |
| bugReport types      | [latest](https://github.com/idvorkin/magic-monitor/blob/main/src/types/bugReport.ts)                | [e4dcb70](https://github.com/idvorkin/magic-monitor/blob/e4dcb70/src/types/bugReport.ts)                |
| shakeDetection tests | [latest](https://github.com/idvorkin/magic-monitor/blob/main/src/utils/shakeDetection.test.ts)      | [e4dcb70](https://github.com/idvorkin/magic-monitor/blob/e4dcb70/src/utils/shakeDetection.test.ts)      |
| bugFormatters tests  | [latest](https://github.com/idvorkin/magic-monitor/blob/main/src/utils/bugReportFormatters.test.ts) | [e4dcb70](https://github.com/idvorkin/magic-monitor/blob/e4dcb70/src/utils/bugReportFormatters.test.ts) |
| CrashFallback        | [latest](https://github.com/idvorkin/magic-monitor/blob/main/src/components/CrashFallback.tsx)      | [PR #17](https://github.com/idvorkin/magic-monitor/pull/17)                                             |

---

## Overview

### Purpose

1. **GitHub Repo Link** — Let users view the source code, star the repo, or explore issues
2. **Bug Reporting** — Allow users to file GitHub issues directly from the app with optional screenshots

### Scope

This spec covers:

- Auto-detecting the GitHub repository from project config
- Displaying a "View Source" link
- Shake-to-report, keyboard shortcuts, and button triggers
- Screenshot capture and issue creation via GitHub's web UI

---

## Configuration

### Auto-detecting GitHub Repo

The app should automatically detect the GitHub repository URL using this priority:

1. **Environment variable**: `GITHUB_REPO_URL` (highest priority, for overrides)
2. **package.json**: Read the `repository` field
   ```json
   {
     "repository": {
       "type": "git",
       "url": "https://github.com/owner/repo"
     }
   }
   ```
3. **Git remote**: Parse `git remote get-url origin` at build time

### Customization Points

Each project adopting this spec can configure:

| Setting             | Required | Description                                             |
| ------------------- | -------- | ------------------------------------------------------- |
| `GITHUB_REPO_URL`   | No       | Override auto-detected repo URL                         |
| `BUG_REPORT_LABELS` | No       | Default labels for issues (e.g., `["bug", "from-app"]`) |
| `SHAKE_THRESHOLD`   | No       | Acceleration magnitude threshold (default: 25)          |
| `SHAKE_COOLDOWN_MS` | No       | Cooldown between shake detections (default: 2000ms)     |

---

## Build-Time Version Generation

### Purpose

Generate Git metadata at build time for displaying in the About dialog and including in bug reports. This provides the **exact deployed version**, not the latest commit from the API.

### Generated Version File

Add this to your build/deploy script (before bundling):

```bash
# Git metadata
SHA=$(git rev-parse HEAD)
BRANCH=$(git rev-parse --abbrev-ref HEAD)

# Derive repo URL from git remote
REPO_URL=$(git remote get-url origin | sed 's/\.git$//' | sed 's|git@github.com:|https://github.com/|')
COMMIT_URL="$REPO_URL/commit/$SHA"
CURRENT_URL="$REPO_URL/tree/$BRANCH"

# Timestamp
BUILD_TIME=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Output file (must be in .gitignore)
cat > src/generated_version.ts <<EOF
export const GIT_SHA = "$SHA";
export const GIT_COMMIT_URL = "$COMMIT_URL";
export const GIT_CURRENT_URL = "$CURRENT_URL";
export const GIT_BRANCH = "$BRANCH";
export const BUILD_TIMESTAMP = "$BUILD_TIME";
EOF
```

**Important:** Add `src/generated_version.ts` to `.gitignore`. It must not be committed but must be bundled into the build output.

### About Dialog

Display build info in an About dialog or settings page:

```tsx
import {
  GIT_SHA,
  GIT_COMMIT_URL,
  GIT_CURRENT_URL,
  GIT_BRANCH,
  BUILD_TIMESTAMP,
} from "@/generated_version";

export function AboutInfo() {
  return (
    <div>
      <p>
        Build:{" "}
        <a href={GIT_COMMIT_URL} target="_blank" rel="noopener noreferrer">
          {GIT_SHA.slice(0, 7)}
        </a>{" "}
        on{" "}
        <a href={GIT_CURRENT_URL} target="_blank" rel="noopener noreferrer">
          {GIT_BRANCH}
        </a>
      </p>
      <p>Built: {BUILD_TIMESTAMP}</p>
    </div>
  );
}
```

### Bug Reporter Integration

Use the generated version instead of fetching from GitHub API:

```ts
import { GIT_SHA, GIT_COMMIT_URL, GIT_BRANCH } from "@/generated_version";

export function buildDefaultDescription(): string {
  const dateStr = formatDate(new Date());
  return `**Date:** ${dateStr}
**Build:** [${GIT_SHA.slice(0, 7)}](${GIT_COMMIT_URL}) on ${GIT_BRANCH}

**What were you trying to do?**

**What happened instead?**

**Steps to reproduce:**
1. `;
}
```

**Benefits:** Works offline, no API rate limits, shows exact deployed version.

---

## Feature 1: GitHub Repo Link

### Purpose

Let users:

- View the source code
- Browse existing issues or discussions
- Understand the app is open source

### UX Placement

**Default: Settings/About page** (recommended)

Other options:
| Location | When to Use |
|----------|-------------|
| **Settings/About page** | Default - detailed info with repo stats |
| **Footer** | Always visible, good for minimal "View Source" |
| **Help menu** | Alongside bug report option |

### Settings Page Preview

```
┌─────────────────────────────────────────┐
│ Settings                                │
├─────────────────────────────────────────┤
│                                         │
│ APPEARANCE                              │
│ ┌─────────────────────────────────────┐ │
│ │ Theme                        Light ▼│ │
│ │ Font Size                    Medium ▼│ │
│ └─────────────────────────────────────┘ │
│                                         │
│ NOTIFICATIONS                           │
│ ┌─────────────────────────────────────┐ │
│ │ Push Notifications              [✓] │ │
│ │ Email Digest                    [ ] │ │
│ └─────────────────────────────────────┘ │
│                                         │
│ BUG REPORTING                           │
│ ┌─────────────────────────────────────┐ │
│ │ Shake to Report Bug             [✓] │ │
│ │ Keyboard Shortcut (Ctrl+I)      [✓] │ │
│ └─────────────────────────────────────┘ │
│                                         │
│ ABOUT                                   │
│ ┌─────────────────────────────────────┐ │
│ │ Version                       1.2.3 │ │
│ │                                     │ │
│ │ ┌─────────────────────────────────┐ │ │
│ │ │  ◉  View on GitHub              │ │ │
│ │ │     github.com/owner/repo       │ │ │
│ │ └─────────────────────────────────┘ │ │
│ │                                     │ │
│ │ [Report a Bug]                      │ │
│ └─────────────────────────────────────┘ │
│                                         │
└─────────────────────────────────────────┘
```

### Visual Options

**Minimal (for footer):**

```
<GitHub icon> View Source
```

**Card style (for settings, recommended):**

```
┌─────────────────────────────────────┐
│  ◉  View on GitHub                  │
│     github.com/owner/repo           │
└─────────────────────────────────────┘
```

### Implementation

```ts
// Build-time: inject repo URL into app config
const GITHUB_REPO_URL =
  process.env.GITHUB_REPO_URL ||
  packageJson.repository?.url ||
  getGitRemoteOrigin();

// Runtime: parse for deep links
function getGitHubLinks(repoUrl: string) {
  const base = repoUrl.replace(/\.git$/, "");
  return {
    repo: base,
    issues: `${base}/issues`,
    newIssue: `${base}/issues/new`,
    discussions: `${base}/discussions`,
    stars: `${base}/stargazers`,
  };
}
```

---

## Feature 2: Bug Reporting

### Approach: GitHub Web UI (No OAuth Required)

The simplest approach is to prepare the bug report in-app, then open GitHub's "New Issue" page with pre-filled content. The user logs into GitHub via their browser and submits directly.

**Why this approach?**

- No OAuth setup required (no client ID, secrets, or backend proxy)
- No token storage or security concerns
- User authenticates via GitHub's own UI
- Works immediately with any public repo

**Flow:**

1. User triggers bug report (shake, keyboard, or button)
2. App shows modal to compose bug details
3. User clicks "Submit" → app copies text to clipboard + opens GitHub new issue URL
4. User pastes/reviews in GitHub and submits

---

### Triggers for Bug Report

| Trigger               | Platform             | Activation                            |
| --------------------- | -------------------- | ------------------------------------- |
| **Device Shake**      | Mobile (iOS/Android) | Shake device when enabled in settings |
| **Keyboard Shortcut** | Desktop              | `Ctrl+I` (or `Cmd+I` on Mac)          |
| **Button**            | All                  | Explicit button in settings/help menu |

#### Shake Detection

**Scope:** Mobile devices only (browser or installed PWA)

**Behavior:**

- Listen to `DeviceMotion` events
- Prefer `event.acceleration` (without gravity) over `accelerationIncludingGravity` for cleaner detection
- Trigger when acceleration magnitude exceeds threshold (~25 m/s²)
- Apply cooldown (2 seconds) to prevent multiple triggers
- Only active when user has enabled "Shake to report bug" in settings

**Precondition:** User must opt-in via Settings

---

### UX Flow

#### Step 1: First-Time Shake Prompt (Mobile Only)

When user opens bug report modal for the first time on a device with motion sensors, offer to enable shake detection:

```
┌─────────────────────────────────────────┐
│ Enable Shake to Report?                 │
│                                         │
│ Shake your device anytime to quickly    │
│ report a bug. This uses your device's   │
│ motion sensors to detect shaking.       │
│                                         │
│ [Enable]  [Not Now]                     │
└─────────────────────────────────────────┘
```

#### Step 2: Bug Report Form

```
┌─────────────────────────────────────────┐
│ Report a Bug                    [X]     │
├─────────────────────────────────────────┤
│ Title *                                 │
│ ┌─────────────────────────────────────┐ │
│ │ Bug                                 │ │
│ └─────────────────────────────────────┘ │
│                                         │
│ Description *                           │
│ ┌─────────────────────────────────────┐ │
│ │ **Date:** Nov 29, 2025              │ │
│ │ **Latest version:** [abc1234](url)  │ │
│ │                                     │ │
│ │ **What were you trying to do?**     │ │
│ │                                     │ │
│ │ **What happened instead?**          │ │
│ │                                     │ │
│ │ **Steps to reproduce:**             │ │
│ │ 1.                                  │ │
│ └─────────────────────────────────────┘ │
│                                         │
│ Screenshot                  [Capture]   │
│ (Browser will ask which tab to share)   │
│                                         │
│ ☑ Include technical details             │
│   (route, app version, browser)         │
│                                         │
│ [Cancel]        [Copy & Open GitHub]    │
└─────────────────────────────────────────┘
```

**Pre-filled values:**

- Title: `Bug` (simple, user can edit)
- Description template includes:
  - Current date
  - Latest commit SHA + message (fetched from GitHub API)
  - Prompts for what happened

#### Step 3: Screenshot Capture

**Platform-specific behavior:**

**Desktop:**

1. User clicks "Capture" button
2. Modal closes temporarily (so it doesn't appear in screenshot)
3. Browser prompts for screen/tab selection via `getDisplayMedia`
4. Capture single frame to canvas
5. Stop video track immediately
6. Reopen modal with form state preserved, show screenshot preview

```ts
// Close modal first to avoid capturing it
onClose();
await new Promise((resolve) => setTimeout(resolve, 150));

const stream = await navigator.mediaDevices.getDisplayMedia({
  video: { displaySurface: "browser" },
});

// Capture frame
const video = document.createElement("video");
video.srcObject = stream;
await video.play();

const canvas = document.createElement("canvas");
canvas.width = video.videoWidth;
canvas.height = video.videoHeight;
canvas.getContext("2d").drawImage(video, 0, 0);

for (const track of stream.getTracks()) {
  track.stop();
}

// Reopen modal with screenshot
onOpen();
```

If user cancels or permission denied, proceed without screenshot.

**Mobile:**

There's no web API to save images directly to the photo album from a PWA. The `getDisplayMedia` API has limited support on mobile, and clipboard image copy often fails.

**Recommended approach for mobile:** Don't offer in-app screenshot capture. Instead, show a message:

> "Take a screenshot on your device, then attach it to the GitHub issue after submitting."

This is simpler and more reliable than attempting download workarounds that may not save to Photos.

#### Step 4: Submission

**On submit:**

1. Build issue body with description + metadata
2. Copy full text to clipboard (backup)
3. Open GitHub new issue URL with pre-filled params:
   ```
   https://github.com/owner/repo/issues/new?title=Bug&body=...&labels=bug,from-app
   ```
4. Show success message

**Success state:**

```
✓ Bug report copied & GitHub opened!
Paste the bug details if they weren't pre-filled.
[Close]
```

---

### Technical Design

#### Data Contract

```ts
interface BugReportData {
  title: string;
  description: string;
  includeMetadata: boolean;
  screenshot?: string; // base64 data URL (for preview only)
}

interface BugReportMetadata {
  route: string;
  userAgent: string;
  timestamp: string; // ISO 8601
  appVersion: string;
}

interface LatestCommit {
  sha: string;
  message: string;
  url: string;
}
```

#### Fetching Latest Commit

Fetch the latest commit on mount to include in bug reports:

```ts
async function fetchLatestCommit(
  repoUrl: string,
): Promise<LatestCommit | null> {
  const match = repoUrl.match(/github\.com\/([^/]+)\/([^/]+)/);
  if (!match) return null;
  const [, owner, repo] = match;

  const response = await fetch(
    `https://api.github.com/repos/${owner}/${repo}/commits?per_page=1`,
    { headers: { Accept: "application/vnd.github.v3+json" } },
  );
  if (!response.ok) return null;

  const commits = await response.json();
  if (!commits.length) return null;

  return {
    sha: commits[0].sha.substring(0, 7),
    message: commits[0].commit.message.split("\n")[0],
    url: commits[0].html_url,
  };
}
```

#### Opening GitHub New Issue URL

```ts
async function submitBugReport(
  data: BugReportData,
  metadata: BugReportMetadata,
) {
  const body = buildIssueBody(data, metadata);

  // Build URL with pre-filled params
  const issueUrl = new URL(`${GITHUB_REPO_URL}/issues/new`);
  issueUrl.searchParams.set("title", data.title);
  issueUrl.searchParams.set("body", body);
  issueUrl.searchParams.set("labels", "bug,from-app");

  // Copy to clipboard as backup
  await navigator.clipboard.writeText(`Title: ${data.title}\n\n${body}`);

  // Open in new tab
  window.open(issueUrl.toString(), "_blank", "noopener,noreferrer");
}
```

#### Issue Body Template

```md
**Date:** Nov 29, 2025

**Latest version:** [abc1234](https://github.com/.../commit/abc1234) - Commit message

**What were you trying to do?**

{user input}

**What happened instead?**

{user input}

**Steps to reproduce:**

1. {user input}

---

**App Metadata**
| Field | Value |
| ----------- | ---------------------- |
| Route | `/current-path` |
| App Version | `1.0.0` |
| Browser | `Mozilla/5.0...` |
| Timestamp | `2025-11-29T18:00:00Z` |

**Screenshot**
_(Screenshot captured - please upload manually to GitHub)_
```

#### Screenshot Handling

**Desktop only** - screenshots are captured for preview in the modal. On submit:

1. If screenshot exists, copy the **image** to clipboard (not text)
2. Open GitHub with pre-filled title/body
3. Show success message prompting user to paste: "Screenshot is on your clipboard! Paste with Ctrl+V / Cmd+V"

```ts
async function copyImageToClipboard(dataUrl: string): Promise<boolean> {
  try {
    const response = await fetch(dataUrl);
    const blob = await response.blob();
    await navigator.clipboard.write([new ClipboardItem({ [blob.type]: blob })]);
    return true;
  } catch {
    return false;
  }
}

// On submit (desktop only):
if (data.screenshot && !isMobileDevice()) {
  hasScreenshotOnClipboard = await copyImageToClipboard(data.screenshot);
}
if (!hasScreenshotOnClipboard) {
  // Fallback: copy text if no screenshot or on mobile
  await navigator.clipboard.writeText(issueText);
}
```

The issue body should include a reminder (desktop only):

```md
**Screenshot**
_(Screenshot is on your clipboard - paste it here with Ctrl+V / Cmd+V)_
```

**Mobile:** Don't include screenshot section in issue body since user will attach manually.

---

### Edge Cases

| Scenario                        | Handling                                                       |
| ------------------------------- | -------------------------------------------------------------- |
| **Offline**                     | Still works - user can copy text, open GitHub when back online |
| **Multiple shakes**             | Debounce via cooldown; ignore while modal is open              |
| **getDisplayMedia unavailable** | Hide capture button or proceed without screenshot              |
| **GitHub API rate limited**     | Fall back to repo URL without commit info                      |
| **URL too long**                | Clipboard always has full text as backup                       |
| **iOS DeviceMotion permission** | Request permission explicitly before enabling shake            |

---

## Framework-Specific: React

### Hooks

#### useShakeDetector

```tsx
function useShakeDetector(options: {
  enabled: boolean;
  threshold?: number; // Default: 25
  cooldownMs?: number; // Default: 2000
  onShake: () => void;
}) {
  const lastShakeRef = useRef<number>(0);

  const handleMotion = useCallback(
    (event: DeviceMotionEvent) => {
      // Prefer acceleration (without gravity) for cleaner shake detection
      const accel = event.acceleration ?? event.accelerationIncludingGravity;
      const { x, y, z } = accel || {};
      if (x == null || y == null || z == null) return;

      const magnitude = Math.sqrt(x * x + y * y + z * z);

      // When using acceleration (preferred), magnitude at rest is ~0
      // A good shake produces magnitudes of 20-40+ m/s²
      if (magnitude > (options.threshold || 25)) {
        const now = Date.now();
        if (now - lastShakeRef.current > (options.cooldownMs || 2000)) {
          lastShakeRef.current = now;
          options.onShake();
        }
      }
    },
    [options.threshold, options.cooldownMs, options.onShake],
  );

  useEffect(() => {
    if (!options.enabled) return;
    window.addEventListener("devicemotion", handleMotion);
    return () => window.removeEventListener("devicemotion", handleMotion);
  }, [options.enabled, handleMotion]);

  return { isSupported: "DeviceMotionEvent" in window };
}
```

#### useBugReporter

```tsx
function useBugReporter() {
  const [isOpen, setIsOpen] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [latestCommit, setLatestCommit] = useState<LatestCommit | null>(null);
  const [shakeEnabled, setShakeEnabled] = useState(
    () => localStorage.getItem("shake-enabled") === "true",
  );

  // Fetch latest commit on mount
  useEffect(() => {
    fetchLatestCommit(GITHUB_REPO_URL).then(setLatestCommit);
  }, []);

  const open = useCallback(() => setIsOpen(true), []);
  const close = useCallback(() => setIsOpen(false), []);

  const getDefaultData = useCallback(
    () => ({
      title: "Bug",
      description: buildDefaultDescription(latestCommit),
      includeMetadata: true,
    }),
    [latestCommit],
  );

  const submit = useCallback(async (data: BugReportData) => {
    setIsSubmitting(true);
    try {
      await submitBugReport(data, getMetadata());
      return { success: true };
    } finally {
      setIsSubmitting(false);
    }
  }, []);

  return {
    isOpen,
    open,
    close,
    submit,
    isSubmitting,
    getDefaultData,
    shakeEnabled,
    setShakeEnabled,
    githubRepoUrl: GITHUB_REPO_URL,
  };
}
```

### Components

```tsx
// GitHubLink - Display repo link in settings
<a href={githubRepoUrl} target="_blank" rel="noopener noreferrer">
  <GithubIcon /> View on GitHub
</a>

// BugReportModal - Full bug reporting flow
<BugReportModal
  isOpen={bugReporter.isOpen}
  onClose={bugReporter.close}
  onOpen={bugReporter.open}  // For reopening after screenshot
  onSubmit={bugReporter.submit}
  defaultData={bugReporter.getDefaultData()}
  shakeEnabled={bugReporter.shakeEnabled}
  onShakeEnabledChange={bugReporter.setShakeEnabled}
/>

// Mobile detection for conditional screenshot UI
function isMobileDevice(): boolean {
  return /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(
    navigator.userAgent
  );
}

// In BugReportModal, show different screenshot section:
{isMobileDevice() ? (
  <p>Take a screenshot on your device, then attach it to the GitHub issue after submitting.</p>
) : (
  <button onClick={handleCaptureScreenshot}>Capture Screenshot</button>
)}
```

### Keyboard Shortcut

```tsx
// Detect platform for shortcut display
const isMac = navigator.platform.toUpperCase().indexOf("MAC") >= 0;
const bugReportShortcut = isMac ? "⌘I" : "Ctrl+I";

// In main component
useEffect(() => {
  const handleKeyDown = (e: KeyboardEvent) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "i") {
      e.preventDefault();
      bugReporter.open();
    }
  };
  window.addEventListener("keydown", handleKeyDown);
  return () => window.removeEventListener("keydown", handleKeyDown);
}, [bugReporter]);

// Pass shortcut to components for display
<BugReportModal shortcut={bugReportShortcut} ... />
<SettingsModal bugReportShortcut={bugReportShortcut} ... />
```

---

## Feature 3: Error Boundary with Crash Reporting

### Purpose

When runtime errors occur, users see a recovery interface instead of a blank error screen. This:

- Prevents the app from appearing broken/frozen
- Gives users a way to recover (reload)
- Enables automatic crash reporting to GitHub with error details

### Implementation

**Dependencies:**

```bash
npm install react-error-boundary
```

**Wrap your app:**

```tsx
import { ErrorBoundary } from "react-error-boundary";
import { CrashFallback } from "./components/CrashFallback";

function App() {
  return (
    <ErrorBoundary FallbackComponent={CrashFallback}>
      {/* Your app content */}
    </ErrorBoundary>
  );
}
```

### CrashFallback Component

```tsx
import {
  buildCrashReportBody,
  buildGitHubIssueUrl,
  formatBuildLink,
  getMetadata,
} from "../utils/bugReportFormatters";
import { GIT_COMMIT_URL, GIT_SHA_SHORT } from "../generated_version";

export function CrashFallback({ error }: { error: Error }) {
  const metadata = getMetadata(
    () => window.location.pathname,
    () => navigator.userAgent,
  );
  const reportUrl = buildGitHubIssueUrl(
    GITHUB_REPO_URL,
    `Crash: ${error.message.slice(0, 50)}`,
    buildCrashReportBody(error, metadata),
    ["bug", "crash"],
  );

  return (
    <div className="error-container">
      <h1>Something went wrong</h1>
      <p>{error.message}</p>
      {error.stack && <pre className="stack-trace">{error.stack}</pre>}
      <div className="actions">
        <button onClick={() => window.location.reload()}>Reload Page</button>
        <a href={reportUrl} target="_blank" rel="noopener noreferrer">
          Report on GitHub
        </a>
      </div>
      <p className="build-info">
        Build: <a href={GIT_COMMIT_URL}>{GIT_SHA_SHORT}</a>
      </p>
    </div>
  );
}
```

### Crash Report Body Builder

Add this to `bugReportFormatters.ts`:

```ts
export function buildCrashReportBody(
  error: Error,
  metadata: BugReportMetadata,
): string {
  return `**Error:** ${error.message}

**Build:** ${formatBuildLink()}

**Stack Trace:**
\`\`\`
${error.stack || "No stack trace available"}
\`\`\`

---

**App Metadata**
| Field | Value |
|-------|-------|
| Route | \`${metadata.route}\` |
| App Version | ${formatBuildLink()} |
| Browser | \`${metadata.userAgent}\` |
| Timestamp | \`${metadata.timestamp}\` |
`;
}
```

### UX Mockup

```
┌─────────────────────────────────────────┐
│                                         │
│         Something went wrong            │
│                                         │
│  Cannot read property 'foo' of null     │
│                                         │
│  ┌─────────────────────────────────────┐│
│  │ TypeError: Cannot read property...  ││
│  │     at Component (App.tsx:42)       ││
│  │     at renderWithHooks (react...)   ││
│  └─────────────────────────────────────┘│
│                                         │
│     [Reload Page]  [Report on GitHub]   │
│                                         │
│         Build: abc1234                  │
│                                         │
└─────────────────────────────────────────┘
```

---

## Feature 4: Session Debug Log (Flight Recorder)

### Purpose

Capture a rolling log of user actions, console output, errors, and environment info in IndexedDB. When users encounter problems (including crashes), they can download this log and attach it to bug reports—providing the context developers need to reproduce issues.

**Key design principles:**

- Always active (no opt-in required for basic logging)
- Auto-persists to IndexedDB for crash recovery
- Keeps multiple sessions for post-crash debugging
- Captures rich environment info for bug reports

### Reference Implementation

See [swing-analyzer SessionRecorder.ts](https://github.com/idvorkin/swing-analyzer) for a production implementation.

### What to Capture

| Event Type           | Data Captured                                                                 |
| -------------------- | ----------------------------------------------------------------------------- |
| **User Actions**     | Click target (selector + text preview), keyboard shortcuts, navigation        |
| **Console Output**   | `console.log`, `console.warn`, `console.error` with arguments                 |
| **Errors**           | Uncaught exceptions, unhandled promise rejections with stack traces           |
| **State Changes**    | App-specific events (e.g., `extraction_start`, `playback_stop`, `cache_load`) |
| **Memory Snapshots** | JS heap usage over time (Chrome only)                                         |
| **Environment**      | Browser, OS, screen, WebGL, video codecs, hardware info                       |

### Data Types

```ts
interface InteractionEvent {
  type: "click" | "keydown" | "keyup";
  timestamp: number;
  target: string; // CSS selector or element description
  details?: Record<string, unknown>;
}

interface StateChangeEvent {
  type: string; // App-specific: 'video_load', 'sync_complete', 'error', etc.
  timestamp: number;
  details?: Record<string, unknown>;
}

interface MemorySnapshot {
  timestamp: number;
  usedJSHeapSize?: number;
  totalJSHeapSize?: number;
  jsHeapSizeLimit?: number;
  usedMB?: number;
  percentUsed?: number;
}

interface EnvironmentInfo {
  // Build info
  buildVersion?: string;
  buildCommit?: string;
  buildTime?: string;

  // Browser/OS
  userAgent: string;
  platform: string;
  language: string;
  cookiesEnabled: boolean;
  onLine: boolean;

  // Display
  screenWidth: number;
  screenHeight: number;
  windowWidth: number;
  windowHeight: number;
  devicePixelRatio: number;

  // Hardware
  hardwareConcurrency?: number; // CPU cores
  deviceMemory?: number; // RAM in GB (Chrome only)

  // WebGL (for ML/graphics debugging)
  webglRenderer?: string;
  webglVendor?: string;
  webglVersion?: string;

  // Video codec support
  videoCodecs: {
    h264: boolean;
    h265: boolean;
    vp8: boolean;
    vp9: boolean;
    av1: boolean;
  };

  // App settings (set by the app)
  appSettings?: Record<string, unknown>;
}

interface SessionRecording {
  version: string;
  sessionId: string;
  startTime: number;
  endTime?: number;
  environment: EnvironmentInfo;
  interactions: InteractionEvent[];
  stateChanges: StateChangeEvent[];
  memorySnapshots: MemorySnapshot[];
}
```

### Storage Strategy

**IndexedDB** with multi-session persistence:

```ts
const SESSION_DB_NAME = "app-sessions";
const SESSION_STORE_NAME = "sessions";
const MAX_PERSISTED_SESSIONS = 10; // Keep last 10 sessions for crash debugging
const AUTO_SAVE_INTERVAL_MS = 5000; // Auto-save every 5 seconds

// Rolling limits per session
const MAX_INTERACTIONS = 5000;
const MAX_STATE_CHANGES = 2000;
const MAX_MEMORY_SNAPSHOTS = 1800; // ~1 hour at 2s intervals
```

**Key behaviors:**

- Auto-saves current session to IndexedDB every 5 seconds
- Saves on `beforeunload` for graceful exits
- Prunes old sessions, keeping only the last N
- Can retrieve previous sessions after crash via `getPersistedSessions()`

### Implementation

#### SessionRecorder Class

```ts
// src/services/SessionRecorder.ts
class SessionRecorder {
  private recording: SessionRecording;
  private autoSaveInterval: number | null = null;
  private memoryInterval: number | null = null;

  constructor() {
    this.recording = this.createNewRecording();
    this.setupEventListeners();
    this.startAutoSave();
    this.startMemoryTracking();
  }

  private createNewRecording(): SessionRecording {
    return {
      version: "1.0.0",
      sessionId: `session-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      startTime: Date.now(),
      environment: this.captureEnvironment(),
      interactions: [],
      stateChanges: [],
      memorySnapshots: [],
    };
  }

  // Record user interaction (click, keydown, etc.)
  recordInteraction(event: InteractionEvent): void {
    /* ... */
  }

  // Record app state change
  recordStateChange(event: StateChangeEvent): void {
    /* ... */
  }

  // Get current recording
  getRecording(): SessionRecording {
    /* ... */
  }

  // Download as JSON file
  downloadRecording(): void {
    /* ... */
  }

  // Cleanup listeners
  dispose(): void {
    /* ... */
  }
}

export const sessionRecorder = new SessionRecorder();
```

#### Environment Capture

```ts
private captureEnvironment(): EnvironmentInfo {
  return {
    // Build info (from generated_version.ts)
    buildVersion: APP_VERSION,
    buildCommit: GIT_SHA_SHORT,
    buildTime: BUILD_TIMESTAMP,

    // Browser/OS
    userAgent: navigator.userAgent,
    platform: navigator.platform,
    language: navigator.language,
    cookiesEnabled: navigator.cookieEnabled,
    onLine: navigator.onLine,

    // Display
    screenWidth: screen.width,
    screenHeight: screen.height,
    windowWidth: window.innerWidth,
    windowHeight: window.innerHeight,
    devicePixelRatio: window.devicePixelRatio,

    // Hardware
    hardwareConcurrency: navigator.hardwareConcurrency,
    deviceMemory: (navigator as any).deviceMemory,

    // WebGL
    ...this.getWebGLInfo(),

    // Video codecs
    videoCodecs: this.checkVideoCodecs(),
  };
}

private getWebGLInfo() {
  try {
    const canvas = document.createElement('canvas');
    const gl = canvas.getContext('webgl2') || canvas.getContext('webgl');
    if (!gl) return {};

    const debugInfo = gl.getExtension('WEBGL_debug_renderer_info');
    return {
      webglRenderer: debugInfo ? gl.getParameter(debugInfo.UNMASKED_RENDERER_WEBGL) : undefined,
      webglVendor: debugInfo ? gl.getParameter(debugInfo.UNMASKED_VENDOR_WEBGL) : undefined,
      webglVersion: gl.getParameter(gl.VERSION),
    };
  } catch {
    return {};
  }
}

private checkVideoCodecs() {
  const video = document.createElement('video');
  return {
    h264: video.canPlayType('video/mp4; codecs="avc1.42E01E"') !== '',
    h265: video.canPlayType('video/mp4; codecs="hev1.1.6.L93.B0"') !== '',
    vp8: video.canPlayType('video/webm; codecs="vp8"') !== '',
    vp9: video.canPlayType('video/webm; codecs="vp9"') !== '',
    av1: video.canPlayType('video/mp4; codecs="av01.0.01M.08"') !== '',
  };
}
```

#### Event Listeners

```ts
private setupEventListeners(): void {
  // Capture clicks with element description
  this.clickHandler = (e: MouseEvent) => {
    const target = e.target as HTMLElement;
    this.recordInteraction({
      type: 'click',
      timestamp: Date.now(),
      target: this.describeElement(target),
      details: { x: e.clientX, y: e.clientY },
    });
  };
  window.addEventListener('click', this.clickHandler, { capture: true });

  // Capture keyboard shortcuts (not regular typing)
  this.keydownHandler = (e: KeyboardEvent) => {
    if (e.key.length > 1 || e.ctrlKey || e.metaKey || e.altKey) {
      this.recordInteraction({
        type: 'keydown',
        timestamp: Date.now(),
        target: this.describeElement(e.target as HTMLElement),
        details: { key: e.key, ctrl: e.ctrlKey, meta: e.metaKey, alt: e.altKey },
      });
    }
  };
  window.addEventListener('keydown', this.keydownHandler, { capture: true });

  // Capture console.error
  this.originalConsoleError = console.error;
  console.error = (...args) => {
    this.recordStateChange({
      type: 'error',
      timestamp: Date.now(),
      details: { message: args.map(a => String(a)).join(' ') },
    });
    this.originalConsoleError?.apply(console, args);
  };

  // Capture unhandled errors
  window.addEventListener('error', (e) => {
    this.recordStateChange({
      type: 'error',
      timestamp: Date.now(),
      details: { message: e.message, filename: e.filename, lineno: e.lineno },
    });
  });

  // Capture unhandled promise rejections
  window.addEventListener('unhandledrejection', (e) => {
    this.recordStateChange({
      type: 'error',
      timestamp: Date.now(),
      details: { message: `Unhandled rejection: ${e.reason}` },
    });
  });
}

private describeElement(el: HTMLElement | null): string {
  if (!el) return 'unknown';
  const parts: string[] = [el.tagName.toLowerCase()];
  if (el.id) parts.push(`#${el.id}`);
  if (el.className && typeof el.className === 'string') {
    const classes = el.className.split(' ').filter(Boolean).slice(0, 3);
    if (classes.length) parts.push(`.${classes.join('.')}`);
  }
  const text = el.textContent?.trim().slice(0, 30);
  if (text) parts.push(`"${text}${text.length >= 30 ? '...' : ''}"`);
  return parts.join('');
}
```

#### Memory Tracking (Chrome Only)

```ts
private startMemoryTracking(): void {
  const perf = performance as Performance & {
    memory?: { usedJSHeapSize: number; totalJSHeapSize: number; jsHeapSizeLimit: number };
  };
  if (!perf.memory) return;

  // Track every 2 seconds
  this.memoryInterval = window.setInterval(() => {
    const { usedJSHeapSize, totalJSHeapSize, jsHeapSizeLimit } = perf.memory!;
    const usedMB = Math.round((usedJSHeapSize / 1024 / 1024) * 100) / 100;
    const percentUsed = Math.round((usedJSHeapSize / jsHeapSizeLimit) * 10000) / 100;

    this.recording.memorySnapshots.push({
      timestamp: Date.now(),
      usedJSHeapSize,
      totalJSHeapSize,
      jsHeapSizeLimit,
      usedMB,
      percentUsed,
    });

    // Trim if over limit
    if (this.recording.memorySnapshots.length > MAX_MEMORY_SNAPSHOTS) {
      this.recording.memorySnapshots = this.recording.memorySnapshots.slice(-MAX_MEMORY_SNAPSHOTS);
    }

    // Warn if high
    if (percentUsed > 80) {
      console.warn(`[SessionRecorder] HIGH MEMORY: ${usedMB}MB (${percentUsed}%)`);
    }
  }, 2000);
}
```

#### IndexedDB Persistence

```ts
async function saveSessionToDB(recording: SessionRecording): Promise<void> {
  const db = await openSessionDB();
  const tx = db.transaction([SESSION_STORE_NAME], "readwrite");
  tx.objectStore(SESSION_STORE_NAME).put(recording);
  await tx.complete;
  db.close();
}

async function pruneOldSessions(): Promise<void> {
  const db = await openSessionDB();
  const tx = db.transaction([SESSION_STORE_NAME], "readwrite");
  const store = tx.objectStore(SESSION_STORE_NAME);
  const index = store.index("startTime");

  // Get all sessions sorted by time, delete oldest beyond limit
  const cursor = index.openCursor(null, "prev");
  let count = 0;
  cursor.onsuccess = () => {
    if (cursor.result) {
      count++;
      if (count > MAX_PERSISTED_SESSIONS) {
        store.delete(cursor.result.value.sessionId);
      }
      cursor.result.continue();
    }
  };
}

// Retrieve previous sessions (for crash debugging)
export async function getPersistedSessions(): Promise<SessionRecording[]> {
  const db = await openSessionDB();
  const tx = db.transaction([SESSION_STORE_NAME], "readonly");
  const sessions = await tx
    .objectStore(SESSION_STORE_NAME)
    .index("startTime")
    .getAll();
  db.close();
  return sessions.reverse(); // Newest first
}

export async function clearPersistedSessions(): Promise<void> {
  const db = await openSessionDB();
  const tx = db.transaction([SESSION_STORE_NAME], "readwrite");
  tx.objectStore(SESSION_STORE_NAME).clear();
  db.close();
}
```

#### App-Level State Change Helpers

Create convenience functions for common state changes:

```ts
// Convenience functions for recording app events
export function recordVideoLoad(details?: Record<string, unknown>): void {
  sessionRecorder.recordStateChange({
    type: "video_load",
    timestamp: Date.now(),
    details,
  });
}

export function recordSyncComplete(details?: Record<string, unknown>): void {
  sessionRecorder.recordStateChange({
    type: "sync_complete",
    timestamp: Date.now(),
    details,
  });
}

export function recordCacheLoad(details: { itemCount: number }): void {
  sessionRecorder.recordStateChange({
    type: "cache_load",
    timestamp: Date.now(),
    details,
  });
}

// Generic for custom events
export function recordAppEvent(
  type: string,
  details?: Record<string, unknown>,
): void {
  sessionRecorder.recordStateChange({ type, timestamp: Date.now(), details });
}
```

### Console Debug Interface

Expose debug functions on `window` for easy console access:

```ts
if (typeof window !== "undefined") {
  const appDebug = {
    // Get all persisted sessions (for crash debugging)
    getCrashLogs: getPersistedSessions,

    // Clear all persisted sessions
    clearCrashLogs: clearPersistedSessions,

    // Get current session
    getCurrentSession: () => sessionRecorder.getRecording(),

    // Download current session as JSON
    downloadSession: () => sessionRecorder.downloadRecording(),

    // Get session stats
    getStats: () => ({
      duration: Date.now() - sessionRecorder.getRecording().startTime,
      interactions: sessionRecorder.getRecording().interactions.length,
      stateChanges: sessionRecorder.getRecording().stateChanges.length,
      errors: sessionRecorder
        .getRecording()
        .stateChanges.filter((e) => e.type === "error").length,
    }),

    // Get current memory (Chrome only)
    getMemory: () => {
      const perf = performance as any;
      if (!perf.memory)
        return { error: "Memory API not available (Chrome only)" };
      return {
        usedMB:
          Math.round((perf.memory.usedJSHeapSize / 1024 / 1024) * 100) / 100,
        limitMB:
          Math.round((perf.memory.jsHeapSizeLimit / 1024 / 1024) * 100) / 100,
        percentUsed:
          Math.round(
            (perf.memory.usedJSHeapSize / perf.memory.jsHeapSizeLimit) * 10000,
          ) / 100,
      };
    },

    // Get environment info
    getEnvironment: () => sessionRecorder.getRecording().environment,
  };

  (window as any).appDebug = appDebug;
  console.log("[SessionRecorder] Debug functions available at window.appDebug");
}
```

**Usage from browser console:**

```js
// After a crash, open console and run:
await appDebug.getCrashLogs(); // See previous sessions

// Download current session for bug report
appDebug.downloadSession();

// Check memory usage
appDebug.getMemory();

// View environment info
appDebug.getEnvironment();
```

### Export Format

```json
{
  "version": "1.0.0",
  "sessionId": "session-1702560000000-a1b2c3",
  "startTime": 1702560000000,
  "endTime": 1702563600000,
  "environment": {
    "buildCommit": "abc1234",
    "userAgent": "Mozilla/5.0...",
    "platform": "MacIntel",
    "screenWidth": 1920,
    "screenHeight": 1080,
    "hardwareConcurrency": 8,
    "deviceMemory": 16,
    "webglRenderer": "ANGLE (Apple, Apple M1, OpenGL 4.1)",
    "videoCodecs": { "h264": true, "vp9": true, "av1": false }
  },
  "interactions": [
    {
      "type": "click",
      "timestamp": 1702560001000,
      "target": "button#submit\"Save\""
    }
  ],
  "stateChanges": [
    {
      "type": "video_load",
      "timestamp": 1702560002000,
      "details": { "duration": 120 }
    },
    {
      "type": "error",
      "timestamp": 1702560003000,
      "details": { "message": "Network timeout" }
    }
  ],
  "memorySnapshots": [
    { "timestamp": 1702560000000, "usedMB": 45.2, "percentUsed": 2.8 }
  ]
}
```

### Integration with Bug Reporter

Add option to attach/download debug log when submitting bug reports:

```tsx
// In BugReportModal
const [includeDebugLog, setIncludeDebugLog] = useState(true);
const [stats, setStats] = useState<{
  interactions: number;
  errors: number;
} | null>(null);

useEffect(() => {
  const s = (window as any).appDebug?.getStats();
  if (s) setStats(s);
}, [isOpen]);

// In form:
<label>
  <input
    type="checkbox"
    checked={includeDebugLog}
    onChange={(e) => setIncludeDebugLog(e.target.checked)}
  />
  Include debug log ({stats?.interactions} events, {stats?.errors} errors)
</label>;

// On submit, if includeDebugLog:
sessionRecorder.downloadRecording(); // Downloads JSON file for user to attach
```

### Privacy Considerations

**Captured by default:**

- Click targets (element selector + first 30 chars of text)
- Keyboard shortcuts (not regular typing)
- Console output
- Errors and stack traces
- Environment info

**NOT captured:**

- Form input values (only capture that click/submit happened)
- Full text content beyond 30 chars
- Request/response bodies
- Passwords, tokens, or sensitive data

**Configurable redaction:**

```ts
const REDACT_PATTERNS = [/password/i, /token/i, /secret/i, /key/i, /auth/i];

function redactSensitive(text: string): string {
  for (const pattern of REDACT_PATTERNS) {
    if (pattern.test(text)) return "[REDACTED]";
  }
  return text;
}
```

### Settings UI

```
DEBUG LOG
┌─────────────────────────────────────┐
│ Current session: 1,247 events       │
│ Duration: 45 minutes                │
│ Errors: 3                           │
│                                     │
│ [Download Session]  [Clear]         │
│                                     │
│ Previous sessions: 4 saved          │
│ [View Crash Logs]                   │
└─────────────────────────────────────┘
```

### Initialization

```tsx
// In app entry point (main.tsx or App.tsx)
import { sessionRecorder, recordAppEvent } from "./services/SessionRecorder";

// SessionRecorder auto-starts on import
// Just log app-specific events:
recordAppEvent("app_initialized", { version: APP_VERSION });
```

---

## Architecture Notes

### Pure Function Extraction

For testability, extract pure logic from React hooks into separate utility files:

**src/utils/shakeDetection.ts** - Pure shake detection functions:

```ts
export interface Acceleration {
  x: number;
  y: number;
  z: number;
}

export function calculateMagnitude(accel: Acceleration): number {
  return Math.sqrt(accel.x ** 2 + accel.y ** 2 + accel.z ** 2);
}

export function isShakeDetected(
  magnitude: number,
  threshold: number,
  currentTime: number,
  lastShakeTime: number,
  cooldownMs: number,
): boolean {
  if (magnitude <= threshold) return false;
  return currentTime - lastShakeTime > cooldownMs;
}

export function extractAcceleration(
  event: DeviceMotionEvent,
): Acceleration | null {
  const accel = event.acceleration ?? event.accelerationIncludingGravity;
  const { x, y, z } = accel || {};
  if (x == null || y == null || z == null) return null;
  return { x, y, z };
}
```

**src/utils/bugReportFormatters.ts** - Pure formatting functions:

```ts
// DRY: Centralize build link formatting - used by bug reports, crash reports, about dialogs
export function formatBuildLink(): string {
  return `[${GIT_SHA_SHORT}](${GIT_COMMIT_URL})`;
}

export function formatDate(date: Date = new Date()): string {
  return date.toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function buildDefaultTitle(): string {
  return "Bug";
}

export function buildDefaultDescription(
  currentDate: Date = new Date(),
): string {
  const dateStr = formatDate(currentDate);

  // DRY: Use formatBuildLink() for consistent build version display
  return `**Date:** ${dateStr}
**Build:** ${formatBuildLink()}

**What were you trying to do?**

**What happened instead?**

**Steps to reproduce:**
1. `;
}

export function buildIssueBody(
  data: BugReportData,
  metadata: BugReportMetadata,
  options: { isMobile: boolean; hasScreenshot: boolean },
): string {
  // ... builds full issue body with metadata table
}

export function buildGitHubIssueUrl(
  repoUrl: string,
  title: string,
  body: string,
  labels: string[] = ["bug", "from-app"],
): string {
  const url = new URL(`${repoUrl}/issues/new`);
  url.searchParams.set("title", title);
  url.searchParams.set("body", body);
  url.searchParams.set("labels", labels.join(","));
  return url.toString();
}

export function getMetadata(
  getCurrentRoute: () => string,
  getUserAgent: () => string,
  currentDate: Date = new Date(),
): BugReportMetadata {
  return {
    route: getCurrentRoute(),
    userAgent: getUserAgent(),
    timestamp: currentDate.toISOString(),
    appVersion: APP_VERSION,
  };
}
```

**Benefits:**

- Pure functions are easy to test without React testing utilities
- No mocking of browser APIs required for core logic
- Hooks become thin wrappers that compose pure functions with browser APIs
- Clear separation between "what to compute" and "how to interact with the browser"

---

## Testing Guidance

### Unit Tests

**Shake detection (pure functions):**

```ts
describe("calculateMagnitude", () => {
  it("calculates 3D magnitude using Pythagorean theorem", () => {
    expect(calculateMagnitude({ x: 3, y: 4, z: 0 })).toBe(5);
    expect(calculateMagnitude({ x: 1, y: 2, z: 2 })).toBe(3);
  });
});

describe("isShakeDetected", () => {
  it("returns true when magnitude exceeds threshold and cooldown passed", () => {
    expect(isShakeDetected(30, 25, 3000, 0, 2000)).toBe(true);
  });

  it("returns false when still in cooldown period", () => {
    expect(isShakeDetected(30, 25, 2500, 1000, 2000)).toBe(false);
  });
});
```

**Bug report formatters (pure functions):**

```ts
describe("buildDefaultDescription", () => {
  it("includes commit info when provided", () => {
    const commit = {
      sha: "abc1234",
      message: "Fix a bug",
      url: "https://github.com/test/repo/commit/abc1234",
    };
    const result = buildDefaultDescription(commit, repoUrl, testDate);
    expect(result).toContain(
      "[abc1234](https://github.com/test/repo/commit/abc1234)",
    );
  });
});

describe("buildGitHubIssueUrl", () => {
  it("builds valid URL with title and body", () => {
    const url = buildGitHubIssueUrl(repoUrl, "Bug Title", "Bug body");
    expect(url).toContain("title=Bug+Title");
    expect(url).toContain("labels=bug%2Cfrom-app");
  });
});
```

**Shake detection (with mocks):**

```ts
// Mock DeviceMotionEvent
fireEvent(
  window,
  new DeviceMotionEvent("devicemotion", {
    accelerationIncludingGravity: { x: 20, y: 20, z: 20 },
  }),
);
expect(onShake).toHaveBeenCalled();
```

**Latest commit fetch:**

```ts
// Mock fetch for GitHub API
global.fetch = vi.fn().mockResolvedValue({
  ok: true,
  json: () =>
    Promise.resolve([
      {
        sha: "abc1234567890",
        commit: { message: "Fix bug\n\nDetails" },
        html_url: "https://github.com/owner/repo/commit/abc1234",
      },
    ]),
});

const commit = await fetchLatestCommit("https://github.com/owner/repo");
expect(commit).toEqual({
  sha: "abc1234",
  message: "Fix bug",
  url: "https://github.com/owner/repo/commit/abc1234",
});
```

### Integration Tests

**Clipboard and window.open:**

```ts
// Mock clipboard and window.open
const writeText = vi.fn();
Object.assign(navigator, { clipboard: { writeText } });
const windowOpen = vi.spyOn(window, "open").mockImplementation(() => null);

await bugReporter.submit({
  title: "Bug",
  description: "Test",
  includeMetadata: true,
});

expect(writeText).toHaveBeenCalledWith(expect.stringContaining("Bug"));
expect(windowOpen).toHaveBeenCalledWith(
  expect.stringContaining("github.com"),
  "_blank",
  expect.any(String),
);
```

### E2E Tests (Playwright)

```ts
test("user can open bug report modal with keyboard shortcut", async ({
  page,
}) => {
  await page.goto("/");
  await page.keyboard.press("Control+i");
  await expect(page.locator("text=Report a Bug")).toBeVisible();
});

test("bug report form has pre-filled content", async ({ page }) => {
  await page.goto("/");
  await page.keyboard.press("Control+i");
  await expect(page.locator('input[id="bug-title"]')).toHaveValue("Bug");
  await expect(page.locator("textarea")).toContainText(
    "What were you trying to do",
  );
});
```
