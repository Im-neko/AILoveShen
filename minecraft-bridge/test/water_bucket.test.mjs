// 水のバケツ: 満たされなければ失敗、地下の水は選ばない、戸口の列は掘らせない（2026-10-07、設計書 100）
import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { PRIMITIVES, configureMovements } from '../src/primitives.mjs'
import { waterSource } from '../src/world.mjs'
import { doorstepColumn } from '../src/home.mjs'

const { Vec3 } = vec3Pkg
const v = (x, y, z) => new Vec3(x, y, z)
const md = minecraftData('1.21.4')

function bucketBot ({ fills }) {
  const items = [{ name: 'bucket', count: 1, type: 1 }, { name: 'dirt', count: 3, type: 2 }]
  const bot = {
    entity: { position: v(0.5, 70, 0.5), eyeHeight: 1.62, onGround: true },
    entities: {},
    registry: md,
    time: { timeOfDay: 1000, age: 0 },
    heldItem: null,
    blockAt: (p) => ({ name: 'water', boundingBox: 'empty', position: p, getProperties: () => ({ level: 0 }) }),
    inventory: { items: () => items },
    equip: async (item) => { bot.heldItem = item },
    lookAt: async () => {},
    waitForTicks: async () => {},
    activateItem: () => {
      // 水に届かないとき: 持ち物は動かない（拾い直しの土が増えるだけ）
      if (fills) { items.splice(0, 1, { name: 'water_bucket', count: 1, type: 3 }) } else { items[1].count += 6 }
    }
  }
  return bot
}
const state = { home: null, memory: {}, unreachableDrops: new Set() }
const c = { verb: 'use_item', item: 'bucket', pos: v(0, 69, 0) }

test('空のバケツは、水のバケツが増えなければ失敗（無関係な土の拾い直しで成功にしない）', async () => {
  await assert.rejects(() => PRIMITIVES.use_item(bucketBot({ fills: false }), state, c, new AbortController().signal), /the bucket was not filled/)
})

test('水が汲めたら成功', async () => {
  const r = await PRIMITIVES.use_item(bucketBot({ fills: true }), state, c, new AbortController().signal)
  assert.match(r, /\+1 water_bucket/)
})

// 水源の列: x=0 が地下の洞窟の水（上は石）、x=10 が地表の池（上は空）
function pond () {
  const water = [v(0, 55, 0), v(10, 62, 0)]
  return {
    registry: md,
    entity: { position: v(1, 57, 0) }, // 地下の水のほうが近い
    findBlocks: () => water,
    blockAt: (p) => {
      if (water.some((w) => w.equals(p))) return { name: 'water', boundingBox: 'empty', position: p, getProperties: () => ({ level: 0 }) }
      const underground = p.x === 0 && p.y > 55 && p.y <= 66
      if (underground) return { name: 'stone', boundingBox: 'block', position: p }
      return { name: 'air', boundingBox: 'empty', position: p }
    }
  }
}

test('waterSource: 地下の洞窟の水（上が岩）は選ばず、地表の池を選ぶ', () => {
  const bot = pond()
  const w = waterSource(bot, { home: null, memory: {} })
  assert.deepEqual([w.x, w.y, w.z], [10, 62, 0])
})

test('doorstepColumn: 戸口の外に立つセルとその下（どの深さでも）。ほかの列と上は含めない', () => {
  const state = { home: { outside: v(73, 69, -204) } }
  assert.equal(doorstepColumn(state, v(73, 69, -204)), true)
  assert.equal(doorstepColumn(state, v(73, 60, -204)), true)
  assert.equal(doorstepColumn(state, v(73, 70, -204)), false)
  assert.equal(doorstepColumn(state, v(74, 60, -204)), false)
  assert.equal(doorstepColumn({ home: null }, v(73, 60, -204)), false)
})

test('経路探索は、戸口の列を掘らない（水を汲みに戸口から掘り下げた）。ほかの土は今までどおり', () => {
  let m
  const bot = { registry: md, entities: {}, entity: { position: v(0, 70, 0) }, blockAt: () => null, pathfinder: { setMovements: (set) => { m = set } } }
  const s = { home: { outside: v(73, 69, -204), inside: v(73, 69, -207), min: v(70, 69, -210), max: v(75, 69, -205) }, plan: null }
  configureMovements(bot, s)
  const at = (name, x, y, z) => ({ name, position: v(x, y, z) })
  assert.ok(m.exclusionBreak(at('dirt', 73, 68, -204)) >= 100)
  assert.ok(m.exclusionBreak(at('stone', 73, 62, -204)) >= 100)
  assert.equal(m.exclusionBreak(at('dirt', 80, 68, -204)), 0)
})
