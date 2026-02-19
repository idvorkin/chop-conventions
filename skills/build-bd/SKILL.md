# Build bd (Beads CLI)

Install or upgrade the `bd` CLI tool with a fully static build to avoid shared library issues (e.g., ICU version mismatches across machines).

## Usage

```text
/build-bd
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

3. If already on the latest version, report that and stop. Otherwise, proceed to build.

4. Build the latest version statically with CGO disabled (use the version from step 2):

   ```bash
   CGO_ENABLED=0 go install github.com/steveyegge/beads/cmd/bd@latest
   ```

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
