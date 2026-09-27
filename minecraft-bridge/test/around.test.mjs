import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { aroundMe, AROUND_KINDS } from '../src/around.mjs'

const { Vec3 } = vec3Pkg

// 草の地面（y 69）の上に立つ。y 69 より下は石（空気に面していないので出ない）
function world (extra = {}, entities = {}) {
  const blocks = new Map(Object.entries(extra))
  const blockAt = (p) => {
    const set = blocks.get(`${p.x},${p.y},${p.z}`)
    const name = set?.name ?? (p.y < 69 ? 'stone' : p.y === 69 ? 'grass_block' : 'air')
    return { name, position: p, getProperties: () => set?.props ?? {} }
  }
  const bot = { entity: { position: new Vec3(0.5, 70, 0.5) }, blockAt, entities }
  bot.entities.self = bot.entity
  return bot
}

test('目の前の原木と作物を、数・一番近い位置・取れる物・熟した数で出す', () => {
  const bot = world({
    '3,70,0': { name: 'oak_log' },
    '3,71,0': { name: 'oak_log' },
    '0,70,-2': { name: 'wheat', props: { age: 7 } },
    '1,70,-2': { name: 'wheat', props: { age: 3 } },
    '0,73,5': { name: 'oak_log' }, // 高さ +3: 出さない
    '9,70,0': { name: 'oak_log' } // 半径の外
  })
  const around = aroundMe(bot, (n) => n === 'wheat' ? ['wheat', 'wheat_seeds'] : [])
  const by = Object.fromEntries(around.blocks.map((b) => [b.name, b]))
  assert.equal(by.oak_log.count, 2)
  assert.deepEqual([by.oak_log.nearest.x, by.oak_log.nearest.y, by.oak_log.nearest.direction], [3, 70, 'E'])
  assert.equal(by.oak_log.gives, undefined)
  assert.equal(by.wheat.count, 2)
  assert.equal(by.wheat.ripe, 1)
  assert.deepEqual(by.wheat.gives, ['wheat', 'wheat_seeds'])
  assert.equal(by.stone, undefined) // 埋まっている
  assert.equal(by.grass_block.nearest.dy, -1)
  assert.ok(around.blocks.length <= AROUND_KINDS)
})

test('落ちているアイテムは種類ごとに数と一番近いもの（高さ ±2 まで）', () => {
  const item = (name, count, x, y, z) => ({ name: 'item', position: new Vec3(x, y, z), getDroppedItem: () => ({ name, count }) })
  const bot = world({}, {
    a: item('oak_sapling', 1, 2.5, 70, 0.5),
    b: item('oak_sapling', 2, -4.5, 70, 0.5),
    c: item('apple', 1, 0.5, 75, 0.5) // 高すぎる
  })
  const { items } = aroundMe(bot)
  assert.deepEqual(items, [{ item: 'oak_sapling', count: 3, distance_m: 2, direction: 'E' }])
})
