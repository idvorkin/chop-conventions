---
name: build-bd-static
description: Build a static `bd` binary when Homebrew is unavailable or its dynamically-linked package is not portable enough for the machine.
allowed-tools: Bash, Read
---

# Build bd Static

Use Homebrew as the default install path:

```bash
brew install beads
brew upgrade beads
```

Use this skill only when Homebrew is unavailable, or when the packaged `bd` is not enough because you need a self-contained binary that avoids shared-library issues (for example ICU version mismatches across machines).

`bd upgrade` is not the installer path here; it reviews version changes but does not replace the binary.

## Usage

```text
/build-bd-static
```

## Steps

1. Check current version:

   ```bash
   bd --version 2>/dev/null || echo "bd not installed"
   ```

2. Find the latest available version:

   ```bash
   go list -m -json github.com/steveyegge/beads@latest 2>&1 | grep '"Version"'
   ```

3. If already on the latest version, report that and stop. Otherwise, proceed to build the static fallback binary.

4. Build the latest version statically with CGO disabled:

   ```bash
   CGO_ENABLED=0 go install github.com/steveyegge/beads/cmd/bd@latest
   ```

   `@latest` resolves at install time, so there's no need to pass the version from step 2 — that step is just for the "already on latest?" gate.

   `CGO_ENABLED=0` forces pure-Go alternatives for all dependencies (ICU regex, SQLite, etc.), producing a binary with zero shared library dependencies.

   > **macOS warning:** `CGO_ENABLED=0` can cause crashes (e.g., during `bd init`) due to CGO/SQLite incompatibilities on macOS. macOS users should use `CGO_ENABLED=1` instead — see upstream `docs/INSTALLING.md` for details.

5. Verify:

   ```bash
   bd --version
   # Linux:
   ldd "$(which bd)" 2>&1  # Should say "not a dynamic executable"
   # macOS:
   otool -L "$(which bd)"  # Should show no external library entries
   ```

## Why static?

The `bd` binary uses `go-icu-regex` (CGo) which links against the system's ICU library. Different machines have different ICU versions (e.g., Homebrew's `icu4c@77` vs system `libicu76`), causing runtime failures:

```text
bd: error while loading shared libraries: libicui18n.so.77: cannot open shared object file
```

Static compilation eliminates this entirely.
