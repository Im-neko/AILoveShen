// 種の余り（設計書 117）: 残すのは畑の数 × 1 スタック（持ち物とチェストの合計）。超えた分は捨てる
import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { Knowledge } from '../src/knowledge.mjs'
import { ground } from '../src/candidates.mjs'
import { createTools } from '../src/tools.mjs'
import { seedLimit, seedSurplus, seedsToToss, seedsToTake } from '../src/seeds.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')
const k = new Knowledge(md)

const farm = (crop = 'wheat') => ({ kind: 'farm', layout: { crop, cells: [], water: [] } })
const home = { inside: new Vec3(0, 69, 0), min: new Vec3(-2, 69, -2), max: new Vec3(2, 72, 2), door: new Vec3(0, 69, 3), breach: [] }

function botWith (held, { slots = 20, inside = true } = {}) {
  const items = Object.entries(held).flatMap(([name, count]) =>
    Array.from({ length: Math.ceil(count / 64) }, (_, i) => ({ name, count: Math.min(64, count - 64 * i) })))
  return {
    entity: { position: inside ? new Vec3(0.5, 69, 0.5) : new Vec3(30.5, 69, 30.5), yaw: 0 },
    entities: {},
    time: { timeOfDay: 1000, age: 100000 },
    health: 20,
    food: 20,
    heldItem: null,
    registry: md,
    inventory: { items: () => items, emptySlotCount: () => slots },
    blockAt: (p) => (p.y < 69 ? { name: 'stone', boundingBox: 'block' } : { name: 'air', boundingBox: 'empty' }),
    findBlock: () => null,
    toss: async () => {},
    look: async () => {}
  }
}

const stateOf = ({ farms = 1, stored = 0, goal = null } = {}) => ({
  home,
  plan: null,
  unreachableDrops: new Set(),
  facilities: Object.fromEntries(Array.from({ length: farms }, (_, i) => [`wheat_farm_${i}`, farm()])),
  memory: { chests: stored ? [{ x: 1, y: 69, z: 1, contents: { wheat_seeds: stored }, seen: 0 }] : [{ x: 1, y: 69, z: 1, contents: {}, seen: 0 }] },
  goal
})

test('残す数は、その作物の畑の数 × 1 スタック。畑がまだなくても 1 畑分', () => {
  const bot = botWith({})
  assert.equal(seedLimit(bot, stateOf({ farms: 0 }), 'wheat_seeds'), 64)
  assert.equal(seedLimit(bot, stateOf({ farms: 1 }), 'wheat_seeds'), 64)
  assert.equal(seedLimit(bot, stateOf({ farms: 3 }), 'wheat_seeds'), 192)
  // ほかの作物の畑は数えない
  const s = stateOf({ farms: 1 })
  s.facilities.beet = farm('beetroots')
  assert.equal(seedLimit(bot, s, 'wheat_seeds'), 64)
  assert.equal(seedLimit(bot, s, 'beetroot_seeds'), 64)
})

test('余りは持ち物とチェストの合計で決める。持ち物から捨て、残りはチェストから出して捨てる', () => {
  const state = stateOf({ farms: 1, stored: 300 })
  const bot = botWith({ wheat_seeds: 40 })
  assert.deepEqual(seedSurplus(bot, state).map((x) => [x.item, x.surplus]), [['wheat_seeds', 276]])
  assert.deepEqual(seedsToToss(bot, state), [{ item: 'wheat_seeds', count: 40 }])
  assert.deepEqual(seedsToTake(bot, state), [{ item: 'wheat_seeds', count: 236 }])
  // 持ち物だけで超えるなら、超えた分だけ
  const heavy = botWith({ wheat_seeds: 100 })
  assert.deepEqual(seedsToToss(heavy, stateOf({ farms: 1 })), [{ item: 'wheat_seeds', count: 36 }])
  assert.deepEqual(seedsToTake(heavy, stateOf({ farms: 1 })), [])
  // 余りがなければ何もない。ニンジン・ジャガイモは種に数えない
  assert.deepEqual(seedsToToss(botWith({ wheat_seeds: 64, carrot: 200, potato: 200 }), stateOf()), [])
})

test('目標が種そのものを集めているときは、捨てない', () => {
  const bot = botWith({ wheat_seeds: 100 })
  assert.deepEqual(seedsToToss(bot, stateOf(), new Set(['wheat_seeds'])), [])
})

const candidatesOf = (bot, state) => ground(bot, state, k, { dig: () => [], hunt: () => [] }, { leaves: [] }).candidates

test('候補: 持ち物の余りは捨てる。チェストの余りは、家の中なら出しに行く。余りがなければどちらも出ない', () => {
  const bot = botWith({ wheat_seeds: 40 })
  const c = candidatesOf(bot, stateOf({ farms: 1, stored: 300 }))
  const toss = c.find((x) => x.verb === 'toss')
  assert.deepEqual(toss.items, [{ item: 'wheat_seeds', count: 40 }])
  const take = c.find((x) => x.verb === 'withdraw')
  assert.equal(take.id, 'take 236 surplus wheat_seeds from the chest at 1,69,1 to throw away')
  assert.equal(take.count, 236)
  // 外では出しに行かない
  assert.ok(!candidatesOf(botWith({ wheat_seeds: 40 }, { inside: false }), stateOf({ farms: 1, stored: 300 })).some((x) => x.verb === 'withdraw'))
  // 余りなし
  const none = candidatesOf(botWith({ wheat_seeds: 40 }), stateOf({ farms: 1, stored: 10 }))
  assert.ok(!none.some((x) => x.verb === 'toss' || x.verb === 'withdraw'))
})

test('余った種は預けない（tidy の預ける分から外す）', () => {
  const bot = botWith({ wheat_seeds: 100, oak_log: 3 })
  const tidy = candidatesOf(bot, stateOf({ farms: 1 })).find((x) => x.verb === 'tidy')
  assert.ok(tidy, '預ける候補が出る')
  const seeds = tidy.stow.find((s) => s.item === 'wheat_seeds')
  assert.equal(seeds?.count, 64) // 100 のうち、捨てる 36 を引いた分
})

test('toss の道具: 余った種は捨てられる。余りがなければ断る', async () => {
  const runs = []
  const make = (bot, state) => createTools({ bot, state, knowledge: k, run: async (c) => { runs.push(c); return { ok: true, result: 'done', seconds: 0 } }, candidates: () => [], check: async () => [] })
  const heavy = make(botWith({ wheat_seeds: 100 }), stateOf({ farms: 1 }))
  assert.equal((await heavy('toss', { item: 'wheat_seeds', intent: 'x' })).ok, true)
  assert.deepEqual(runs[0].items, [{ item: 'wheat_seeds', count: 36 }])
  await heavy('toss', { item: 'wheat_seeds', count: 500, intent: 'x' })
  assert.deepEqual(runs[1].items, [{ item: 'wheat_seeds', count: 36 }]) // 余りを超えては捨てない
  const all = make(botWith({ wheat_seeds: 100, dirt: 100 }), stateOf({ farms: 1 }))
  await all('toss', { intent: 'x' })
  assert.ok(runs[2].items.some((i) => i.item === 'wheat_seeds' && i.count === 36))
  const fine = make(botWith({ wheat_seeds: 64 }), stateOf({ farms: 1 }))
  assert.match((await fine('toss', { item: 'wheat_seeds', intent: 'x' })).result, /one stack per farm/)
})
