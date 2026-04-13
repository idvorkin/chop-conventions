#!/bin/sh
# Claude Code status line — mirrors Starship/default prompt style
# Reads JSON from stdin, outputs a single status line

input=$(cat)

cwd=$(echo "$input" | jq -r '.cwd // .workspace.current_dir // ""')
model=$(echo "$input" | jq -r '.model.display_name // ""')
used=$(echo "$input" | jq -r '.context_window.used_percentage // empty')
ctx_size=$(echo "$input" | jq -r '.context_window.context_window_size // empty')
cost_usd=$(echo "$input" | jq -r '.cost.total_cost_usd // empty')

user=$(whoami)
host=$(hostname -s)

# ANSI colors
YELLOW=$(printf '\033[33m')
GREEN=$(printf '\033[32m')
BLUE=$(printf '\033[34m')
RED=$(printf '\033[31m')
PINK=$(printf '\033[38;5;213m')
RESET=$(printf '\033[0m')

# Shorten home directory to ~
short_dir=$(echo "$cwd" | sed "s|^$HOME|~|")

# Git branch (skip optional locks, ignore errors)
branch=$(git -C "$cwd" --no-optional-locks symbolic-ref --short HEAD 2>/dev/null)

# Build the prompt segments
prompt="${user}@${host} ${YELLOW}${short_dir}${RESET}"
[ -n "$branch" ] && prompt="${prompt} ${YELLOW}[${branch}]${RESET}"
[ -n "$model" ] && prompt="${prompt} | ${model}"
if [ -n "$used" ] && [ -n "$ctx_size" ]; then
  pct=$(printf '%.0f' "$used")
  total_k=$((ctx_size / 1000))
  # Bucket used tokens to nearest 10k (round half up)
  used_k=$(awk "BEGIN {printf \"%d\", int(($used * $total_k / 100 + 5) / 10) * 10}")
  # Color by absolute used tokens: <200k green, <400k yellow, <600k pink, else red
  used_tokens=$(awk "BEGIN {printf \"%d\", $used * $ctx_size / 100}")
  if [ "$used_tokens" -lt 200000 ]; then
    ctx_color=$GREEN
  elif [ "$used_tokens" -lt 400000 ]; then
    ctx_color=$YELLOW
  elif [ "$used_tokens" -lt 600000 ]; then
    ctx_color=$PINK
  else
    ctx_color=$RED
  fi
  prompt="${prompt} ${ctx_color}ctx:${pct}% ${used_k}k/${total_k}k${RESET}"
fi
[ -n "$cost_usd" ] && prompt="${prompt} \$$(printf '%.2f' "$cost_usd")"

printf '%s' "$prompt"
