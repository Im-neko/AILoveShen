import { test } from 'node:test'
import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import vec3Pkg from 'vec3'
import { collectNearbyDrops } from '../src/primitives.mjs'

const { Vec3 } = vec3Pkg
const signal = new AbortController().signal

// 足元 (0, 64, 0) に立つボット。落とし物は上から落ちてきて拾われる（拾われたら playerCollect と持ち物）
function scene (dropAt) {
  const bot = new EventEmitter()
  bot.entity = { position: new Vec3(0, 64, 0) }
  bot.entities = { 1: bot.entity, 7: { id: 7, name: 'item', position: dropAt } }
  bot.waitForTicks = async () => {}
  return bot
}

test('4 ブロック以上高い所のドロップは、追わずに足元へ落ちてくるのを待つ（拾えなかったと失敗にしない）', async () => {
  const bot = scene(new Vec3(0.2, 68.3, 0)) // 4.3 上: nearbyDrops の範囲（±4）の外
  setTimeout(() => { delete bot.entities[7]; bot.emit('playerCollect', bot.entity, {}) }, 30)
  const t0 = Date.now()
  const why = await collectNearbyDrops(bot, { unreachableDrops: new Set() }, signal)
  assert.equal(why, '')
  assert.ok(Date.now() - t0 < 1000, '拾えたらすぐ返る（待ちきらない）')
  assert.equal(bot.entities[7], undefined)
})

test('落ちてこない（動かない）ドロップは、待つのを 2 回で切り上げる', async () => {
  const bot = scene(new Vec3(0.2, 68.3, 0))
  const original = setTimeout
  const waits = []
  globalThis.setTimeout = (fn, ms, ...rest) => { waits.push(ms); return original(fn, 0, ...rest) } // 待ちを縮める
  try {
    await collectNearbyDrops(bot, { unreachableDrops: new Set() }, signal)
  } finally { globalThis.setTimeout = original }
  assert.equal(waits.filter((ms) => ms === 1500).length, 2)
})
