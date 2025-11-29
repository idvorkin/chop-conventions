# Settings UI Convention

## Required Elements

Every app settings screen should include:

| Element | Purpose |
|---------|---------|
| Plus/Premium section | Upsell or advanced feature visibility |
| Cog icon on avatar | Standard access pattern - settings behind profile avatar gear icon |
| Link to create issue | `https://github.com/{owner}/{repo}/issues/new` |
| Link to repository | `https://github.com/{owner}/{repo}` |

## UX Patterns

### Access

- Place settings behind a **cog icon on the user avatar/profile**
- Keep settings discoverable but not prominent

### Dialog Behavior

- **Pop dialog to keep small as required** - use modal/dialog for settings, not full page
- Keep the dialog compact; expand only when user needs more options
- Avoid overwhelming users with all options at once

### Organization

- Group related settings logically
- Place premium/plus features in dedicated section
- Show app metadata (version, links) at bottom

## Implementation

```tsx
// Settings dialog structure
<SettingsDialog>
  <Section title="General">
    {/* Core settings */}
  </Section>
  
  <Section title="Plus Features">
    {/* Premium/advanced options */}
  </Section>
  
  <Section title="About">
    <Link href={`https://github.com/${owner}/${repo}`}>
      View Repository
    </Link>
    <Link href={`https://github.com/${owner}/${repo}/issues/new`}>
      Report Issue
    </Link>
    <VersionInfo />
  </Section>
</SettingsDialog>
```

## Reference

See [PWA_ENABLEMENT_SPEC.md](./PWA_ENABLEMENT_SPEC.md) for version checking and update notification patterns that integrate with settings.
