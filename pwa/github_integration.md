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
- GitHub OAuth authentication for bug reporting
- Shake-to-report, keyboard shortcuts, and button triggers
- Screenshot capture and issue creation

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

Each project adopting this spec must configure:

| Setting                     | Required | Description                                                   |
| --------------------------- | -------- | ------------------------------------------------------------- |
| `GITHUB_REPO_URL`           | No       | Override auto-detected repo URL                               |
| `GITHUB_CLIENT_ID`          | Yes\*    | OAuth App client ID (\*for bug reporting)                     |
| `GITHUB_OAUTH_REDIRECT_URI` | Yes\*    | OAuth callback URL                                            |
| `BUG_REPORT_LABELS`         | No       | Default labels for issues (e.g., `["bug", "from-app"]`)       |
| `SHAKE_THRESHOLD`           | No       | Acceleration threshold for shake detection (default: 15 m/s²) |
| `SHAKE_COOLDOWN_MS`         | No       | Cooldown between shake detections (default: 5000ms)           |

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

### Authentication: GitHub OAuth

Issues are created **as the authenticated user**, not a bot. This builds trust and allows users to track their own issues.

#### Why OAuth?

- Issues show the user's GitHub username
- Users can follow up on their own issues
- No server-side token storage required
- Users can revoke access anytime

#### OAuth Flow

1. User clicks "Report Bug" (or triggers via shake/keyboard)
2. If not authenticated, show "Sign in with GitHub" button
3. Redirect to GitHub OAuth authorization:
   ```
   https://github.com/login/oauth/authorize
     ?client_id={CLIENT_ID}
     &redirect_uri={REDIRECT_URI}
     &scope=public_repo
     &state={random_state}
   ```
4. User authorizes the app
5. GitHub redirects back with `?code=xxx&state=xxx`
6. Exchange code for access token (via backend proxy to protect client secret)
7. Store token securely, proceed to bug report form

#### Token Storage

```ts
// Store encrypted in localStorage or sessionStorage
interface GitHubAuth {
  accessToken: string;
  username: string;
  avatarUrl: string;
  expiresAt?: number;
}
```

**Security considerations:**

- Use `sessionStorage` for stricter security (cleared on tab close)
- Or `localStorage` with encryption for persistence
- Always validate state parameter to prevent CSRF
- Provide clear "Sign out" option

#### Sign Out

Clear stored tokens and show unauthenticated state:

```ts
function signOutGitHub() {
  localStorage.removeItem("github_auth");
  // Optionally revoke token via GitHub API
}
```

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
- Trigger when acceleration magnitude exceeds threshold (~15 m/s²)
- Apply cooldown (5 seconds) to prevent multiple triggers
- Only active when user has enabled "Shake to report bug" in settings

**Precondition:** User must opt-in via Settings

---

### UX Flow

#### Step 1: Trigger Detected

Show non-blocking prompt:

```
┌─────────────────────────────────────────┐
│ Shake detected! Want to report a bug?   │
│                                         │
│ [Report Bug]  [Dismiss]                 │
│ ☐ Don't ask again                       │
└─────────────────────────────────────────┘
```

- **Report Bug** → Open bug report modal
- **Dismiss** → Close, do nothing
- **Don't ask again** → Disable shake detection, persist preference

#### Step 2: Authentication Check

If user is not signed in to GitHub:

```
┌─────────────────────────────────────────┐
│ Sign in to report bugs                  │
│                                         │
│ Issues will be created with your        │
│ GitHub username so you can track them.  │
│                                         │
│ [Sign in with GitHub]  [Cancel]         │
└─────────────────────────────────────────┘
```

#### Step 3: Bug Report Form

```
┌─────────────────────────────────────────┐
│ Report a Bug                    [X]     │
├─────────────────────────────────────────┤
│ Title *                                 │
│ ┌─────────────────────────────────────┐ │
│ │ Bug: /dashboard – Nov 29, 2025      │ │
│ └─────────────────────────────────────┘ │
│                                         │
│ Description *                           │
│ ┌─────────────────────────────────────┐ │
│ │ **What were you trying to do?**     │ │
│ │                                     │ │
│ │ **What happened instead?**          │ │
│ │                                     │ │
│ │ **Steps to reproduce:**             │ │
│ │ 1.                                  │ │
│ └─────────────────────────────────────┘ │
│                                         │
│ ☑ Attach screenshot of current screen  │
│ ☑ Include technical details            │
│   (route, app version, browser)        │
│                                         │
│ Signed in as @username  [Sign out]     │
│                                         │
│ [Cancel]              [Submit Bug]      │
└─────────────────────────────────────────┘
```

**Pre-filled values:**

- Title: `Bug: {current route} – {date}`
- Description: Template with prompts

#### Step 4: Screenshot Capture

If "Attach screenshot" is checked:

1. Show overlay explaining what will happen:

   ```
   Capturing screenshot...
   Your browser will ask which screen to share.
   Select this tab, then we'll grab a single image.
   ```

2. Call `getDisplayMedia`:

   ```ts
   const stream = await navigator.mediaDevices.getDisplayMedia({
     video: { displaySurface: "browser" },
   });
   ```

3. Capture single frame to canvas, convert to PNG blob

4. Immediately stop the video track

5. If user cancels or permission denied:
   - Proceed without screenshot
   - Add note to description: `_Screenshot requested but not captured._`

#### Step 5: Submission Feedback

**During submission:**

```
Creating GitHub issue...
```

**On success:**

```
✓ Bug created: #123
[Open in GitHub]
```

**On failure:**

```
✗ Failed to create issue
[Copy bug text] [Try again]
```

---

### Technical Design

#### Data Contract

```ts
interface BugReportPayload {
  title: string;
  description: string;
  metadata: {
    route: string;
    appVersion: string;
    userAgent: string;
    timestamp: string; // ISO 8601
    userId?: string;
  };
  screenshot?: Blob;
  labels?: string[];
}

interface BugReportResponse {
  issueUrl: string;
  issueNumber: number;
}
```

#### GitHub API Call

```ts
// POST https://api.github.com/repos/{owner}/{repo}/issues
// Authorization: Bearer {user_access_token}

const response = await fetch(
  `https://api.github.com/repos/${owner}/${repo}/issues`,
  {
    method: "POST",
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      title: payload.title,
      body: buildIssueBody(payload),
      labels: payload.labels || ["bug", "from-app"],
    }),
  },
);
```

#### Issue Body Template

```md
{description}

---

**App Metadata**
| Field | Value |
|-------|-------|
| Route | `{route}` |
| App Version | `{appVersion}` |
| Browser | `{userAgent}` |
| Timestamp | `{timestamp}` |

{if screenshot}
**Screenshot**
![Screenshot]({screenshotUrl})
{/if}
```

#### Screenshot Handling

Since GitHub Issues API doesn't support direct image upload, options:

1. **Upload to GitHub via Contents API** — Store in `.github/bug-screenshots/`
2. **Upload to external storage** — S3, Cloudflare R2, etc.
3. **Encode as data URL** — Only for small images, not recommended

---

### Edge Cases

| Scenario                        | Handling                                          |
| ------------------------------- | ------------------------------------------------- |
| **Offline**                     | Queue bug locally, prompt "Will sync when online" |
| **Multiple shakes**             | Debounce; ignore while modal is open              |
| **getDisplayMedia unavailable** | Proceed without screenshot, log telemetry         |
| **OAuth token expired**         | Prompt to re-authenticate                         |
| **Rate limited**                | Show error, suggest trying later                  |
| **No GitHub account**           | Offer to copy bug text for manual submission      |

---

## Framework-Specific: React

### Hooks

#### useGitHubAuth

```tsx
function useGitHubAuth() {
  const [auth, setAuth] = useState<GitHubAuth | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    // Check for stored auth on mount
    const stored = localStorage.getItem("github_auth");
    if (stored) setAuth(JSON.parse(stored));
    setIsLoading(false);
  }, []);

  const signIn = () => {
    /* redirect to OAuth */
  };
  const signOut = () => {
    /* clear storage */
  };

  return { auth, isLoading, isAuthenticated: !!auth, signIn, signOut };
}
```

#### useShakeDetector

```tsx
function useShakeDetector(options: {
  enabled: boolean;
  threshold?: number;
  cooldown?: number;
  onShake: () => void;
}) {
  useEffect(() => {
    if (!options.enabled) return;

    const handler = (event: DeviceMotionEvent) => {
      const { x, y, z } = event.accelerationIncludingGravity || {};
      const magnitude = Math.sqrt(
        (x || 0) ** 2 + (y || 0) ** 2 + (z || 0) ** 2,
      );
      if (magnitude > (options.threshold || 15)) {
        options.onShake();
      }
    };

    window.addEventListener("devicemotion", handler);
    return () => window.removeEventListener("devicemotion", handler);
  }, [options.enabled]);
}
```

#### useBugReporter

```tsx
function useBugReporter() {
  const { auth } = useGitHubAuth();
  const [isOpen, setIsOpen] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const open = () => setIsOpen(true);
  const close = () => setIsOpen(false);

  const submit = async (payload: BugReportPayload) => {
    setIsSubmitting(true);
    try {
      const result = await createGitHubIssue(auth.accessToken, payload);
      return result;
    } finally {
      setIsSubmitting(false);
    }
  };

  return { isOpen, open, close, submit, isSubmitting, isAuthenticated: !!auth };
}
```

### Components

```tsx
// GitHubLink - Display repo link
<GitHubLink showStars={true} />

// GitHubAuthButton - Sign in/out
<GitHubAuthButton />

// BugReportModal - Full bug reporting flow
<BugReportModal
  isOpen={isOpen}
  onClose={close}
  onSubmit={handleSubmit}
/>

// BugReportTrigger - Wrapper that handles all triggers
<BugReportTrigger
  enableShake={true}
  enableKeyboard={true}
>
  {children}
</BugReportTrigger>
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

**OAuth flow:**

```ts
// Mock window.location for redirect
// Mock fetch for token exchange
```

### Integration Tests

**GitHub API:**

```ts
// Use MSW to mock GitHub API responses
server.use(
  rest.post(
    "https://api.github.com/repos/:owner/:repo/issues",
    (req, res, ctx) => {
      return res(ctx.json({ number: 123, html_url: "..." }));
    },
  ),
);
```

### E2E Tests (Playwright/Cypress)

```ts
// Test full flow
test("user can report a bug", async ({ page }) => {
  await page.goto("/");
  await page.keyboard.press("Control+i");
  await page.fill('[name="title"]', "Test bug");
  await page.fill('[name="description"]', "Description");
  await page.click("text=Submit Bug");
  await expect(page.locator("text=Bug created")).toBeVisible();
});
```
