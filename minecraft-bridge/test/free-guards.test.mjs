// 既定（BRIDGE_GUARDS が standard 以外）: 作業知識の断りは警告にして通し、安全の断りは残す（設計書 124）。standard は断る
import { test, afterEach } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { createTools } from '../src/tools.mjs'
import { freeGuards } from '../src/guards.mjs'
import { layoutStatus } from '../src/facilities.mjs'
import { Knowledge } from '../src/knowledge.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')
const k = new Knowledge(md)
const SOLID = new Set(['grass_block', 'dirt', 'stone', 'oak_planks', 'farmland'])

afterEach(() => { delete process.env.BRIDGE_GUARDS })

// y 67 が草、その下が土。bot は (50.5, 68, -192.5)。home は遠く（30 ブロック離れた西）
function world (blocks = {}, items = []) {
  const inv = items.map(([name, count]) => ({ name, count, type: md.itemsByName[name].id }))
  return {
    registry: md,
    entity: { position: new Vec3(50.5, 68, -192.5), onGround: true, yaw: 0, eyeHeight: 1.62 },
    entities: {},
    time: { timeOfDay: 1000, age: 100 },
    health: 20,
    food: 20,
    heldItem: null,
    inventory: { items: () => inv, emptySlotCount: () => 30 },
    blockAt: (p) => {
      const q = new Vec3(Math.floor(p.x), Math.floor(p.y), Math.floor(p.z))
      const name = blocks[`${q.x},${q.y},${q.z}`] ?? (q.y === 67 ? 'grass_block' : q.y < 67 ? 'dirt' : 'air')
      const solid = SOLID.has(name)
      const age = blocks[`age:${q.x},${q.y},${q.z}`] ?? 0
      return { name, boundingBox: solid ? 'block' : 'empty', shapes: solid ? [[0, 0, 0, 1, 1, 1]] : [], position: q, getProperties: () => ({ age }) }
    },
    get world () { return { getBlock: this.blockAt } },
    pathfinder: { goto: async () => {}, setGoal: () => {} },
    on: () => {},
    off: () => {},
    waitForTicks: async () => {}
  }
}
const home = { door: new Vec3(20, 68, -190), inside: new Vec3(21, 68, -190), outside: new Vec3(19, 68, -190), min: new Vec3(21, 68, -192), max: new Vec3(23, 68, -188), bed: null, breach: [] }
const stateOf = (extra = {}) => ({ home, plan: null, memory: {}, facilities: {}, busy: false, reflex: false, plantings: [], ...extra })
const toolsOf = (bot, state) => createTools({ bot, state, knowledge: k, run: async (c) => ({ ok: true, result: c.verb, seconds: 0 }), candidates: () => [], check: async () => [] })
const both = async (fn) => {
  process.env.BRIDGE_GUARDS = 'standard'
  const standard = await fn()
  process.env.BRIDGE_GUARDS = 'free'
  const free = await fn()
  delete process.env.BRIDGE_GUARDS
  return { standard, free }
}

test('既定は free。BRIDGE_GUARDS=standard のときだけ作業知識も断る', () => {
  assert.equal(freeGuards(), true)
  process.env.BRIDGE_GUARDS = 'standard'
  assert.equal(freeGuards(), false)
  process.env.BRIDGE_GUARDS = 'free'
  assert.equal(freeGuards(), true)
})

test('設計したマスの外に植える・耕す: 標準は断り、free は理由を添えて通す', async () => {
  const bot = world({}, [['birch_sapling', 2], ['stone_hoe', 1]])
  const call = toolsOf(bot, stateOf())
  const plant = await both(() => call('plant', { item: 'birch_sapling', x: 50, y: 68, z: -194, intent: 'x' }))
  assert.equal(plant.standard.refused, true)
  assert.match(plant.standard.result, /will not plant at 50,68,-194: new farmland and saplings go only/)
  assert.equal(plant.free.ok, true, plant.free.result)
  assert.match(plant.free.result, /^plant \(not enforced: will not plant at 50,68,-194: new farmland and saplings go only/)
  // 畑の外の耕しは、畑がなくても落ちない
  const till = await both(() => call('till', { x: 50, y: 67, z: -194, intent: 'x' }))
  assert.equal(till.standard.refused, true)
  assert.equal(till.free.ok, true, till.free.result)
  assert.match(till.free.result, /not enforced: will not till at 50,67,-194/)
})

test('家のそばは free でも断る（壁をふさぐ・壊す）。家から遠いだけの断りは警告', async () => {
  const bot = world({}, [['birch_sapling', 2]])
  process.env.BRIDGE_GUARDS = 'free'
  const call = toolsOf(bot, stateOf())
  // 壁の隣 (24,68,-190) は、free でも断る。ただし届く距離に動かす
  bot.entity.position = new Vec3(25.5, 68, -190.5)
  const wall = await call('plant', { item: 'birch_sapling', x: 24, y: 68, z: -190, intent: 'x' })
  assert.equal(wall.refused, true)
  assert.match(wall.result, /on or next to the home or a build/)
  // 家がまだないときは、警告で通る
  const noHome = await toolsOf(bot, stateOf({ home: null }))('plant', { item: 'birch_sapling', x: 29, y: 68, z: -190, intent: 'x' })
  assert.equal(noHome.ok, true, noHome.result)
  assert.match(noHome.result, /not enforced: will not plant at 29,68,-190: there is no home yet/)
})

test('整地の保護・熟す前の作物: 標準は断り、free は通す。水・溶岩をせき止める所は free でも断る', async () => {
  const state = stateOf({ leveling: { done: [{ x: 50, y: 68, z: -194, radius: 3 }], wanted: [] } })
  const bot = world({ '53,67,-194': 'stone' })
  const call = toolsOf(bot, state)
  const kept = await both(() => call('dig', { x: 50, y: 67, z: -194, intent: 'x' }))
  assert.equal(kept.standard.refused, true)
  assert.match(kept.standard.result, /being leveled/)
  assert.equal(kept.free.ok, true, kept.free.result)
  assert.match(kept.free.result, /not enforced: will not dig grass_block at 50,67,-194: the ground there is being leveled/)
  const crop = world({ '50,68,-194': 'wheat' })
  const callCrop = toolsOf(crop, stateOf())
  const unripe = await both(() => callCrop('dig', { x: 50, y: 68, z: -194, intent: 'x' }))
  assert.equal(unripe.standard.refused, true)
  assert.match(unripe.standard.result, /is not ripe yet/)
  assert.equal(unripe.free.ok, true, unripe.free.result)
  assert.match(unripe.free.result, /not enforced: the wheat at 50,68,-194 is not ripe yet/)
  // 水をせき止める土: 安全は free でも断る
  const wet = world({ '51,67,-194': 'water' })
  process.env.BRIDGE_GUARDS = 'free'
  const held = await toolsOf(wet, stateOf())('dig', { x: 50, y: 67, z: -194, intent: 'x' })
  assert.equal(held.refused, true)
  assert.match(held.result, /will not dig grass_block at 50,67,-194/)
})

test('かまどの置き場所は警告、家の保護は free でも断る', async () => {
  const bot = world({ '24,67,-190': 'grass_block', '24,68,-190': 'oak_planks' }, [['furnace', 1], ['oak_planks', 1]])
  const call = toolsOf(bot, stateOf())
  const furnace = await both(() => call('place', { item: 'furnace', x: 50, y: 68, z: -194, intent: 'x' }))
  assert.equal(furnace.standard.refused, true)
  assert.match(furnace.standard.result, /placed only inside the house/)
  assert.equal(furnace.free.ok, true, furnace.free.result)
  assert.match(furnace.free.result, /not enforced: will not place at 50,68,-194: a furnace is placed only inside the house/)
  // 家の壁（現在の家）は free でも断る
  bot.entity.position = new Vec3(24.5, 68, -192.5)
  process.env.BRIDGE_GUARDS = 'free'
  const wall = await call('place', { item: 'oak_planks', x: 24, y: 69, z: -190, intent: 'x' })
  assert.equal(wall.refused, true, wall.result)
  assert.match(wall.result, /current home/)
})

test('断られた手の台帳: 標準は同じ場所を断り、free は前の失敗を添えて通す', async () => {
  const state = stateOf()
  state.refusals = new Map([['dig:50,67,-194', { verb: 'dig', pos: { x: 50, y: 67, z: -194 }, reason: 'will not dig dirt at 50,67,-194: no path', until: Date.now() + 300000, at: Date.now() - 120000 }]])
  const call = toolsOf(world(), state)
  const r = await both(() => call('dig', { x: 50, y: 67, z: -194, intent: 'x' }))
  assert.equal(r.standard.refused, true)
  assert.match(r.standard.result, /was tried before and will not work/)
  assert.equal(r.free.ok, true, r.free.result)
  assert.match(r.free.result, /an earlier dig at 50,67,-194 failed 2 min ago: will not dig dirt at 50,67,-194: no path/)
})

test('植林場のマスは、柱に苗木か木があれば植わっている（設計の高さがずれていても）', () => {
  const fac = { name: 'g', kind: 'tree_farm', layout: { sapling: 'birch_sapling', spacing: 5, cells: [{ x: 50, y: 65, z: -194 }, { x: 45, y: 68, z: -194 }] } }
  const bot = world({ '50,68,-194': 'birch_sapling' })
  const s = layoutStatus(bot, fac)
  assert.equal(s.done, 1)
  assert.deepEqual(s.plant.map((p) => `${p.x},${p.y},${p.z}`), ['45,68,-194'])
  // 柱の外（8 つ上）にある木は数えない
  const far = world({ '50,73,-194': 'birch_log' })
  assert.equal(layoutStatus(far, { ...fac, layout: { ...fac.layout, cells: [{ x: 50, y: 65, z: -194 }] } }).done, 0)
})

test('添えた警告は、その呼び出しの結果にだけ付く（次の呼び出しに残らない）', async () => {
  process.env.BRIDGE_GUARDS = 'free'
  const bot = world({}, [['birch_sapling', 2], ['stone_pickaxe', 1]])
  const call = toolsOf(bot, stateOf())
  const first = await call('plant', { item: 'birch_sapling', x: 50, y: 68, z: -194, intent: 'x' })
  assert.match(first.result, /not enforced/)
  const next = await call('equip', { item: 'stone_pickaxe', intent: 'x' })
  assert.doesNotMatch(next.result, /not enforced/)
})

test('設計した畑で、種より耕した数が多い耕しは、標準は断り、free は警告で通す', async () => {
  const farm = { name: 'f', kind: 'farm', layout: { crop: 'wheat', cells: [{ x: 50, y: 67, z: -194 }] } }
  const call = toolsOf(world({}, [['stone_hoe', 1]]), stateOf({ facilities: { f: farm } }))
  const r = await both(() => call('till', { x: 50, y: 67, z: -194, intent: 'x' }))
  assert.equal(r.standard.refused, true)
  assert.match(r.standard.result, /will not till more: you have 0 wheat_seeds/)
  assert.equal(r.free.ok, true, r.free.result)
  assert.match(r.free.result, /not enforced: will not till more: you have 0 wheat_seeds/)
})
