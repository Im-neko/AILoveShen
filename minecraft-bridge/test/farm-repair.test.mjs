// 崩れた畑を直す（設計書 115）: 作物のマスの地面が耕せない固いブロックなら掘って土で埋め直す。畑は土だけで埋める
import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { registerFacility, onFarmGround, inTown, FARM_DIG_COST, TOWN_MARGIN } from '../src/facilities.mjs'
import { farmLevel } from '../src/leveling.mjs'
import { makeGoal, evaluate } from '../src/goals.mjs'
import { Knowledge } from '../src/knowledge.mjs'
import { PRIMITIVES, configureMovements } from '../src/primitives.mjs'
import { digTargets } from '../src/world.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')
const SOLID = (n) => !/air|wheat|short_grass|red_tulip/.test(n)

// 地面は y 63 の草（その下は土）、上は空気。blocks で上書き。items は [名前, 数] で、掘ると土が増え、置くと減る
function world (blocks = {}, items = []) {
  const inv = items.map(([name, count]) => ({ name, count, type: md.itemsByName[name].id }))
  const give = (name) => { const i = inv.find((x) => x.name === name); if (i) i.count++; else inv.push({ name, count: 1, type: md.itemsByName[name].id }) }
  const bot = {
    registry: md,
    entity: { position: new Vec3(10.5, 64, 0.5), onGround: true, effects: {} },
    entities: {},
    time: { timeOfDay: 1000, age: 0 },
    heldItem: null,
    inventory: { items: () => inv.filter((i) => i.count > 0), emptySlotCount: () => 30 },
    blockAt: (p) => {
      const q = new Vec3(Math.floor(p.x), Math.floor(p.y), Math.floor(p.z))
      const name = blocks[`${q.x},${q.y},${q.z}`] ?? (q.y === 63 ? 'grass_block' : q.y < 63 ? 'dirt' : 'air')
      return { name, boundingBox: SOLID(name) ? 'block' : 'empty', position: q, getProperties: () => ({}) }
    },
    findBlocks: () => [],
    equip: async () => {},
    waitForTicks: async () => {},
    dig: async (b) => { blocks[`${b.position.x},${b.position.y},${b.position.z}`] = 'air'; give(b.name === 'cobblestone' ? 'cobblestone' : 'dirt') },
    placeBlock: async (below, v) => {
      const p = below.position.plus(v)
      const now = blocks[`${p.x},${p.y},${p.z}`]
      if (now && now !== 'air') throw new Error(`the block is still ${now}`) // 花が咲いたままでは置けない
      const item = inv.find((i) => i.count > 0 && i.name === bot.heldName)
      item.count--
      blocks[`${p.x},${p.y},${p.z}`] = item.name === 'dirt' ? 'dirt' : item.name
    }
  }
  bot.equip = async (item) => { bot.heldName = item.name }
  return bot
}
const home = { breach: [], inside: new Vec3(0, 64, 0), min: new Vec3(-1, 64, -1), max: new Vec3(1, 64, 1), door: new Vec3(0, 64, 2), outside: new Vec3(0, 64, 3) }
const state = () => ({ home, plan: null, builds: {}, formerHomes: [], recipeBook: { recipesFor: () => [1] }, unreachableDrops: new Set() })
const ZONE = [{ min_x: 8, min_z: 0, max_x: 15, max_z: 7 }]
const design = (s) => registerFacility(world(), s, 'wheat_farm', { kind: 'farm', boxes: ZONE, design: { width: 3, depth: 3 } })
const at = (c, y = c.y) => `${c.x},${y},${c.z}`

test('畑の修理: 作物のマスの地面が丸石なら、掘って土で埋め直す。作物でないマスの丸石は触らない', () => {
  const s = state()
  const fac = design(s)
  const [c] = fac.layout.cells
  const { rect } = fac.layout
  const bot = world({ [at(c)]: 'cobblestone' })
  const lv = farmLevel(bot, s, fac)
  assert.equal(lv.off, 1)
  assert.deepEqual(lv.cuts.map((p) => [p.x, p.y, p.z]), [[c.x, c.y, c.z]])
  assert.deepEqual(lv.fills.map((p) => [p.x, p.y, p.z]), [[c.x, c.y, c.z]])
  // 水のマス（作物のマスでない）の丸石は、直す対象にしない
  const [w] = fac.layout.water
  assert.equal(farmLevel(world({ [at(w)]: 'cobblestone' }), s, fac).off, 0)
  // 置いた物（かまど）は壊さない
  assert.equal(farmLevel(world({ [at(c)]: 'furnace' }), s, fac).off, 0)
  assert.ok(rect)
})

test('畑の修理: level は丸石を掘り、持っている丸石でなく土で埋める。花が咲いていれば先にどける', async () => {
  const s = state()
  const fac = design(s)
  const [c] = fac.layout.cells
  const blocks = { [at(c)]: 'cobblestone' }
  const bot = world(blocks, [['cobblestone', 8], ['wooden_pickaxe', 1]])
  // 土がなく丸石だけなら埋められない（畑は耕せる土だけ）
  assert.match(await PRIMITIVES.level(bot, s, { facility: 'wheat_farm' }, new AbortController().signal), /dug 1 blocks, filled 0.*no dirt to fill/)
  assert.equal(blocks[at(c)], 'air')
  const mended = { [at(c)]: 'cobblestone' }
  const bot2 = world(mended, [['wooden_pickaxe', 1], ['dirt', 2]])
  const out = await PRIMITIVES.level(bot2, s, { facility: 'wheat_farm' }, new AbortController().signal)
  assert.match(out, /dug 1 blocks, filled 1/)
  assert.equal(mended[at(c)], 'dirt')
  assert.equal(farmLevel(bot2, s, fac).off, 0)
  // 埋めるマスに花が咲いていても、先にどけて埋める
  const flowered = { [at(c)]: 'red_tulip' }
  const bot3 = world(flowered, [['dirt', 2]])
  assert.match(await PRIMITIVES.level(bot3, s, { facility: 'wheat_farm' }, new AbortController().signal), /filled 1/)
  assert.equal(flowered[at(c)], 'dirt')
})

test('畑の修理: 畑の目標は、崩れていれば耕す・まくより先に level を手にする。土がなければ集める', () => {
  const k = new Knowledge(md)
  const s = state()
  const fac = design(s)
  const [c] = fac.layout.cells
  const FULL = (items) => ({ inventory: {}, blocks: {}, mobs: {}, table: null, unlocked: () => true, dig: () => [], hunt: () => [], ...items })
  const gear = [['wooden_hoe', 1], ['wheat_seeds', 4], ['wooden_pickaxe', 1]]
  const broken = world({ [at(c)]: 'cobblestone' }, [...gear, ['dirt', 4]])
  s.goal = makeGoal({ predicate: 'farmed', item: 'wheat', count: 2 }, broken, s, k)
  let r = evaluate(broken, s, k, FULL({ inventory: { wooden_hoe: 1, wheat_seeds: 4, wooden_pickaxe: 1, dirt: 4 } }))
  assert.deepEqual(r.leaves.map((l) => [l.kind, l.facility]), [['level', 'wheat_farm']], r.lines.join('\n'))
  assert.match(r.lines.join('\n'), /has fallen apart/)
  // 丸石は土の数に入らない
  const noSoil = world({ [at(c)]: 'air' }, [...gear, ['cobblestone', 9]])
  s.goal = makeGoal({ predicate: 'farmed', item: 'wheat', count: 2 }, noSoil, s, k)
  r = evaluate(noSoil, s, k, FULL({ inventory: { wooden_hoe: 1, wheat_seeds: 4, wooden_pickaxe: 1, cobblestone: 9 } }))
  assert.ok(r.lines.some((l) => /need 1 dirt \(carrying 0\)/.test(l)), r.lines.join('\n'))
})

test('畑の地面を掘る経路の費用: 掘れるが高い（禁止でなく）。畑の外、畑の上のブロック、閉じた所から出る行動は無料', () => {
  const s = state()
  const fac = design(s)
  s.facilities = { wheat_farm: fac }
  const [c] = fac.layout.cells
  assert.ok(FARM_DIG_COST > 0 && FARM_DIG_COST < 100)
  assert.equal(onFarmGround(s, new Vec3(c.x, c.y, c.z)), true)
  assert.equal(onFarmGround(s, new Vec3(c.x, c.y + 1, c.z)), false)
  assert.equal(onFarmGround(s, new Vec3(c.x + 50, c.y, c.z)), false)
  let m
  const bot = { registry: md, entities: {}, entity: { position: new Vec3(0, 64, 0) }, blockAt: () => null, pathfinder: { setMovements: (set) => { m = set } } }
  configureMovements(bot, s)
  const dirt = (x, y, z) => ({ name: 'dirt', position: new Vec3(x, y, z) })
  assert.equal(m.exclusionBreak(dirt(c.x, c.y, c.z)), FARM_DIG_COST)
  assert.equal(m.exclusionBreak(dirt(c.x + 50, c.y, c.z)), 0)
  assert.equal(m.exclusionBreak(dirt(c.x, c.y + 1, c.z)), 0)
  s.current = { verb: 'climb_out' }
  assert.equal(m.exclusionBreak(dirt(c.x, c.y, c.z)), 0)
})

test('街: 家・建物・施設の範囲とそのまわり。土は街の外を先に掘る（畑の地面は最後）', () => {
  const s = state()
  const fac = design(s)
  s.facilities = { wheat_farm: fac, home: { name: 'home', kind: 'home', box: { min: { x: -1, y: 64, z: -1 }, max: { x: 1, y: 64, z: 1 } } } }
  const [c] = fac.layout.cells
  assert.equal(inTown(s, new Vec3(1 + TOWN_MARGIN, 64, 0)), true)
  assert.equal(inTown(s, new Vec3(fac.box.max.x + TOWN_MARGIN + 1, 64, fac.box.max.z + TOWN_MARGIN + 1)), false)
  const outside = new Vec3(c.x + 40, 63, c.z)
  const near = new Vec3(0, 63, 5) // 家のそば: 街の中
  const field = new Vec3(c.x, c.y, c.z) // 畑の地面
  const bot = {
    registry: md,
    entity: { position: new Vec3(c.x, 64, c.z) },
    entities: {},
    findBlocks: () => [field, near, outside], // 近い順（畑、街、外）
    blockAt: (p) => ({ name: 'dirt', boundingBox: 'block', position: p, getProperties: () => ({}) })
  }
  const exposedBot = { ...bot, blockAt: (p) => (p.y > 63 ? { name: 'air', boundingBox: 'empty', position: p } : { name: 'dirt', boundingBox: 'block', position: p, getProperties: () => ({}) }) }
  s.unreachableBlocks = new Set()
  assert.deepEqual(digTargets(exposedBot, s, 'dirt').map((p) => `${p.x},${p.y},${p.z}`), [outside, near, field].map((p) => `${p.x},${p.y},${p.z}`))
})
