import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { watchDrop, look, LOOKS_MS } from '../src/dropwatch.mjs'

const { Vec3 } = vec3Pkg
const v = (x, y, z) => new Vec3(x, y, z)

function fixture () {
  const timers = []
  const written = []
  const bot = {
    entity: { position: v(0.5, 64, 0.5), onGround: true },
    entities: {},
    blockAt: (p) => ({ name: p.y < 64 ? 'dirt' : 'air' }),
    pathfinder: { isBuilding: () => false }
  }
  bot.entities[1] = { id: 1, name: 'item', position: v(3.5, 60, 0.5), onGround: true }
  bot.entities[2] = { id: 2, name: 'zombie', position: v(1.5, 64, 0.5) }
  bot.entities[3] = { id: 3, name: 'item', position: v(40, 64, 0) } // 遠い
  const schedule = (fn, ms) => timers.push({ fn, ms })
  const fire = (ms) => timers.filter((t) => t.ms === ms).forEach((t) => t.fn())
  return { bot, timers, written, schedule, fire, write: (r) => written.push(r) }
}

test('まわりのアイテムを、ボットからの差で近い順に（10 m まで、アイテムだけ）', () => {
  const { bot } = fixture()
  assert.deepEqual(look(bot).items, [{ dx: 3, dy: -4, dz: 0, d: 5, ground: true }])
})

test('掘った直後の様子と、0.5 / 1 / 2 秒後のまわりのアイテム、結果を 1 行にまとめて書く', () => {
  const f = fixture()
  const watch = watchDrop(f.bot, { block: 'oak_log', pos: v(0, 67, 1) }, { schedule: f.schedule, write: f.write })
  f.bot.entity.position.y = 66 // 動いても、掘った時の位置は写しを持つ
  for (const ms of LOOKS_MS) f.fire(ms)
  assert.equal(f.written.length, 0, '結果が来るまで書かない')
  watch.finish('failed: dug oak_log but picked nothing up')
  assert.equal(f.written.length, 1)
  const rec = f.written[0]
  assert.equal(rec.block, 'oak_log')
  assert.deepEqual(rec.bot, [0.5, 64, 0.5])
  assert.equal(rec.log_above_bot, 3)
  assert.equal(rec.below, 'dirt')
  assert.equal(rec.building, false)
  assert.deepEqual(rec.looks.map((l) => l.after_ms), LOOKS_MS)
  assert.equal(rec.looks[0].bot_dy, 2)
  assert.match(rec.outcome, /picked nothing up/)
})

test('結果が先に来ても、見終わってから書く。一度しか書かない。結果が来なくても最後に書く', () => {
  const f = fixture()
  const watch = watchDrop(f.bot, { block: 'oak_log', pos: v(0, 65, 0) }, { schedule: f.schedule, write: f.write })
  watch.finish('ok: dug oak_log, picked up 1 items')
  assert.equal(f.written.length, 0)
  for (const ms of LOOKS_MS) f.fire(ms)
  assert.equal(f.written.length, 1)
  f.timers.filter((t) => t.ms > 2000).forEach((t) => t.fn())
  assert.equal(f.written.length, 1)

  const g = fixture()
  watchDrop(g.bot, { block: 'oak_log', pos: v(0, 65, 0) }, { schedule: g.schedule, write: g.write })
  g.timers.at(-1).fn() // 行動が終わらなくても書く
  assert.equal(g.written[0].outcome, 'unknown')
})
