#!/usr/bin/env bun
/**
 * Small bun-native unit test for server.ts `assertSendable` guard.
 *
 * server.ts is an executable script with top-level MCP side effects, so we
 * can't cleanly `import { assertSendable }` here. Instead this file ports the
 * same guard into a pure function and exercises the block/allow contract the
 * spec requires:
 *
 *   BLOCK: ~/.claude/channels/telegram/   (credential store — .env + access.json)
 *   ALLOW: <base>/attachments/             (echo/re-send of inbound files)
 *   ALLOW: any other path                  (outside both server-owned trees)
 *
 * Run:  bun skills/harden-telegram/server/tests/test_assert_sendable.ts
 * Exit: 0 on pass, 1 on failure.
 */
import { mkdirSync, writeFileSync, realpathSync, rmSync, symlinkSync } from 'fs'
import { tmpdir } from 'os'
import { join, sep } from 'path'

// ---- Pure copy of server.ts assertSendable with injected dirs -------------

function makeAssertSendable(STATE_DIR: string, ATTACHMENTS_DIR: string) {
  return function assertSendable(f: string): void {
    let real: string
    try {
      real = realpathSync(f)
    } catch { return }

    try {
      const stateReal = realpathSync(STATE_DIR)
      if (real === stateReal || real.startsWith(stateReal + sep)) {
        throw new Error(`refusing to send credential store file: ${f}`)
      }
    } catch (err) {
      if (err instanceof Error && err.message.startsWith('refusing to send')) throw err
    }

    try {
      const attachmentsReal = realpathSync(ATTACHMENTS_DIR)
      if (real === attachmentsReal || real.startsWith(attachmentsReal + sep)) return
    } catch {}

    // Implicit allow for anything else.
  }
}

// ---- Fixtures --------------------------------------------------------------

const root = join(tmpdir(), `assert-sendable-${Date.now()}-${process.pid}`)
const stateDir = join(root, 'state')
const baseDir = join(root, 'base')
const attachmentsDir = join(baseDir, 'attachments')
const elsewhere = join(root, 'elsewhere')

mkdirSync(stateDir, { recursive: true })
mkdirSync(attachmentsDir, { recursive: true })
mkdirSync(elsewhere, { recursive: true })

const envFile = join(stateDir, '.env')
const accessFile = join(stateDir, 'access.json')
const photo = join(attachmentsDir, 'photo.jpg')
const someFile = join(elsewhere, 'hello.txt')

writeFileSync(envFile, 'TELEGRAM_BOT_TOKEN=fake\n')
writeFileSync(accessFile, '{}')
writeFileSync(photo, 'fake-jpeg-bytes')
writeFileSync(someFile, 'hello')

// Symlink under attachments/ that points into stateDir — the realpath check
// should still reject it.
const trapLink = join(attachmentsDir, 'trap')
symlinkSync(stateDir, trapLink)

// ---- Assertions ------------------------------------------------------------

const assertSendable = makeAssertSendable(stateDir, attachmentsDir)
let failures = 0

function expectThrow(path: string, label: string): void {
  try {
    assertSendable(path)
    console.error(`FAIL: ${label} — expected throw but got pass (${path})`)
    failures++
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    if (!msg.startsWith('refusing to send')) {
      console.error(`FAIL: ${label} — wrong error: ${msg}`)
      failures++
    } else {
      console.log(`  ok: ${label}`)
    }
  }
}

function expectPass(path: string, label: string): void {
  try {
    assertSendable(path)
    console.log(`  ok: ${label}`)
  } catch (err) {
    console.error(`FAIL: ${label} — expected pass but threw: ${err}`)
    failures++
  }
}

console.log('assertSendable block checks:')
expectThrow(envFile, '.env in STATE_DIR is blocked')
expectThrow(accessFile, 'access.json in STATE_DIR is blocked')
expectThrow(join(trapLink, '.env'), 'symlink attachments/trap/.env into STATE_DIR is blocked')

console.log('assertSendable allow checks:')
expectPass(photo, 'attachments/photo.jpg is allowed')
expectPass(someFile, 'arbitrary path outside both trees is allowed')
expectPass('/etc/hostname', '/etc/hostname is allowed (not in either tree)')

// ---- Cleanup ---------------------------------------------------------------
try {
  rmSync(root, { recursive: true, force: true })
} catch {}

if (failures > 0) {
  console.error(`\n${failures} assertion(s) failed`)
  process.exit(1)
}
console.log('\nall assertSendable tests passed')
