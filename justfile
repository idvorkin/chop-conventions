default:
    @just --list

fast-test:
    @python3 -c "from pathlib import Path; import subprocess, sys; test_dirs = sorted({str(path.parent) for path in Path('skills').rglob('test_*.py')}); sys.exit(0 if all(subprocess.run(['python3', '-m', 'unittest', 'discover', '-s', test_dir, '-p', 'test_*.py']).returncode == 0 for test_dir in test_dirs) else 1)"

test:
    @echo "All tests - Add comprehensive tests"

# Install chop-conventions skill CLIs as symlinks in ~/.local/bin
install-tools:
    ./install-tools.py

# Preview install-tools changes without writing anything
install-tools-dry-run:
    ./install-tools.py --dry-run

# Remove symlinks in ~/.local/bin that resolve into this repo
uninstall-tools:
    ./install-tools.py --uninstall

# One-time: point git at repo-versioned hooks + run initial install-tools
bootstrap:
    git config core.hooksPath githooks
    ./install-tools.py
