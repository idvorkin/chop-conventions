default:
    @just --list

fast-test:
    @python3 -c "from pathlib import Path; import subprocess, sys; test_dirs = sorted({str(path.parent) for path in Path('skills').rglob('test_*.py')}); sys.exit(0 if all(subprocess.run(['python3', '-m', 'unittest', 'discover', '-s', test_dir, '-p', 'test_*.py']).returncode == 0 for test_dir in test_dirs) else 1)"

test:
    @echo "All tests - Add comprehensive tests" 
