#!/usr/bin/env bash
# One watcher for every agent the manager drives. Emits one line whenever an agent needs the manager:
#   BLOCKED <name>: …   it waits on an approval or a question (the last visible lines follow)
#   FAILED <name>: …    its model call failed ("model failed", "transport error"): Herdr still says "working"
#   STALLED <name>: …   "working" but the screen has not changed for STALL_SECS (a hung turn)
#   <name> idle|done    it settled
# Usage: bash watch-agents.sh [name …]   (no names: every live Muse from `herdr agent list`)
# Run it under a Monitor. Nothing here types keys: the manager reads the prompt and answers with
# `herdr agent send-keys <name> enter` (or what the prompt wants), or re-prompts a FAILED agent.
set -uo pipefail
STALL_SECS=${STALL_SECS:-300}
KIND=${KIND:-muse}
NAMES=("$@")
declare -A last screen_prev same_since
while true; do
  if [ ${#NAMES[@]} -eq 0 ]; then
    mapfile -t agents < <(herdr agent list 2>/dev/null | jq -r --arg k "$KIND" '.result.agents[] | select(.agent==$k and .name != null) | .name')
  else
    agents=("${NAMES[@]}")
  fi
  now=$(date +%s)
  for a in "${agents[@]}"; do
    state=$(herdr agent get "$a" 2>/dev/null | jq -r '.result.agent.agent_status // "gone"')
    screen=$(herdr agent read "$a" --source visible --lines 40 2>/dev/null | grep -vE '^\s*$|Voice input|^──' | tail -8 | tr '\n' ' ')
    if [ "$screen" != "${screen_prev[$a]:-}" ]; then screen_prev[$a]=$screen; same_since[$a]=$now; fi
    case "$state" in
      blocked)
        [ "${last[$a]:-}" != "blocked:$screen" ] && { last[$a]="blocked:$screen"; echo "BLOCKED $a: ${screen:0:400}"; } ;;
      working)
        if printf '%s' "$screen" | grep -qE "model failed|transport error|rate limit|Interrupted ·"; then
          [ "${last[$a]:-}" != "failed:$screen" ] && { last[$a]="failed:$screen"; echo "FAILED $a (re-prompt it): ${screen:0:300}"; }
        elif [ $((now - ${same_since[$a]:-$now})) -ge "$STALL_SECS" ]; then
          [ "${last[$a]:-}" != "stalled:$screen" ] && { last[$a]="stalled:$screen"; echo "STALLED $a (no screen change ${STALL_SECS}s): ${screen:0:300}"; }
        fi ;;
      idle|done)
        [ "${last[$a]:-}" != "settled:$state" ] && { last[$a]="settled:$state"; echo "$a $state"; } ;;
      gone)
        [ "${last[$a]:-}" != "gone" ] && { last[$a]="gone"; echo "$a gone"; } ;;
    esac
  done
  sleep 20
done
