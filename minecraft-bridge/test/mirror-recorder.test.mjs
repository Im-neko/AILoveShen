import { test } from 'node:test'
import assert from 'node:assert/strict'
import { WorldRecorder, LOG_LIMIT } from '../src/mirror.mjs'

const play = (name) => ({ name, state: 'play' })
const buf = (n = 1) => Buffer.alloc(n)

test('記録のログは上限を超えたら古い方から捨てる。ボットの持ち物（ウィンドウ 0）は記録しない', () => {
  const rec = new WorldRecorder()
  for (let i = 0; i < LOG_LIMIT + 50; i++) rec.record({}, play('player_info'), buf())
  assert.equal(rec.log.length, LOG_LIMIT)
  rec.record({ windowId: 0, slot: 5 }, play('set_slot'), buf())
  rec.record({ windowId: 0 }, play('window_items'), buf())
  assert.equal(rec.log.length, LOG_LIMIT)
  rec.record({ windowId: 3, slot: 5 }, play('set_slot'), buf()) // チェストの画面は残す
  assert.equal(rec.log.length, LOG_LIMIT)
  assert.ok(rec.log.at(-1))
})

test('チャンクのブロックの変化は、位置ごとに最後の 1 つだけ覚える（掘って置いてを何度繰り返しても増えない）', () => {
  const rec = new WorldRecorder()
  rec.record({ x: 0, z: 0 }, play('map_chunk'), buf())
  for (let i = 0; i < 1000; i++) rec.record({ location: { x: 3, y: 70, z: 4 }, type: i % 2 }, play('block_change'), buf())
  const changes = rec.chunks.get('0,0').changes
  assert.equal(changes.size, 1)
  assert.deepEqual(changes.get('3,70,4'), { x: 3, y: 70, z: 4, type: 1 })
  // まとめた変化: 区画 (1, 4, -1)、レコード = 状態 << 12 | x << 8 | z << 4 | y
  const record = (state, x, y, z) => (state << 12) | (x << 8) | (z << 4) | y
  rec.record({ x: 1, z: -1 }, play('map_chunk'), buf())
  rec.record({ chunkCoordinates: { x: 1, y: 4, z: -1 }, records: [record(77, 2, 5, 9), record(0, 15, 0, 0)] }, play('multi_block_change'), buf())
  const multi = rec.chunks.get('1,-1').changes
  assert.deepEqual(multi.get(`${16 + 2},${64 + 5},${-16 + 9}`), { x: 18, y: 69, z: -7, type: 77 })
  assert.equal(multi.size, 2)
  // 読み込まれていないチャンクの変化は覚えない
  rec.record({ location: { x: 999, y: 1, z: 999 }, type: 1 }, play('block_change'), buf())
  assert.equal(rec.chunks.size, 2)
})
