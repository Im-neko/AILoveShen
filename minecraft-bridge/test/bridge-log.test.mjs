import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, writeFileSync, existsSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { teeConsole, MAX_BYTES } from '../src/bridge-log.mjs'

const fakeConsole = () => {
  const seen = []
  return { seen, log: (...a) => seen.push(['log', ...a]), info () {}, warn: (...a) => seen.push(['warn', ...a]), error () {}, debug () {} }
}

test('console の出力を、端末へ出したまま、時刻つきでファイルにも追記する', () => {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'bridge-log-'))
  const file = path.join(dir, 'logs', 'bridge.log')
  const target = fakeConsole()
  const stop = teeConsole(target, file, () => new Date('2026-10-01T00:00:00Z'))
  target.log('[move] %s を掘っては置く', '55,27,-332')
  target.warn('遅い', { ms: 12 })
  assert.deepEqual(target.seen, [['log', '[move] %s を掘っては置く', '55,27,-332'], ['warn', '遅い', { ms: 12 }]])
  assert.equal(
    readFileSync(file, 'utf8'),
    '2026-10-01T00:00:00.000Z log [move] 55,27,-332 を掘っては置く\n2026-10-01T00:00:00.000Z warn 遅い { ms: 12 }\n'
  )
  stop()
  target.log('戻した後')
  assert.ok(!readFileSync(file, 'utf8').includes('戻した後'))
})

test('大きくなったファイルは 1 世代だけ残して新しく始める。空文字なら何もしない', () => {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'bridge-log-'))
  const file = path.join(dir, 'bridge.log')
  writeFileSync(file, 'x'.repeat(MAX_BYTES + 1))
  const target = fakeConsole()
  const stop = teeConsole(target, file)
  target.log('新しい')
  assert.ok(existsSync(`${file}.1`))
  assert.match(readFileSync(file, 'utf8'), /^\S+ log 新しい\n$/)
  stop()

  const off = fakeConsole()
  const original = off.log
  teeConsole(off, '')
  assert.equal(off.log, original)
})
