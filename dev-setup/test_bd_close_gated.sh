#!/usr/bin/env bash
# Manual verification harness for dev-setup/bd-close-gated.
#
# Creates a throwaway beads DB in a tempdir, exercises three scenarios,
# and tears down on exit. Run with: bash dev-setup/test_bd_close_gated.sh
#
# Requires: bd, jq, git.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WRAPPER="$SCRIPT_DIR/bd-close-gated"

[[ -x "$WRAPPER" ]] || { echo "FAIL: $WRAPPER not executable"; exit 2; }
command -v bd >/dev/null || { echo "FAIL: bd not in PATH"; exit 2; }
command -v jq >/dev/null || { echo "FAIL: jq not in PATH"; exit 2; }

TMPDIR="$(mktemp -d -t bd-close-gated-test.XXXXXX)"
cleanup() { rm -rf "$TMPDIR"; }
trap cleanup EXIT

cd "$TMPDIR"
# bd init expects a git repo; the --non-interactive path will create one.
export BD_NON_INTERACTIVE=1
bd init --prefix=tst >/dev/null 2>&1

pass=0
fail=0

check() {
  # check <label> <expected-exit> <actual-exit>
  if [[ "$2" -eq "$3" ]]; then
    echo "  PASS: $1 (exit=$3)"
    pass=$((pass + 1))
  else
    echo "  FAIL: $1 (expected exit=$2, got $3)"
    fail=$((fail + 1))
  fi
}

# ---------------------------------------------------------------------------
echo "[1] Happy path — unblocked bead closes cleanly"
solo_id="$(bd create "solo bead" -t task --json | jq -r '.id')"
set +e
"$WRAPPER" "$solo_id" --reason "test solo close" >/dev/null
rc=$?
set -e
check "solo close returns 0" 0 "$rc"
status="$(bd show "$solo_id" --json | jq -r '.[0].status')"
[[ "$status" == "closed" ]] && echo "  PASS: solo bead marked closed" || { echo "  FAIL: solo bead status=$status"; fail=$((fail + 1)); }

# ---------------------------------------------------------------------------
echo "[2] Blocked path — parent with open child refuses to close"
parent_id="$(bd create "parent" -t task --json | jq -r '.id')"
child_id="$(bd create "child" -t task --parent "$parent_id" --json | jq -r '.id')"
set +e
out="$("$WRAPPER" "$parent_id" --reason "try close" 2>&1)"
rc=$?
set -e
check "blocked close returns 1" 1 "$rc"
if grep -q "$child_id" <<<"$out"; then
  echo "  PASS: error names open child $child_id"
  pass=$((pass + 1))
else
  echo "  FAIL: error did not mention $child_id"
  echo "  --- output ---"; echo "$out"; echo "  --------------"
  fail=$((fail + 1))
fi
status="$(bd show "$parent_id" --json | jq -r '.[0].status')"
[[ "$status" == "open" ]] && echo "  PASS: parent still open after refused close" || { echo "  FAIL: parent status=$status"; fail=$((fail + 1)); }

# ---------------------------------------------------------------------------
echo "[3] Force path — --force bypasses the gate"
set +e
"$WRAPPER" "$parent_id" --force --reason "force close" >/dev/null
rc=$?
set -e
check "force close returns 0" 0 "$rc"
status="$(bd show "$parent_id" --json | jq -r '.[0].status')"
[[ "$status" == "closed" ]] && echo "  PASS: parent closed via --force" || { echo "  FAIL: parent status=$status"; fail=$((fail + 1)); }

# ---------------------------------------------------------------------------
echo "[4] Usage error — missing bead-id returns 2"
set +e
"$WRAPPER" >/dev/null 2>&1
rc=$?
set -e
check "missing-arg returns 2" 2 "$rc"

# ---------------------------------------------------------------------------
echo
echo "Results: $pass passed, $fail failed"
[[ "$fail" -eq 0 ]]
