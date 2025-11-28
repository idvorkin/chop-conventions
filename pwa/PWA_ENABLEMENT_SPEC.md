# PWA Enablement Spec

## Success Criteria

1. **Installable** - App shows browser "Install" prompt on mobile and desktop
2. **Offline capable** - App loads without network after first visit
3. **Auto-update detection** - App detects when new version is deployed
4. **Update notification** - User sees popup when update available with "Reload" button
5. **Manual update check** - Settings has "Check for Update" button that works
6. **Last check timestamp** - Shows when updates were last checked, persists across sessions
7. **Standalone mode** - When installed, app runs without browser chrome (fullscreen app experience)
8. **Testable** - All browser API access goes through DeviceService (Humble Object pattern)
9. **Graceful degradation** - App works normally if service worker fails to register
10. **Accessible** - Update notification announced to screen readers, dismissable with keyboard

## Pre-Implementation Questions

Before starting, ask the user - But give 3-5 reco for each.

1. **App icons**: "Describe what you want the PWA icons to look like.
2. **App name**: "What should the installed app be called? (short_name max 12 chars)"
3. **Orientation**: "Allow both"

## Implementation Details

### Dependencies

```bash
npm install -D vite-plugin-pwa
```

### Files to Create/Modify

| File                                     | Purpose                                               |
| ---------------------------------------- | ----------------------------------------------------- |
| `vite.config.ts`                         | Add VitePWA plugin with manifest, workbox config      |
| `src/services/DeviceService.ts`          | Humble Object for localStorage access (if not exists) |
| `src/hooks/useVersionCheck.ts`           | Hook for update detection using DeviceService         |
| `src/components/VersionNotification.tsx` | Popup when update available                           |
| `src/hooks/useVersionCheck.test.ts`      | Unit tests for the hook                               |
| `public/pwa-192x192.png`                 | App icon (192x192)                                    |
| `public/pwa-512x512.png`                 | App icon (512x512)                                    |
| Settings UI                              | Add "Updates" section                                 |

### Humble Object Pattern

All browser API access must go through `DeviceService` for testability:

```typescript
// src/services/DeviceService.ts
export const DeviceService = {
  getStorageItem(key: string): string | null {
    try {
      return localStorage.getItem(key);
    } catch {
      return null;
    }
  },
  setStorageItem(key: string, value: string): void {
    try {
      localStorage.setItem(key, value);
    } catch {
      // localStorage unavailable (private browsing, quota)
    }
  },
};
```

The hook uses `DeviceService.getStorageItem()` / `DeviceService.setStorageItem()` instead of direct `localStorage` calls.

### Tests Required

**src/hooks/useVersionCheck.test.ts:**

| Test Case                                           | Validates                          |
| --------------------------------------------------- | ---------------------------------- |
| `initializes lastCheckTime from storage`            | Reads persisted timestamp on mount |
| `returns null lastCheckTime when storage empty`     | Handles first-time users           |
| `checkForUpdate updates lastCheckTime`              | Button click updates state         |
| `checkForUpdate persists to storage`                | Timestamp survives refresh         |
| `checkForUpdate sets isChecking during check`       | Loading state works                |
| `checkForUpdate resets isChecking after completion` | Loading state clears               |
| `does not double-check when already checking`       | Prevents spam clicks               |

**Test setup:** Create mock DeviceService to inject into hook for isolated testing.

### Key Config Values

- `registerType: "autoUpdate"` - Auto-register new service workers
- `display: "standalone"` - Fullscreen app mode when installed
- `globPatterns: ["**/*.{js,css,html,ico,png,svg}"]` - Cache all static assets
- Check interval: 30 minutes automatic, plus manual button

### Reference Implementation

See idvorkin/magic-monitor repo for working examples:

- `src/hooks/useVersionCheck.ts`
- `src/components/VersionNotification.tsx`
- `src/services/DeviceService.ts`
- `vite.config.ts`

## UX Best Practices

### Update Notification Timing

- **Don't show immediately on page load** - Wait 5-10 seconds or until user is idle
- Interrupting users mid-task causes frustration and ignored notifications
- Consider using `requestIdleCallback` or a simple delay

### Error Handling

- **Silent fail on SW registration errors** - Some corporate proxies/browsers block service workers
- Log errors to console for debugging, but don't show error UI to users
- App should be fully functional without SW (just loses offline/update features)

### Cache Versioning

- Document strategy for breaking changes that require cache bust
- Consider adding version number to cached assets or using `skipWaiting()` for critical updates

## Accessibility Requirements

### Update Notification Component

```tsx
<div role="alert" aria-live="polite" aria-label="Application update available">
  {/* notification content */}
  <button onClick={onDismiss} aria-label="Dismiss update notification">
    ✕
  </button>
  <button onClick={reload}>Reload</button>
</div>
```

- `role="alert"` announces to screen readers
- `aria-live="polite"` waits for user to finish current task
- All buttons keyboard accessible and labeled

## Future Considerations (Optional)

### Analytics

- Track update notification impressions vs clicks (are users ignoring updates?)
- Track PWA install rate
- Monitor SW registration success/failure rates

### Rollback Strategy

- Document how to force cache clear if bad SW is deployed
- Consider "skip waiting" mechanism for critical security updates
