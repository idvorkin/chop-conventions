# lib.sh — shared helpers for scenario scripts
# Sourced by each NN-*.sh scenario. Keeps each scenario standalone:
# reset_run_dir wipes its own runs/ subdir so re-runs are idempotent.

set -euo pipefail

SCENARIOS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNS_DIR="$(cd "$SCENARIOS_DIR/.." && pwd)/runs"

section() {
    echo ""
    echo "================================================================"
    echo "  $*"
    echo "================================================================"
}

step() {
    echo ""
    echo "---- $* ----"
}

run() {
    echo "\$ $*"
    "$@"
}

reset_run_dir() {
    local name="$1"
    local dir="$RUNS_DIR/$name"
    rm -rf "$dir"
    mkdir -p "$dir"
    echo "$dir"
}
