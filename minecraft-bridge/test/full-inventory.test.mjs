import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { Knowledge } from '../src/knowledge.mjs'
import { ground } from '../src/candidates.mjs'
import { spareStacks, canHold, isTossable, isTossed } from '../src/spare.mjs'
import { nearbyDrops, PRIMITIVES, fight } from '../src/primitives.mjs'
import { createTools } from '../src/tools.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')
const k = new Knowledge(md)

// 2026-09-30 の持ち物（36 スロット全部が埋まって、落とした肉が拾えなかった）
const HELD = {
  dirt: 447, cobblestone: 320, granite: 35, diorite: 33, andesite: 7, gravel: 13, white_tulip: 33, orange_tulip: 8,
  light_gray_wool: 1, white_wool: 1, stone_sword: 1, wooden_sword: 1, torch: 9, coal: 3, oak_log: 2, oak_planks: 2, raw_copper: 7
}

function botWith (held = HELD, { slots = 0, drops = [], time = 1000 } = {}) {
  // 実際の持ち物と同じに、64 個ずつのスタックに分ける（最後のスタックだけ端数）
  const items = Object.entries(held).flatMap(([name, count]) =>
    Array.from({ length: Math.ceil(count / 64) }, (_, i) => ({ name, count: Math.min(64, count - 64 * i) })))
  const entities = {}
  drops.forEach(({ item, at }, i) => {
    entities[100 + i] = { id: 100 + i, name: 'item', position: at, getDroppedItem: () => ({ name: item, count: 1 }) }
  })
  return {
    entity: { position: new Vec3(0.5, 70, 0.5), yaw: 0 },
    entities,
    time: { timeOfDay: time, age: 100000 },
    health: 20,
    food: 20,
    heldItem: null,
    registry: md,
    inventory: { items: () => items, emptySlotCount: () => slots },
    blockAt: (p) => (p.y < 70 ? { name: 'stone', boundingBox: 'block' } : { name: 'air', boundingBox: 'empty' }),
    findBlock: () => null,
    toss: async () => {},
    look: async () => {}
  }
}

const stateOf = (extra = {}) => ({ home: null, plan: null, unreachableDrops: new Set(), ...extra })
const candidatesOf = (bot, state) => ground(bot, state, k, { dig: () => [], hunt: () => [] }, { leaves: [] }).candidates

test('余りは、土と丸石は 64 個ずつ残した分と、花崗岩・花などの要らない物。道具・食料・松明・鉱石は含めない', () => {
  const spare = Object.fromEntries(spareStacks(botWith()).map((s) => [s.item, s.count]))
  assert.deepEqual(spare, {
    dirt: 383, cobblestone: 256, granite: 35, diorite: 33, white_tulip: 33, orange_tulip: 8, gravel: 13, andesite: 7, light_gray_wool: 1, white_wool: 1
  })
  assert.ok(!isTossable('coal_ore') && !isTossable('stone_sword') && !isTossable('torch') && !isTossable('raw_copper'))
})

test('目標が使う物は余りに入れない（丸石を集める目標では丸石を捨てない）', () => {
  const keep = new Set(['cobblestone', 'dirt'])
  const spare = spareStacks(botWith(), keep).map((s) => s.item)
  assert.ok(!spare.includes('cobblestone') && !spare.includes('dirt') && spare.includes('granite'))
})

test('持ち物がほぼいっぱいで余りがあれば「捨てる」を出す。空きが十分あるか、余りがなければ出さない', () => {
  const toss = (bot, state) => candidatesOf(bot, state).find((c) => c.verb === 'toss')
  const c = toss(botWith(HELD, { slots: 0 }), stateOf())
  assert.ok(c && c.inPlace)
  assert.equal(c.id, 'throw away the spare items to free inventory slots') // id に数を入れない
  assert.equal(c.items.find((i) => i.item === 'dirt').count, 383)
  assert.ok(toss(botWith(HELD, { slots: 1 }), stateOf()))
  assert.ok(!toss(botWith(HELD, { slots: 2 }), stateOf()), '空きが 2 つあれば出さない')
  assert.ok(!toss(botWith({ dirt: 64, cobblestone: 64, stone_sword: 1 }, { slots: 0 }), stateOf()), '残す数しかない')
})

test('いっぱいのとき、入らない落とし物は拾う候補に出ない。まだ入るスタックがあれば出る', () => {
  const drops = [{ item: 'beef', at: new Vec3(2.5, 70, 0.5) }]
  const full = botWith(HELD, { slots: 0, drops })
  assert.ok(!candidatesOf(full, stateOf()).some((c) => c.verb === 'pickup'))
  assert.equal(canHold(full, 'beef'), false)
  assert.equal(canHold(full, 'dirt'), true) // 447 個の最後のスタックはまだ入る
  assert.ok(nearbyDrops(botWith(HELD, { slots: 5, drops }), stateOf(), 16).length === 1)
  const dirtDrop = botWith(HELD, { slots: 0, drops: [{ item: 'dirt', at: new Vec3(2.5, 70, 0.5) }] })
  assert.equal(nearbyDrops(dirtDrop, stateOf(), 16).length, 1)
})

test('捨てる: 余りを投げ、捨てた物を覚えて、その近くの同じ物は拾う候補に出さない', async () => {
  const thrown = []
  const bot = botWith(HELD, { slots: 0 })
  bot.toss = async (id, meta, n) => { thrown.push([md.items[id].name, n]) }
  const state = stateOf()
  const c = candidatesOf(bot, state).find((x) => x.verb === 'toss')
  const result = await PRIMITIVES.toss(bot, state, c, new AbortController().signal)
  assert.match(result, /^threw away 383 dirt, 256 cobblestone, 35 granite/)
  assert.deepEqual(thrown.find(([n]) => n === 'dirt'), ['dirt', 383])
  // 捨てた dirt が近くに落ちている: 拾う候補にも数えない。別の物と、遠くに落ちた同じ物は出す
  const lying = botWith(HELD, {
    slots: 5,
    drops: [{ item: 'dirt', at: new Vec3(3.5, 70, 0.5) }, { item: 'beef', at: new Vec3(4.5, 70, 0.5) }, { item: 'dirt', at: new Vec3(40.5, 70, 0.5) }]
  })
  const listed = nearbyDrops(lying, state, 60).map(({ e }) => e.getDroppedItem().name)
  assert.deepEqual(listed.sort(), ['beef', 'dirt'])
  assert.ok(isTossed(state, lying, Object.values(lying.entities)[0]))
  state.tossed.dirt.at -= 6001 // 5 分たったら、また拾ってよい
  assert.ok(!isTossed(state, lying, Object.values(lying.entities)[0]))
})

function toolsOf (bot, state = stateOf()) {
  const runs = []
  const callTool = createTools({
    bot,
    state,
    knowledge: k,
    run: async (c, label) => { runs.push({ c, label }); return { ok: true, result: 'done', seconds: 0 } },
    candidates: () => [],
    check: async () => []
  })
  return { callTool, runs }
}

test('toss の道具: item なしは余り全部、item ありはその物。捨てられない物と、余りがないときは断る', async () => {
  const { callTool, runs } = toolsOf(botWith(HELD, { slots: 0 }))
  assert.equal((await callTool('toss', { intent: 'x' })).ok, true)
  assert.equal(runs[0].c.verb, 'toss')
  assert.equal(runs[0].c.items.find((i) => i.item === 'dirt').count, 383)
  await callTool('toss', { item: 'granite', intent: 'x' })
  assert.deepEqual(runs[1].c.items, [{ item: 'granite', count: 35 }])
  await callTool('toss', { item: 'dirt', count: 100, intent: 'x' })
  assert.deepEqual(runs[2].c.items, [{ item: 'dirt', count: 100 }])
  assert.match((await callTool('toss', { item: 'stone_sword', intent: 'x' })).result, /not thrown away by this tool/)
  assert.match((await callTool('toss', { item: 'diamond', intent: 'x' })).result, /you have no diamond/)
  assert.equal(runs.length, 3)
  const few = toolsOf(botWith({ dirt: 30 }))
  assert.match((await few.callTool('toss', { intent: 'x' })).result, /nothing spare/)
})

test('pickup の道具: 入らないだけのときは、捨てるか預けるように断る', async () => {
  const bot = botWith(HELD, { slots: 0, drops: [{ item: 'beef', at: new Vec3(2.5, 70, 0.5) }] })
  assert.match((await toolsOf(bot).callTool('pickup', {})).result, /inventory is full.*toss/)
  assert.match((await toolsOf(botWith(HELD, { slots: 0 })).callTool('pickup', {})).result, /no item on the ground/)
})

test('deposit: 家にチェストがなければ、捨てるか作るかを教えて断る', async () => {
  const r = await toolsOf(botWith(HELD)).callTool('deposit', { item: 'dirt', intent: 'x' })
  assert.match(r.result, /no chest in the home.*toss/)
})

test('チェストを持っていて家に預ける先がなければ、置く候補を出す。あれば出さない', () => {
  const home = { inside: new Vec3(0, 69, 0), min: new Vec3(-2, 69, -2), max: new Vec3(2, 72, 2), breach: [] }
  const held = { ...HELD, chest: 1 }
  const put = (state) => candidatesOf(botWith(held, { slots: 8 }), state).some((c) => c.verb === 'place_chest')
  assert.ok(put(stateOf({ home, memory: { chests: [] } })))
  assert.ok(!put(stateOf({ home, memory: { chests: [{ x: 1, y: 69, z: 1, contents: {}, seen: 0 }] } })))
})

test('始めから 24 m より遠い相手（狩りの候補は 32 m まで）は、すぐ見失ったことにしない。始めより 4 m 以上離れたら見失う', async () => {
  const target = { id: 7, name: 'cow', height: 1.4, position: new Vec3(26, 64, 0) }
  let ticks = 0
  const controller = new AbortController()
  const bot = {
    entities: { 7: target },
    entity: { position: new Vec3(0, 64, 0) },
    pathfinder: { goal: null, setGoal (g) { this.goal = g } },
    waitForTicks: async () => { if (++ticks >= 3) controller.abort() }
  }
  assert.doesNotMatch(await fight(bot, target, controller.signal), /lost it/)
  // 走って逃げて始めより 4 m 以上離れた
  const runner = { id: 8, name: 'cow', height: 1.4, position: new Vec3(26, 64, 0) }
  const bot2 = { ...bot, entities: { 8: runner }, waitForTicks: async () => { runner.position = new Vec3(31, 64, 0) } }
  assert.match(await fight(bot2, runner, new AbortController().signal), /lost it \(31m away\)/)
})

test('家の中でチェストがあれば、捨てずに預ける（storeSpare）。チェストがなければ家の中でも捨てる', () => {
  const home = { inside: new Vec3(0, 69, 0), min: new Vec3(-2, 69, -2), max: new Vec3(2, 72, 2), door: new Vec3(0, 69, 3), breach: [] }
  const inside = botWith(HELD, { slots: 0 })
  inside.entity.position = new Vec3(0.5, 69, 0.5)
  const chest = { x: 1, y: 69, z: 1, contents: {}, seen: 0 }
  const verbs = (state) => candidatesOf(inside, state).map((c) => c.verb)
  assert.ok(!verbs(stateOf({ home, memory: { chests: [chest] } })).includes('toss'))
  assert.ok(verbs(stateOf({ home, memory: { chests: [chest] } })).includes('deposit'))
  assert.ok(verbs(stateOf({ home, memory: { chests: [] } })).includes('toss'))
})
