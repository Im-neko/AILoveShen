// 苗木のマスに草花が乗っていたら、先にどけて植える（設計書 33）: 設計は草花の乗ったマスも選ぶが、plant は空気のマスしか受けなかった
import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { PRIMITIVES } from '../src/primitives.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')
const SOLID = new Set(['grass_block', 'dirt', 'stone'])

// y 67 が草、y 68 が苗木のマス（blocks で上書き）
function world (blocks = {}) {
  const dug = []
  const placedAt = []
  const bot = {
    registry: md,
    entity: { position: new Vec3(50.5, 68, -192.5), onGround: true },
    entities: {},
    time: { timeOfDay: 1000, age: 100 },
    heldItem: null,
    inventory: { items: () => [{ name: 'birch_sapling', count: 2, type: md.itemsByName.birch_sapling.id }] },
    blockAt: (p) => {
      const q = new Vec3(Math.floor(p.x), Math.floor(p.y), Math.floor(p.z))
      const name = blocks[`${q.x},${q.y},${q.z}`] ?? (q.y === 67 ? 'grass_block' : q.y < 67 ? 'dirt' : 'air')
      return { name, boundingBox: SOLID.has(name) ? 'block' : 'empty', position: q, getProperties: () => ({}) }
    },
    pathfinder: { goto: async () => {}, setGoal: () => {} },
    equip: async () => {},
    lookAt: async () => {},
    waitForTicks: async () => {},
    on: () => {},
    off: () => {},
    dig: async (b) => { dug.push(b.name); blocks[`${b.position.x},${b.position.y},${b.position.z}`] = 'air' },
    placeBlock: async (ground, v) => { const p = ground.position.plus(v); placedAt.push(p); blocks[`${p.x},${p.y},${p.z}`] = 'birch_sapling' }
  }
  return { bot, dug, placedAt }
}
const run = (bot, state = {}) => PRIMITIVES.plant(bot, { plantings: [], ...state }, { item: 'birch_sapling', pos: new Vec3(50, 68, -194) }, new AbortController().signal)

test('苗木のマスに草花が乗っていたら、どけてから植える', async () => {
  for (const flower of ['white_tulip', 'short_grass', 'poppy']) {
    const { bot, dug, placedAt } = world({ '50,68,-194': flower })
    assert.match(await run(bot), /^planted birch_sapling at 50,68,-194/)
    assert.deepEqual(dug, [flower])
    assert.equal(placedAt.length, 1)
  }
})

test('空気のマスは掘らずに植える。草花でない物が乗っていたら、掘らずに断る', async () => {
  const air = world()
  assert.match(await run(air.bot), /^planted/)
  assert.deepEqual(air.dug, [])
  const log = world({ '50,68,-194': 'birch_log' })
  await assert.rejects(run(log.bot), /no free ground for a sapling at 50,68,-194 any more \(birch_log is in the cell\)/)
  assert.deepEqual(log.dug, [])
  // 地面がなければ、草花をどけても植えない
  const hole = world({ '50,68,-194': 'poppy', '50,67,-194': 'air' })
  await assert.rejects(run(hole.bot), /any more \(the ground under it is air\)/)
  // 固いブロックが入っているマスは、掘ってよい物なら掘るよう添える
  const solid = world({ '50,68,-194': 'grass_block' })
  await assert.rejects(run(solid.bot), /grass_block is in the cell \(dig it if it may be removed\)/)
})
