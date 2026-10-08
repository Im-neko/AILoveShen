// 種の余り（設計書 117）: 残すのは畑の数 × 1 スタック（持ち物とチェストの合計）。超えた分は捨てる
import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { Knowledge } from '../src/knowledge.mjs'
import { ground, describe } from '../src/candidates.mjs'
import { createTools } from '../src/tools.mjs'
import { PRIMITIVES } from '../src/primitives.mjs'
import { inTown } from '../src/facilities.mjs'
import { seedLimit, seedSurplus, seedsToToss, seedsToTake, tossSpot, TOSS_AWAY } from '../src/seeds.mjs'

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

test('余りは持ち物とチェストの合計で決める。持ち物は残す数まではまく分で、捨てるのは超えた分。余りがあればチェストの種を全部出す', () => {
  const state = stateOf({ farms: 1, stored: 300 })
  const bot = botWith({ wheat_seeds: 40 })
  assert.deepEqual(seedSurplus(bot, state).map((x) => [x.item, x.surplus]), [['wheat_seeds', 276]])
  assert.deepEqual(seedsToToss(bot, state), [], '持ち物の 40 は残す数以内: まく種を捨てない')
  assert.deepEqual(seedsToTake(bot, state), [{ item: 'wheat_seeds', count: 300 }])
  // 持ち物だけで超えるなら、超えた分だけ
  const heavy = botWith({ wheat_seeds: 100 })
  assert.deepEqual(seedsToToss(heavy, stateOf({ farms: 1 })), [{ item: 'wheat_seeds', count: 36 }])
  assert.deepEqual(seedsToTake(heavy, stateOf({ farms: 1 })), [])
  // 持ち物が超え、チェストにもあるなら、持ち物の超えた分を捨て、チェストの種は出す（出したあと、全体で 1 スタックになるまで捨てる）
  assert.deepEqual(seedsToToss(heavy, stateOf({ farms: 1, stored: 300 })), [{ item: 'wheat_seeds', count: 36 }])
  assert.deepEqual(seedsToTake(heavy, stateOf({ farms: 1, stored: 300 })), [{ item: 'wheat_seeds', count: 300 }])
  // 余りがなければ何もない。ニンジン・ジャガイモは種に数えない
  assert.deepEqual(seedsToToss(botWith({ wheat_seeds: 64, carrot: 200, potato: 200 }), stateOf()), [])
  assert.deepEqual(seedsToTake(botWith({ wheat_seeds: 30 }), stateOf({ farms: 1, stored: 30 })), [])
})

test('チェストの種を出して捨てると、1 回で 1 スタックに収まる（何度も出し入れしない）', () => {
  let held = 1
  let stored = 300
  const state = stateOf({ farms: 1, stored })
  for (let round = 0; round < 3; round++) {
    state.memory.chests[0].contents.wheat_seeds = stored
    const bot = botWith({ wheat_seeds: held })
    const take = seedsToTake(bot, state)[0]?.count ?? 0
    held += take
    stored -= take
    state.memory.chests[0].contents.wheat_seeds = stored
    held -= seedsToToss(botWith({ wheat_seeds: held }), state)[0]?.count ?? 0
  }
  assert.deepEqual([held, stored], [64, 0])
})

test('目標が種そのものを集めているときは、捨てない', () => {
  const bot = botWith({ wheat_seeds: 100 })
  assert.deepEqual(seedsToToss(bot, stateOf(), new Set(['wheat_seeds'])), [])
})

const candidatesOf = (bot, state) => ground(bot, state, k, { dig: () => [], hunt: () => [] }, { leaves: [] }).candidates

test('候補: 持ち物の超えた分は街の外で捨てる。チェストの種は、家の中なら出しに行く。余りがなければどちらも出ない', () => {
  const state = stateOf({ farms: 1, stored: 300 })
  const bot = botWith({ wheat_seeds: 40 })
  const c = candidatesOf(bot, state)
  // 持ち物の 40 は、まく分: 捨てる候補は出ない
  assert.ok(!c.some((x) => x.verb === 'toss'))
  const take = c.find((x) => x.verb === 'withdraw')
  assert.match(take.id, /^take 300 wheat_seeds from the chest at 1,69,1/)
  assert.equal(take.count, 300)
  // 外では出しに行かない
  assert.ok(!candidatesOf(botWith({ wheat_seeds: 40 }, { inside: false }), state).some((x) => x.verb === 'withdraw'))
  // 空きスロットで持てる分だけ出す
  assert.equal(candidatesOf(botWith({ wheat_seeds: 40 }, { slots: 2 }), state).find((x) => x.verb === 'withdraw').count, 128)
  // 余りなし
  const none = candidatesOf(botWith({ wheat_seeds: 40 }), stateOf({ farms: 1, stored: 10 }))
  assert.ok(!none.some((x) => x.verb === 'toss' || x.verb === 'withdraw'))
  // 持ち物が超えた分は、街の外へ歩いて捨てる（足元に捨てると拾い直す）
  const heavy = candidatesOf(botWith({ wheat_seeds: 100 }), stateOf({ farms: 1 })).find((x) => x.verb === 'toss')
  assert.deepEqual(heavy.items, [{ item: 'wheat_seeds', count: 36 }])
  assert.ok(!heavy.inPlace)
  assert.ok(heavy.pos.distanceTo(home.inside) >= TOSS_AWAY - 2, `街から離れた所: ${heavy.pos}`)
  assert.ok(!inTown(stateOf({ farms: 1 }), heavy.pos))
  assert.equal(describe(heavy).yaw, undefined, 'yaw はモデルに見せない')
})

test('捨てる場所は街から離れた、立てる所で、向きは街から離れる向き。足場がない（読み込まれていない）なら null で、候補も出ない', () => {
  const bot = botWith({ wheat_seeds: 100 })
  const state = stateOf({ farms: 1 })
  const spot = tossSpot(bot, state)
  assert.ok(spot.pos.distanceTo(home.inside) >= TOSS_AWAY - 2)
  assert.ok(!inTown(state, spot.pos))
  // 向いた先は街から遠ざかる側（mineflayer の向き: 前は (-sin yaw, -cos yaw)）
  const fwd = new Vec3(-Math.sin(spot.yaw), 0, -Math.cos(spot.yaw))
  const away = new Vec3(spot.pos.x - home.inside.x, 0, spot.pos.z - home.inside.z).normalize()
  assert.ok(fwd.dot(away) > 0.99, `${fwd} ${away}`)
  // 近い順: ボットが東にいれば東側の場所
  const east = botWith({ wheat_seeds: 100 })
  east.entity.position = new Vec3(40.5, 69, 0.5)
  assert.ok(tossSpot(east, state).pos.x > 20)
  // 施設の近くは選ばない（畑のまわりで捨てると、畑仕事で拾い直す）
  const withFarm = stateOf({ farms: 1 })
  withFarm.facilities.wheat_farm_0.box = { min: { x: 20, y: 69, z: -5 }, max: { x: 40, y: 70, z: 5 } }
  assert.ok(!inTown(withFarm, tossSpot(east, withFarm).pos))
  // 地面が読み込まれていない
  const unloaded = botWith({ wheat_seeds: 100 })
  unloaded.blockAt = () => null
  assert.equal(tossSpot(unloaded, state), null)
  assert.ok(!candidatesOf(unloaded, stateOf({ farms: 1 })).some((x) => x.verb === 'toss'))
})

test('捨てる: 場所まで歩いてから、街から離れる向きに投げ、捨てた場所を覚える', async () => {
  const bot = botWith({ wheat_seeds: 100 })
  const order = []
  bot.pathfinder = {
    goto: async (goal) => { order.push(['goto', goal.x, goal.z]); bot.entity.position = new Vec3(goal.x + 0.5, goal.y, goal.z + 0.5) },
    setGoal: () => {}
  }
  bot.look = async (yaw) => { order.push(['look', yaw]) }
  bot.toss = async (id, meta, n) => { order.push(['toss', md.items[id].name, n]) }
  bot.time = { timeOfDay: 1000, age: 100000 }
  const state = stateOf({ farms: 1 })
  const c = candidatesOf(bot, state).find((x) => x.verb === 'toss')
  const result = await PRIMITIVES.toss(bot, state, c, new AbortController().signal)
  assert.match(result, /^threw away 36 wheat_seeds/)
  assert.deepEqual(order.map((o) => o[0]), ['goto', 'look', 'toss'])
  assert.equal(order[0][1], c.pos.x)
  assert.equal(order[1][1], c.yaw)
  // 捨てた場所は、家からも離れている（そこを覚える）
  assert.ok(Math.hypot(state.tossed.wheat_seeds.x - home.inside.x, state.tossed.wheat_seeds.z - home.inside.z) >= TOSS_AWAY - 3)
})

test('余った種は預けない（tidy の預ける分から外す）', () => {
  const bot = botWith({ wheat_seeds: 100, oak_log: 3 })
  const tidy = candidatesOf(bot, stateOf({ farms: 1 })).find((x) => x.verb === 'tidy')
  assert.ok(tidy, '預ける候補が出る')
  const seeds = tidy.stow.find((s) => s.item === 'wheat_seeds')
  assert.equal(seeds?.count, 64) // 100 のうち、捨てる 36 を引いた分
})

test('toss の道具: 余った種は街の外へ捨てに行く。余りがなければ断る。場所がなければ理由つきで断る', async () => {
  const runs = []
  const make = (bot, state) => createTools({ bot, state, knowledge: k, run: async (c) => { runs.push(c); return { ok: true, result: 'done', seconds: 0 } }, candidates: () => [], check: async () => [] })
  const heavy = make(botWith({ wheat_seeds: 100 }), stateOf({ farms: 1 }))
  assert.equal((await heavy('toss', { item: 'wheat_seeds', intent: 'x' })).ok, true)
  assert.deepEqual(runs[0].items, [{ item: 'wheat_seeds', count: 36 }])
  assert.ok(runs[0].pos.distanceTo(home.inside) >= TOSS_AWAY - 2 && !runs[0].inPlace)
  await heavy('toss', { item: 'wheat_seeds', count: 500, intent: 'x' })
  assert.deepEqual(runs[1].items, [{ item: 'wheat_seeds', count: 36 }]) // 余りを超えては捨てない
  // item なし: 余りの物（土）があればそれをその場で。なければ余った種を街の外で
  const all = make(botWith({ wheat_seeds: 100, dirt: 100 }), stateOf({ farms: 1 }))
  await all('toss', { intent: 'x' })
  assert.deepEqual(runs[2].items, [{ item: 'dirt', count: 36 }])
  assert.equal(runs[2].inPlace, true)
  await make(botWith({ wheat_seeds: 100 }), stateOf({ farms: 1 }))('toss', { intent: 'x' })
  assert.deepEqual(runs[3].items, [{ item: 'wheat_seeds', count: 36 }])
  assert.ok(runs[3].pos)
  const fine = make(botWith({ wheat_seeds: 64 }), stateOf({ farms: 1 }))
  assert.match((await fine('toss', { item: 'wheat_seeds', intent: 'x' })).result, /one stack per farm/)
  const noGround = botWith({ wheat_seeds: 100 })
  noGround.blockAt = () => null
  assert.match((await make(noGround, stateOf({ farms: 1 }))('toss', { item: 'wheat_seeds', intent: 'x' })).result, /no place outside the town/)
})
