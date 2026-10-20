import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { Knowledge } from '../src/knowledge.mjs'
import { windfallNow, windfallTrips, collectKinds, canHarvest, WINDFALL_ORE } from '../src/windfall.mjs'
import { WorldMap, makeClassifier, AIR } from '../src/worldmap.mjs'
import { registerFacility } from '../src/facilities.mjs'
import { ground } from '../src/candidates.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')
const k = new Knowledge(md)
const v = (x, y, z) => new Vec3(x, y, z)
const id = (name) => md.itemsByName[name].id

// 足元は石、その上は空気。blocks: 'x,y,z' -> ブロック名（空気に面した位置に置く）
function setup ({ items = [], blocks = {}, health = 20, food = 20, time = 1000 } = {}) {
  const inv = items.map(([name, count]) => ({ name, count, type: id(name) }))
  const bot = {
    registry: md,
    entity: { position: v(0.5, 70, 0.5), onGround: true },
    entities: {},
    time: { timeOfDay: time, age: 100000 },
    health,
    food,
    heldItem: null,
    inventory: { items: () => inv, emptySlotCount: () => 20 },
    pathfinder: { isMining: () => false, isBuilding: () => false },
    blockAt: (p) => {
      const name = blocks[`${p.x},${p.y},${p.z}`]
      if (name) return { name, position: p, boundingBox: 'block', type: md.blocksByName[name].id }
      return p.y < 70 ? { name: 'stone', position: p, boundingBox: 'block' } : { name: 'air', position: p, boundingBox: 'empty' }
    },
    canSeeBlock: () => true,
    findBlocks: ({ matching, maxDistance }) => {
      const ids = [].concat(matching)
      return Object.entries(blocks).filter(([, n]) => ids.includes(md.blocksByName[n].id)).map(([key]) => v(...key.split(',').map(Number)))
        .filter((p) => p.distanceTo(v(0.5, 70, 0.5)) <= maxDistance)
    }
  }
  return { bot, state: { home: null, memory: { places: {}, chests: {} } } }
}

const ORE_IRON = { '3,70,0': 'iron_ore' }

test('鉱石は石のツルハシで採れる。ダイヤは鉄のツルハシから、黒曜石はダイヤのツルハシから', () => {
  const { bot } = setup({ items: [['stone_pickaxe', 1]] })
  assert.equal(canHarvest(bot, 'iron_ore'), true)
  assert.equal(canHarvest(bot, 'coal_ore'), true)
  assert.equal(canHarvest(bot, 'diamond_ore'), false)
  assert.equal(canHarvest(bot, 'obsidian'), false)
  assert.equal(canHarvest(setup({ items: [['iron_pickaxe', 1]] }).bot, 'diamond_ore'), true)
  assert.equal(canHarvest(setup({ items: [['iron_pickaxe', 1]] }).bot, 'obsidian'), false)
  assert.equal(canHarvest(setup({ items: [['diamond_pickaxe', 1]] }).bot, 'obsidian'), true)
  assert.equal(canHarvest(setup().bot, 'iron_ore'), false) // 素手では掘れない
  assert.equal(canHarvest(setup().bot, 'sand'), true) // 道具が要らないものは素手で
})

test('対象は鉱石と、設定の一覧（レジストリにあるものだけ）', () => {
  const { bot, state } = setup()
  state.collect = { extra: ['obsidian', 'no_such_block'] }
  const kinds = collectKinds(bot, state)
  assert.ok(kinds.includes('iron_ore') && kinds.includes('deepslate_diamond_ore') && kinds.includes('obsidian'))
  assert.ok(!kinds.includes('no_such_block') && !kinds.includes('stone'))
  assert.ok(WINDFALL_ORE.test('deepslate_redstone_ore') && !WINDFALL_ORE.test('nether_gold_ore'))
})

test('近くの鉱石は、採れる道具があるときだけ候補（windfall: now）に出る', () => {
  const have = setup({ items: [['stone_pickaxe', 1]], blocks: ORE_IRON })
  const [c] = windfallNow(have.bot, have.state, k)
  assert.equal(c.windfall, 'now')
  assert.equal(c.verb, 'dig')
  assert.equal(c.block, 'iron_ore')
  assert.match(c.id, /^collect iron_ore at 3,70,0/)
  assert.deepEqual(windfallNow(setup({ items: [['wooden_pickaxe', 1]], blocks: ORE_IRON }).bot, { home: null }, k), []) // 木では鉄が採れない
  assert.deepEqual(windfallNow(setup({ items: [['stone_pickaxe', 1]], blocks: { '3,70,0': 'diamond_ore' } }).bot, { home: null }, k), [])
})

test('壁や岩の向こうの鉱石は、近くにあっても候補に出さない（視界に入っているものだけ）', () => {
  const { bot, state } = setup({ items: [['stone_pickaxe', 1]], blocks: { '3,70,0': 'iron_ore', '0,70,3': 'coal_ore' } })
  bot.canSeeBlock = (block) => block.name !== 'iron_ore'
  const found = windfallNow(bot, state, k)
  assert.deepEqual(found.map((c) => c.block), ['coal_ore'])
  bot.canSeeBlock = () => false
  assert.deepEqual(windfallNow(bot, state, k), [])
})

test('寄り道しないとき: 体力が危険、飢えそう、夜の外、持っている数が上限、12 m より遠い', () => {
  const items = [['stone_pickaxe', 1]]
  assert.deepEqual(windfallNow(...Object.values(setup({ items, blocks: ORE_IRON, health: 6 })).slice(0, 2), k), [])
  assert.deepEqual(windfallNow(...Object.values(setup({ items, blocks: ORE_IRON, food: 5 })).slice(0, 2), k), [])
  assert.deepEqual(windfallNow(...Object.values(setup({ items, blocks: ORE_IRON, time: 18000 })).slice(0, 2), k), [])
  assert.deepEqual(windfallNow(...Object.values(setup({ items: [...items, ['raw_iron', 64]], blocks: ORE_IRON })).slice(0, 2), k), [])
  assert.deepEqual(windfallNow(...Object.values(setup({ items, blocks: { '30,70,0': 'iron_ore' } })).slice(0, 2), k), [])
})

test('身を守る小目標の間は寄り道しない（家に帰る・夜を越す・戦う）', () => {
  const items = [['stone_pickaxe', 1]]
  const goal = (predicate) => { const r = setup({ items, blocks: ORE_IRON }); r.state.goal = { spec: { predicate } }; return windfallNow(r.bot, r.state, k) }
  assert.equal(goal('have').length, 1)
  for (const p of ['at_home', 'through_night', 'cleared']) assert.deepEqual(goal(p), [], p)
})

test('地下は武器を持っているときだけ寄り道する。地下で夜を越す間は寄り道してよい（家に帰る間はしない）', () => {
  const underground = (extra, predicate = 'have') => {
    const r = setup({ items: [['stone_pickaxe', 1], ...extra] })
    r.state.goal = { spec: { predicate } }
    r.bot.entity.position = v(0.5, 40, 0.5)
    const cell = (p) => p.x === 3 && p.y === 40 && p.z === 0 ? 'iron_ore' : p.y === 40 && p.x < 3 && p.z === 0 ? 'air' : 'stone'
    r.bot.blockAt = (p) => ({ name: cell(p), position: p, boundingBox: cell(p) === 'air' ? 'empty' : 'block', type: md.blocksByName[cell(p)].id })
    r.bot.findBlocks = () => [v(3, 40, 0)]
    r.bot.canSeeBlock = () => true
    return windfallNow(r.bot, r.state, k)
  }
  assert.deepEqual(underground([]), [])
  assert.equal(underground([['wooden_sword', 1]]).length, 1)
  assert.equal(underground([['wooden_sword', 1]], 'through_night').length, 1)
  assert.deepEqual(underground([['wooden_sword', 1]], 'at_home'), [])
})

test('候補の一覧（ground）に、目標と関係なく出る', () => {
  const { bot, state } = setup({ items: [['stone_pickaxe', 1]], blocks: ORE_IRON })
  const { candidates } = ground(bot, state, k, { dig: () => [], hunt: () => [] }, { leaves: [] })
  assert.ok(candidates.some((c) => c.windfall === 'now' && c.block === 'iron_ore'))
})

test('採掘場の階段の柱にある鉱石へは、行く候補を出さない（行っても dig が断る）', () => {
  const cls = makeClassifier(md)
  const worldMap = new WorldMap()
  const { bot, state } = setup({ items: [['stone_pickaxe', 1], ['stone_sword', 1], ['torch', 64]] })
  Object.assign(state, { worldMap, mapCells: cls, plan: null, placed: {}, builds: {}, formerHomes: [] })
  const put = (x, y, z) => { worldMap.set(x, y, z, cls.cellOf('iron_ore'), 90); worldMap.set(x, y + 1, z, AIR, 90) }
  const flat = { registry: md, entity: { position: v(10, 64, 0) }, inventory: { items: () => [], emptySlotCount: () => 30 }, entities: {}, findBlock: () => null, time: { timeOfDay: 1000, age: 0 }, findBlocks: () => [], blockAt: (p) => { const name = p.y > 63 ? 'air' : p.y === 63 ? 'grass_block' : 'stone'; return { name, boundingBox: p.y > 63 ? 'empty' : 'block', position: p } } }
  const fac = registerFacility(flat, { home: { inside: v(0, 64, 0), min: v(-1, 64, -1), max: v(1, 64, 1), door: v(0, 64, 2), outside: v(0, 64, 3) }, plan: null, builds: {}, formerHomes: [] }, 'mine_1', {
    kind: 'mine', boxes: [{ min_x: 8, min_z: 0, max_x: 15, max_z: 7 }], design: { bottom_y: 40, tunnels: 2, tunnel_length: 8 }
  })
  state.facilities = { mine_1: fac }
  const pillar = { x: fac.box.min.x + 1, y: 50, z: fac.box.min.z + 1 }
  put(pillar.x, pillar.y, pillar.z)
  put(-30, 66, 0) // 採掘場とは関係ない鉄
  const trips = windfallTrips(bot, state, k)
  assert.deepEqual(trips.map((t) => [t.pos.x, t.pos.z]), [[-30, 0]])
})
