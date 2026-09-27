#!/bin/bash
# Sleep catcher: records why the Mac went to sleep, and for a forced sleep
# (`pmset sleepnow`, an app calling IOPMSleepSystem) which process asked.
#
# `caffeinate` only blocks *idle* sleep, so a forced sleep gets through it.
# powerd logs the requester only as "Software Sleep pid=N", and by the time
# anyone looks that process is long gone. So: the instant powerd announces
# kIOMessageSystemWillSleep (~25ms after the request, seconds before the
# actual sleep), snapshot the process table. When the sleep reason arrives,
# resolve pid N against that snapshot and log its parent chain.
#
# Maintenance sleeps (dark-wake network chatter, every minute or so overnight)
# are dropped; every other sleep reason is logged - idle, thermal, forced.
# `machine_doctor.py sleeps` folds these catches into its report.
#
# Usage:
#   sleep-catcher.sh watch               Catch sleeps as they happen
#   sleep-catcher.sh replay FROM [TO]    Run past powerd events through the
#                                        same logic (reasons only; the process
#                                        table is gone by now)
#   sleep-catcher.sh install             Install/reload the launchd agent
#   sleep-catcher.sh uninstall           Remove the launchd agent
#   sleep-catcher.sh status              Agent state and recent catches

set -uo pipefail

# launchd runs with a minimal PATH
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"

PREDICATE='process == "powerd" AND (eventMessage BEGINSWITH "Received kIOMessageSystemWillSleep" OR eventMessage CONTAINS "Entering Sleep state due to")'

LABEL="local.machine-doctor.sleep-catcher"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/Library/Logs/sleep-catcher"
LOG="$LOG_DIR/sleep-catcher.log"
KEEP_DUMPS=30
# launchd runs a copy from the internal disk: agents may not exec scripts on
# external volumes without a TCC grant (exit 126, "Operation not permitted"),
# and a checkout or worktree the agent points into can move or be deleted.
INSTALLED="$HOME/.local/share/machine-doctor/sleep-catcher.sh"
SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"

pending_dump="" # snapshot taken at WillSleep, awaiting its sleep reason

log() {
    mkdir -p "$LOG_DIR"
    echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >>"$LOG"
}

snapshot() {
    local dump
    dump="$LOG_DIR/ps-$(date '+%Y%m%d-%H%M%S').txt"
    mkdir -p "$LOG_DIR"
    ps -Ao pid=,ppid=,etime=,command= >"$dump" 2>/dev/null
    echo "$dump"
}

# Parent chain of pid $1 from snapshot $2, one "pid etime command" per hop
ancestry() {
    awk -v pid="$1" '
        { ppid[$1] = $2; line[$1] = $0 }
        END {
            for (hops = 0; pid != "" && pid != "0" && hops < 20; hops++) {
                if (!(pid in line)) { if (hops == 0) exit 1; break }
                printf "    %s\n", substr(line[pid], 1, 200)
                pid = ppid[pid]
            }
        }' "$2"
}

# Processes that started within the last 10s of the snapshot: when the
# requester exited before the snapshot, its siblings/parent are usually here
recent_procs() {
    awk '$3 ~ /^00:0[0-9]$/ { printf "    %s\n", substr($0, 1, 200) }' "$1"
}

prune_dumps() {
    # shellcheck disable=SC2012 # filenames are ours, no odd characters
    ls -1t "$LOG_DIR"/ps-*.txt 2>/dev/null | tail -n +$((KEEP_DUMPS + 1)) | xargs rm -f
}

handle() {
    local line="$1" live="$2" reason pid
    if [[ "$line" == *"Received kIOMessageSystemWillSleep"* ]]; then
        [[ "$live" == 1 ]] && pending_dump=$(snapshot)
        return
    fi
    [[ "$line" =~ Entering\ Sleep\ state\ due\ to\ \'([^\']*)\' ]] || return
    reason="${BASH_REMATCH[1]}"

    if [[ "$reason" == "Maintenance Sleep" ]]; then
        [[ -n "$pending_dump" ]] && rm -f "$pending_dump"
        pending_dump=""
        return
    fi

    local when="${line:0:19}"
    if [[ "$reason" =~ Software\ Sleep\ pid=([0-9]+) ]]; then
        pid="${BASH_REMATCH[1]}"
        log "FORCED sleep ($reason) at $when"
        if [[ -z "$pending_dump" ]]; then
            log "  no process snapshot (replay, or WillSleep was missed)"
        elif chain=$(ancestry "$pid" "$pending_dump"); then
            log "  requester $pid and its parents:"$'\n'"$chain"
        else
            log "  pid $pid had already exited; processes started <10s before:"$'\n'"$(recent_procs "$pending_dump")"
        fi
        [[ -n "$pending_dump" ]] && log "  full snapshot: $pending_dump"
        # Whatever the requester logged under its pid names its binary
        local who since
        since=$(date -j -v-30S -f '%Y-%m-%d %H:%M:%S' "$when" '+%Y-%m-%d %H:%M:%S' 2>/dev/null)
        who=$(/usr/bin/log show --start "$since" --end "$when" --style compact --predicate "processID == $pid" 2>/dev/null |
            awk 'NR > 1 { print $4; exit }')
        [[ -n "$who" ]] && log "  unified log names pid $pid as: $who"
    else
        log "sleep ($reason) at $when"
        [[ -n "$pending_dump" ]] && rm -f "$pending_dump"
    fi
    pending_dump=""
    prune_dumps
}

watch() {
    log "watch: started (pid $$)"
    # `log` is a zsh builtin, so always call the binary by path
    /usr/bin/log stream --style compact --predicate "$PREDICATE" |
        while read -r line; do
            # Only timestamped event lines; the header echoes the predicate text
            [[ "$line" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2} ]] || continue
            handle "$line" 1
        done
    log "watch: log stream exited"
}

replay() {
    local from="${1:?usage: replay FROM [TO], e.g. replay '2026-09-26 21:00'}"
    local to="${2:-$(date '+%Y-%m-%d %H:%M:%S')}"
    # `log show` rejects times without seconds
    [[ "$from" =~ [0-9]{2}:[0-9]{2}$ && ! "$from" =~ :[0-9]{2}:[0-9]{2}$ ]] && from+=":00"
    [[ "$to" =~ [0-9]{2}:[0-9]{2}$ && ! "$to" =~ :[0-9]{2}:[0-9]{2}$ ]] && to+=":00"
    log() { echo "$*"; } # print instead of appending to the catch log
    /usr/bin/log show --start "$from" --end "$to" --style compact --predicate "$PREDICATE" 2>/dev/null |
        while read -r line; do
            [[ "$line" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2} ]] || continue
            handle "$line" 0
        done
}

install() {
    mkdir -p "$(dirname "$PLIST")" "$(dirname "$INSTALLED")"
    uninstall
    if [[ "$SCRIPT" != "$INSTALLED" ]]; then
        cp "$SCRIPT" "$INSTALLED" && chmod +x "$INSTALLED"
    fi
    cat >"$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$LABEL</string>
    <key>ProgramArguments</key>
    <array><string>$INSTALLED</string><string>watch</string></array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
EOF
    mkdir -p "$LOG_DIR"
    if ! launchctl bootstrap "gui/$(id -u)" "$PLIST"; then
        echo "launchctl bootstrap failed for $PLIST" >&2
        return 1
    fi
    echo "Installed $LABEL (log: $LOG)"
}

uninstall() {
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null
    rm -f "$PLIST"
}

status() {
    launchctl list | grep "$LABEL" || echo "agent not loaded"
    [[ -f "$LOG" ]] && tail -n 20 "$LOG"
}

case "${1:-}" in
watch) watch ;;
replay) shift; replay "$@" ;;
install) install ;;
uninstall) uninstall ;;
status) status ;;
*)
    sed -n '2,22p' "$SCRIPT"
    exit 1
    ;;
esac
