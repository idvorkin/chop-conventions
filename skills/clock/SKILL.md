---
name: clock
description: Schedule recurring tasks in your session. Defaults to time check every 15 min.
allowed-tools: Bash, CronCreate, CronList, CronDelete, Skill
---

# Clock — Session Scheduler

Schedule a recurring task in your session. Reports the current time, then sets up a CronCreate loop.

## Usage

```
/clock                          # default: time every 15 min
/clock 15m                      # time every 15 min (explicit)
/clock 4h /background-usage     # run /background-usage every 4 hours
/clock 30m remind me to stretch # custom reminder every 30 min
/clock 1h time                  # time every hour
```

**Arguments:**

- **interval** (optional): Duration like `15m`, `30m`, `1h`, `4h`. Default: `15m`
- **action** (optional): What to do on each tick. Default: `time`
  - `time` — report current time
  - `/skill-name` — invoke a skill (e.g., `/background-usage`)
  - any other text — use as a reminder message

**Supported intervals:** `15m`, `20m`, `30m`, `1h`, `2h`, `4h`. Other values are not supported — ask the user to pick a supported interval.

## Steps

### 1. Parse arguments

Extract interval and action from the arguments. If no arguments, use `15m` and `time`.

### 2. Report current time and execute action immediately

Always report the current time first:

```bash
TZ='America/Los_Angeles' date '+%I:%M %p %Z (%A, %B %d, %Y)'
```

Tell the user conversationally:

> It's 3:45 PM PDT, Monday Mar 17.

Take the zone abbreviation from the command's `%Z` output (`PST` or `PDT`) — never hardcode it.

Then execute the action right now (this IS the first tick — no separate one-shot needed):

- If action is `time`: you just reported it, done.
- If action is `/skill-name`: invoke the skill now.
- If action is custom text: show the reminder now.

### 3. Convert interval to cron expression

| Interval | Cron expression      | Notes                           |
| -------- | -------------------- | ------------------------------- |
| `15m`    | `3,18,33,48 * * * *` | At :03, :18, :33, :48 each hour |
| `20m`    | `7,27,47 * * * *`    | At :07, :27, :47 each hour      |
| `30m`    | `7,37 * * * *`       | At :07, :37 each hour           |
| `1h`     | `57 * * * *`         | At :57 each hour                |
| `2h`     | `57 */2 * * *`       | Every 2 hours at :57            |
| `4h`     | `57 */4 * * *`       | Every 4 hours at :57            |

### 4. Build the cron prompt

Start every prompt with the marker `[clock:<action>]`, where `<action>` is `time`, the `/skill-name`, or the reminder text. Step 5 uses the marker to find this clock's earlier jobs. Each prompt below starts with it.

**If action is `time`:**

```
[clock:time] Report the current time to the user. Run: TZ='America/Los_Angeles' date '+%I:%M %p %Z (%A, %B %d, %Y)' — then tell them the time in one line, e.g. "Clock: It's 4:48 PM PDT, Monday Mar 17." — use the zone abbreviation the command printed (PST or PDT), never a fixed one. Keep it to one line.
```

**If action is a `/skill-name`:**

```
[clock:/<skill-name>] Run the <skill-name> skill now. Use a background Agent subagent (run_in_background: true) so it doesn't block the main conversation.
```

**If action is custom text:**

```
[clock:<the text>] Reminder: <the text>. Also report the current time (run: TZ='America/Los_Angeles' date '+%I:%M %p %Z').
```

### 5. Replace earlier jobs, then create the recurring cron job

Call `CronList` first. `CronDelete` every job whose prompt starts with this action's marker `[clock:<action>]` — the old recurring job and its pending renewal. Otherwise each re-run (including every self-renewal) stacks another schedule that renews itself forever. Also delete unmarked jobs an older `/clock` created for the same action (same prompt text, no marker). Leave jobs with a different marker alone: they are other clocks the user still wants.

Then create the new job:

- **cron**: (from step 3)
- **recurring**: true
- **prompt**: (from step 4)

### 6. Set up self-renewal

CronCreate jobs auto-expire after 3 days. Create a one-shot renewal that fires ~70 hours from now and re-invokes `/clock` with the same arguments.

Use `date` to calculate the renewal time reliably. CronCreate reads cron fields in the host's **local** timezone, so compute it in local time — no `-u`, no `TZ=` override (a UTC time would fire hours late, past the expiry, on a host west of UTC). GNU vs BSD `date` differ — use whichever works on the host:

```bash
# Linux / GNU coreutils
date -d '+70 hours' '+%M %H %d %m *'

# macOS / BSD
date -v +70H '+%M %H %d %m *'
```

Use that output directly as the cron expression.

- **cron**: (output of the date command above)
- **recurring**: false
- **prompt**: `[clock:<action>] The clock cron job is about to expire. Re-invoke: /clock <original arguments>. Run the skill now.`

### 7. Confirm

> Scheduled: **<action>** every **<interval>**. Auto-renews before the 3-day expiry.

## Notes

- Re-running `/clock` for the same action replaces that action's jobs (step 5), so a new interval takes over cleanly. A different action is a separate clock and runs alongside.
- To stop a clock: `CronList`, then `CronDelete` the jobs carrying its `[clock:<action>]` marker.
- All cron minutes are offset to avoid :00/:15/:30/:45 congestion marks.
- Session-only: all cron jobs die when Claude exits.
- For `/skill-name` actions, the skill must be available in the session.
