#!/usr/bin/env bash
# Emits one line whenever a Herdr agent becomes blocked (an approval or a question), with the last visible lines,
# and one line when it settles (idle/done). Run it under a Monitor: `bash watch-blocked.sh <agent name>`.
# The caller reads the prompt and answers with `herdr agent send-keys <name> y` (or whatever the prompt wants);
# nothing here types keys on its own (an auto-approver once typed a stray `y` into Muse's input).
set -uo pipefail
AGENT=$1
last=""
while true; do
  state=$(herdr agent get "$AGENT" 2>/dev/null | jq -r '.result.agent.agent_status // "gone"')
  case "$state" in
    blocked)
      screen=$(herdr agent read "$AGENT" --source visible --lines 40 2>/dev/null | grep -vE '^\s*$' | tail -8 | tr '\n' ' ')
      [ "$screen" != "$last" ] && { last=$screen; echo "BLOCKED $AGENT: ${screen:0:400}"; } ;;
    idle|done)
      [ "$last" != "settled:$state" ] && { last="settled:$state"; echo "$AGENT $state"; } ;;
    gone)
      echo "$AGENT gone"; exit 0 ;;
  esac
  sleep 10
done
