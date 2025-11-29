# GitHub Integration Spec

A reusable specification for integrating GitHub features into PWAs: displaying the source repo link and enabling users to file bugs directly from the app.

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

## Testing Guidance

### Unit Tests

**Shake detection:**

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
