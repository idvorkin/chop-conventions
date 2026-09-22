#!/usr/bin/env bun
/**
 * Unit tests for server.ts's channel gate — the rule that keeps the Telegram
 * bridge from running in Claude sessions that never asked for the channel.
 *
 * server.ts is an executable script with top-level side effects, so it cannot
 * be imported here. Rather than copy the logic (and let the copy drift from
 * the thing that actually ships), this test EXTRACTS the marked pure block out
 * of server.ts at runtime, writes it to a temp module and imports that. The
 * code under test is therefore byte-identical to the deployed code, and the
 * test fails loudly if the markers ever go missing.
 *
 * Run:  bun skills/harden-telegram/server/tests/test_channel_gate.ts
 * Exit: 0 on pass, 1 on failure.
 */
import { readFileSync, writeFileSync, mkdtempSync, rmSync } from 'fs'
import { tmpdir } from 'os'
import { join, dirname } from 'path'

const SERVER_TS = join(dirname(import.meta.dir), 'server.ts')
const BEGIN = '// --- BEGIN channel-gate pure logic'
const END = '// --- END channel-gate pure logic ---'

// ---- Extract the shipped block --------------------------------------------

const source = readFileSync(SERVER_TS, 'utf8')
const startIdx = source.indexOf(BEGIN)
const endIdx = source.indexOf(END)
if (startIdx < 0 || endIdx < 0 || endIdx < startIdx) {
  console.error(
    `FAIL: could not find the channel-gate markers in ${SERVER_TS}. ` +
    `If the block moved, update BEGIN/END here — do not delete the test.`,
  )
  process.exit(1)
}
const block = source.slice(startIdx, endIdx)

const scratch = mkdtempSync(join(tmpdir(), 'channel-gate-'))
const modulePath = join(scratch, 'gate.ts')
writeFileSync(modulePath, block)

const gate = await import(modulePath)
const {
  parseProcStat,
  parseProcCmdline,
  looksLikeClaude,
  channelsFromArgv,
  subscribedToTelegram,
  findClaudeAncestry,
  channelGateDecision,
} = gate

// ---- Synthetic process tables ---------------------------------------------

type FakeProc = { comm: string; ppid: number; argv: string[] }

function makeReaders(table: Record<number, FakeProc | null>) {
  return {
    readStat: (pid: number) => {
      const p = table[pid]
      if (!p) return null
      // Real /proc/<pid>/stat shape: pid (comm) state ppid pgrp ...
      return `${pid} (${p.comm}) S ${p.ppid} 1 1 0 -1 4194304 100 0 0 0\n`
    },
    readArgv: (pid: number) => {
      const p = table[pid]
      if (!p) return null
      return p.argv.join('\0') + '\0'
    },
  }
}

const LARRY_ARGV = [
  'claude',
  '/startup-larry',
  '--add-dir',
  '/home/developer/larry-telegram',
  '--channels',
  'plugin:telegram@claude-plugins-official',
]

// The real shape on this box: bun server.ts <- bun run --cwd ... <- claude
function chain(claudeArgv: string[]): Record<number, FakeProc> {
  return {
    100: { comm: 'bun', ppid: 99, argv: ['/home/developer/.bun/bin/bun', 'server.ts'] },
    99: { comm: 'bun', ppid: 98, argv: ['bun', 'run', '--cwd', '/plugins/telegram', 'start'] },
    98: { comm: 'claude', ppid: 97, argv: claudeArgv },
    97: { comm: 'larry_start.sh', ppid: 1, argv: ['/bin/bash', './larry_start.sh'] },
  }
}

// ---- Assertions ------------------------------------------------------------

let failures = 0

function check(label: string, actual: unknown, expected: unknown): void {
  const a = JSON.stringify(actual)
  const e = JSON.stringify(expected)
  if (a === e) {
    console.log(`  ok: ${label}`)
  } else {
    console.error(`FAIL: ${label}\n      expected ${e}\n      got      ${a}`)
    failures++
  }
}

function decide(table: Record<number, FakeProc | null>, force?: string) {
  return channelGateDecision(findClaudeAncestry(100, makeReaders(table)), force)
}

console.log('/proc parsing:')
check(
  'stat with a parenthesised multi-word comm',
  parseProcStat('3943007 (bun run --cwd (x)) S 3942897 3660667 1 0 -1 4194304'),
  { comm: 'bun run --cwd (x)', ppid: 3942897 },
)
check('stat that is not stat-shaped', parseProcStat('garbage'), null)
check('cmdline splits on NULs and drops the terminator', parseProcCmdline('claude\0-p\0say ok\0'), [
  'claude',
  '-p',
  'say ok',
])
check('empty cmdline (kernel thread)', parseProcCmdline(''), [])

console.log('claude detection:')
check('comm=claude', looksLikeClaude('claude', '/usr/bin/whatever'), true)
check('comm truncated to claude-code', looksLikeClaude('claude-code', ''), true)
check('argv[0] basename claude', looksLikeClaude('node', '/home/x/.local/bin/claude'), true)
check('unrelated process', looksLikeClaude('bun', '/home/x/.bun/bin/bun'), false)

console.log('--channels parsing:')
check('space-separated value', channelsFromArgv(['claude', '--channels', 'plugin:telegram@m']), [
  'plugin:telegram@m',
])
check('equals form', channelsFromArgv(['claude', '--channels=plugin:telegram@m']), [
  'plugin:telegram@m',
])
check('comma list', channelsFromArgv(['claude', '--channels', 'slack,telegram']), ['slack', 'telegram'])
check('variadic form', channelsFromArgv(['claude', '--channels', 'slack', 'telegram', '--verbose']), [
  'slack',
  'telegram',
])
check('singular alias', channelsFromArgv(['claude', '--channel=telegram']), ['telegram'])
check('dangling flag at end of argv', channelsFromArgv(['claude', '--channels']), [])
check('no flag at all', channelsFromArgv(['claude', '-p', 'say ok']), [])
check('telegram subscription, positive', subscribedToTelegram(LARRY_ARGV), true)
check('telegram subscription, negative', subscribedToTelegram(['claude', '--channels', 'slack']), false)

console.log('gate decisions:')
check('parent claude WITH --channels telegram → allow', decide(chain(LARRY_ARGV)), {
  allow: true,
  reason: 'claude pid=98 was started with --channels telegram',
})
check('parent claude WITHOUT --channels → deny', decide(chain(['claude', '-p', 'say ok'])), {
  allow: false,
  reason: 'claude pid=98 was started with no --channels flag',
})
check('parent claude with a different channel → deny', decide(chain(['claude', '--channels', 'slack'])), {
  allow: false,
  reason: 'claude pid=98 was started with --channels slack',
})
check('no claude anywhere in the chain → deny', decide({
  100: { comm: 'bun', ppid: 99, argv: ['bun', 'server.ts'] },
  99: { comm: 'zsh', ppid: 1, argv: ['zsh'] },
}), { allow: false, reason: 'no claude ancestor process' })
check('cycle in the ppid chain → deny, no hang', decide({
  100: { comm: 'bun', ppid: 99, argv: ['bun', 'server.ts'] },
  99: { comm: 'bun', ppid: 100, argv: ['bun', 'run'] },
}), { allow: false, reason: 'no claude ancestor process' })

console.log('FORCE override and fail-open:')
for (const force of ['1', 'true', 'YES', ' on ']) {
  check(`TELEGRAM_BRIDGE_FORCE=${JSON.stringify(force)} overrides a deny`, decide(chain(['claude', '-p', 'x']), force), {
    allow: true,
    reason: 'TELEGRAM_BRIDGE_FORCE is set',
  })
}
check('TELEGRAM_BRIDGE_FORCE=0 does not override', decide(chain(['claude', '-p', 'x']), '0').allow, false)
check('TELEGRAM_BRIDGE_FORCE="" does not override', decide(chain(['claude', '-p', 'x']), '').allow, false)
check('no procfs at all → fail open', decide({}), {
  allow: true,
  reason: 'process ancestry unreadable (/proc/100/stat unreadable) — gate not enforced',
})
check('ancestor vanished mid-walk → fail open', decide({
  100: { comm: 'bun', ppid: 99, argv: ['bun', 'server.ts'] },
  99: null,
}).allow, true)

// ---- Cleanup ---------------------------------------------------------------
try {
  rmSync(scratch, { recursive: true, force: true })
} catch {}

if (failures > 0) {
  console.error(`\n${failures} assertion(s) failed`)
  process.exit(1)
}
console.log('\nall channel-gate tests passed')
