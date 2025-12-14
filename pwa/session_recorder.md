# Session Debug Log (Flight Recorder)

A reusable specification for capturing user actions, console output, errors, and environment info in IndexedDB for debugging PWAs.

---

## Reference Implementation

See [swing-analyzer SessionRecorder.ts](https://github.com/idvorkin/swing-analyzer) for a production implementation.

---

## Purpose

Capture a rolling log of user actions, console output, errors, and environment info in IndexedDB. When users encounter problems (including crashes), they can download this log and attach it to bug reports—providing the context developers need to reproduce issues.

**Key design principles:**

- Always active (no opt-in required for basic logging)
- Auto-persists to IndexedDB for crash recovery
- Keeps multiple sessions for post-crash debugging
- Captures rich environment info for bug reports

## What to Capture

| Event Type           | Data Captured                                                                 |
| -------------------- | ----------------------------------------------------------------------------- |
| **User Actions**     | Click target (selector + text preview), keyboard shortcuts, navigation        |
| **Console Output**   | `console.log`, `console.warn`, `console.error` with arguments                 |
| **Errors**           | Uncaught exceptions, unhandled promise rejections with stack traces           |
| **State Changes**    | App-specific events (e.g., `extraction_start`, `playback_stop`, `cache_load`) |
| **Memory Snapshots** | JS heap usage over time (Chrome only)                                         |
| **Environment**      | Browser, OS, screen, WebGL, video codecs, hardware info                       |

## Data Types

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

## Storage Strategy

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

## Implementation

### SessionRecorder Class

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

### Environment Capture

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

### Event Listeners

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

### Memory Tracking (Chrome Only)

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

### IndexedDB Persistence

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

### App-Level State Change Helpers

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

## Console Debug Interface

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

## Export Format

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

## Integration with Bug Reporter

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

## Privacy Considerations

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

## Settings UI

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

## Initialization

```tsx
// In app entry point (main.tsx or App.tsx)
import { sessionRecorder, recordAppEvent } from "./services/SessionRecorder";

// SessionRecorder auto-starts on import
// Just log app-specific events:
recordAppEvent("app_initialized", { version: APP_VERSION });
```
