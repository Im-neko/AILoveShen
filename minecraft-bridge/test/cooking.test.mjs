import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { Knowledge } from '../src/knowledge.mjs'
import { cooking } from '../src/cooking.mjs'
import { ground } from '../src/candidates.mjs'
import { makeGoal, evaluate } from '../src/goals.mjs'
import { newMemory, rememberChest } from '../src/memory.mjs'

const { Vec3 } = vec3Pkg
const v = (x, y, z) => new Vec3(x, y, z)
const md = minecraftData('1.21.4')
const k = new Knowledge(md)
const FURNACE = { name: 'furnace', position: v(3, 70, 2) } // 家の中（家の min 1,70,1 〜 max 3,70,3）
const home = { door: v(2, 70, 4), inside: v(2, 70, 3), outside: v(2, 70, 5), min: v(1, 70, 1), max: v(3, 70, 3), bed: null, breach: [] }
const world = { inventory: {}, stored: {}, blocks: {}, mobs: {}, table: 'near', unlocked: () => true, dig: () => [], hunt: () => [] }

function bot (items, { furnace = true } = {}) {
  return {
    registry: md,
    entity: { position: v(2, 70, 6) },
    entities: {},
    time: { timeOfDay: 1000 },
    health: 20,
    food: 20,
    heldItem: null,
    blockAt: (p) => (furnace && p.equals(FURNACE.position) ? FURNACE : null),
    findBlock: ({ matching }) => (furnace && matching === md.blocksByName.furnace.id ? FURNACE : null),
    findBlocks: ({ matching }) => (furnace && matching === md.blocksByName.furnace.id ? [FURNACE.position] : []),
    inventory: { items: () => Object.entries(items).map(([name, count]) => ({ name, count })), emptySlotCount: () => 20 }
  }
}

test('燃料を持っていれば生肉をかまどで焼く。焼く物・燃料・かまどのどれかが無ければ候補に出さない', () => {
  assert.deepEqual(cooking(bot({ beef: 5, oak_planks: 4 }), k), {
    furnace: { position: FURNACE.position }, input: 'beef', product: 'cooked_beef', count: 5, fuel: 'oak_planks', fuelCount: 4
  })
  assert.equal(cooking(bot({ beef: 5, coal: 1 }), k).fuelCount, 1)
  assert.equal(cooking(bot({ bread: 5, coal: 1 }), k), null)
  assert.equal(cooking(bot({ beef: 5 }), k), null)
  assert.equal(cooking(bot({ beef: 5, coal: 1 }, { furnace: false }), k), null)
})

test('焼く候補は目標に関係なく出す', () => {
  const state = { home, plan: null, unreachableDrops: new Set(), memory: newMemory() }
  const { candidates } = ground(bot({ beef: 3, coal: 1 }), state, k, world, { leaves: [] })
  assert.ok(candidates.some((c) => c.id === 'cook 3 beef in the furnace at 3,70,2' && c.verb === 'smelt' && c.fuel === 'coal'))
})

test('食料の備蓄には、焼ける所では生肉を焼いてから入れ、焼けない所では生のまま入れる', () => {
  const memory = newMemory()
  rememberChest(memory, v(1, 70, 1), {}, 0)
  const state = { home, plan: null, memory }
  const deposits = (b) => {
    const goal = makeGoal({ predicate: 'stored', item: 'food', count: 10 }, b, state, k)
    return evaluate(b, { ...state, goal }, k, world).leaves.find((l) => l.kind === 'deposit')?.items ?? []
  }
  assert.deepEqual(deposits(bot({ beef: 4, bread: 2, coal: 1 })), [{ item: 'bread', count: 2 }])
  assert.deepEqual(deposits(bot({ beef: 4, coal: 1 }, { furnace: false })), [{ item: 'beef', count: 4 }])
})

test('生肉を持っていてかまどがどこにもなければ、かまどを作る・置く手を出す（焼けば満腹度が 2.7 倍）', () => {
  const state = { home, plan: null, memory: newMemory() }
  const leaves = (b, w = world) => {
    const goal = makeGoal({ predicate: 'have', item: 'dirt', count: 1 }, b, state, k)
    return evaluate(b, { ...state, goal }, k, w).leaves
  }
  const craftsFurnace = (ls) => ls.some((l) => l.kind === 'craft' && l.item === 'furnace' && /furnace to cook/.test(l.also))
  const w = { ...world, inventory: { beef: 3, cobblestone: 8 } }
  assert.ok(craftsFurnace(leaves(bot({ beef: 3, cobblestone: 8 }, { furnace: false }), w)))
  // 持っているなら置く
  assert.ok(leaves(bot({ beef: 3, furnace: 1 }, { furnace: false })).some((l) => l.kind === 'place' && l.item === 'furnace'))
  // かまどがあれば出さない。生肉が少なければ出さない。石がなければ出せない
  assert.ok(!craftsFurnace(leaves(bot({ beef: 3, cobblestone: 8 }), w)))
  assert.ok(!craftsFurnace(leaves(bot({ beef: 2, cobblestone: 8 }, { furnace: false }), { ...world, inventory: { beef: 2, cobblestone: 8 } })))
  assert.ok(!craftsFurnace(leaves(bot({ beef: 3 }, { furnace: false }), { ...world, inventory: { beef: 3 } })))
})

test('食べる候補は、飢えそうなときだけ urgent を付ける（聞かずに食べさせる）', () => {
  const state = { home, plan: null, unreachableDrops: new Set(), memory: newMemory() }
  const eat = (food) => {
    const b = bot({ beef: 3 })
    b.food = food
    return ground(b, state, k, world, { leaves: [] }).candidates.find((c) => c.verb === 'eat')
  }
  assert.equal(eat(5).urgent, true)
  assert.equal(eat(9)?.urgent, undefined)
})

test('手持ちの食べ物の個数は have(food) と同じ数え方（腐った肉などは数えない）', async () => {
  const { foodCount } = await import('../src/cooking.mjs')
  assert.equal(foodCount(bot({ beef: 3, bread: 2, rotten_flesh: 5, spider_eye: 1, cobblestone: 40 }), k), 5)
  assert.equal(foodCount(bot({ dirt: 3 }), k), 0)
  // チェストの中身の合計（名前ごとの個数）も同じ数え方: 腐った肉などは数えない
  const { foodIn } = await import('../src/cooking.mjs')
  assert.equal(foodIn({ beef: 3, bread: 2, rotten_flesh: 5, torch: 64 }, k), 5)
  assert.equal(foodIn({}, k), 0)
})

test('満腹度の合計: 個数でなく回復量で数える。生肉は、かまどが分かっているときだけ焼いた値で数える', async () => {
  const { foodPointsIn } = await import('../src/cooking.mjs')
  // じゃがいも 8 個は 8、焼いた牛 8 個は 64。腐った肉・クモの目は数えない
  assert.equal(foodPointsIn({ potato: 8 }, k), 8)
  assert.equal(foodPointsIn({ cooked_beef: 8 }, k), 64)
  assert.equal(foodPointsIn({ bread: 2, rotten_flesh: 5, spider_eye: 1, cobblestone: 40 }, k), 10)
  // 生の牛 4 個: かまどがなければ 3 × 4、あれば焼いた値 8 × 4
  assert.equal(foodPointsIn({ beef: 4 }, k, false), 12)
  assert.equal(foodPointsIn({ beef: 4 }, k, true), 32)
  assert.equal(foodPointsIn({}, k), 0)
})

test('食べる物は、捨てる点数が少ないものから。満腹に近いときに焼いた肉を食べて捨てない', () => {
  const state = { home, plan: null, unreachableDrops: new Set(), memory: newMemory() }
  const eat = (items, food, health = 20) => {
    const b = bot(items)
    b.food = food
    b.health = health
    return ground(b, state, k, world, { leaves: [] }).candidates.find((c) => c.verb === 'eat')
  }
  // 満腹度 19（あと 1）に焼いた牛（8）は 7 点捨てる: 出さない。じゃがいも（1）なら出す
  assert.equal(eat({ cooked_beef: 2 }, 19), undefined)
  assert.equal(eat({ cooked_beef: 2, potato: 1 }, 19).item, 'potato')
  // あと 8 ならちょうどの焼いた牛。あと 5 ならパン（5）が牛（8、3 捨てる）より先
  assert.equal(eat({ cooked_beef: 2, bread: 2 }, 12).item, 'cooked_beef')
  assert.equal(eat({ cooked_beef: 2, bread: 2 }, 15).item, 'bread')
  // 少し捨てるだけなら出す（あと 5 に牛 8: 3 捨てる）
  assert.equal(eat({ cooked_beef: 2 }, 15).item, 'cooked_beef')
})

test('体力が減っていて満腹度が 18 に届かないなら、捨てても満腹度 18 まで食べる（敵がいなければ聞かずに）', () => {
  const state = { home, plan: null, unreachableDrops: new Set(), memory: newMemory() }
  const eat = (items, food, health) => {
    const b = bot(items)
    b.food = food
    b.health = health
    return ground(b, state, k, world, { leaves: [] }).candidates.find((c) => c.verb === 'eat')
  }
  // 満腹度 17 に焼いた牛 1 個: 体力満タンなら食べない（5 捨てる）が、体力 12 なら食べる
  assert.equal(eat({ cooked_beef: 1 }, 17, 20), undefined)
  const c = eat({ cooked_beef: 1 }, 17, 12)
  assert.equal(c.item, 'cooked_beef')
  assert.equal(c.urgent, true)
  assert.equal(c.healTo, 18)
  // 体力が 17 以上（HEAL_BELOW 16 を超える）なら、そこまでしない
  assert.equal(eat({ cooked_beef: 1 }, 17, 18), undefined)
  // 満腹度 18 以上なら食べる必要はない
  assert.equal(eat({ cooked_beef: 1 }, 18, 12), undefined)
  // かまどがなく生肉だけでも、体力が減っているなら食べる
  assert.equal(eat({ beef: 2 }, 12, 12).item, 'beef')
})

test('食べる手は、満腹度が 20 になるまで同じ物を続けて食べる。次の 1 個の捨てる点数が多ければやめる', async () => {
  const { PRIMITIVES } = await import('../src/primitives.mjs')
  const run = async (items, food, c) => {
    const inv = { ...items }
    const b = {
      registry: md,
      food,
      inventory: { items: () => Object.entries(inv).filter(([, n]) => n > 0).map(([name, count]) => ({ name, count })) },
      equip: async () => {},
      consume: async () => { inv[c.item]--; b.food = Math.min(20, b.food + md.foodsByName[c.item].foodPoints) }
    }
    const result = await PRIMITIVES.eat(b, {}, c, new AbortController().signal)
    return { result, left: inv[c.item], food: b.food }
  }
  // 満腹度 4 に焼いた牛（8）: 4→12→20 の 2 個で止まる（3 個目は満腹で食べられない）
  let r = await run({ cooked_beef: 5 }, 4, { item: 'cooked_beef' })
  assert.deepEqual([r.food, r.left], [20, 3])
  assert.match(r.result, /ate 2 cooked_beef, hunger 4 -> 20/)
  // 満腹度 11 に牛（8）: 1 個で 19。次の 1 個は 7 点捨てるのでやめる
  r = await run({ cooked_beef: 5 }, 11, { item: 'cooked_beef' })
  assert.deepEqual([r.food, r.left], [19, 4])
  // 回復のため（healTo 18）なら、満腹度が 18 に届くまでは捨ててもやめない
  r = await run({ cooked_beef: 5 }, 9, { item: 'cooked_beef', healTo: 18 })
  assert.deepEqual([r.food, r.left], [20, 3])
  await assert.rejects(run({ cooked_beef: 1 }, 20, { item: 'cooked_beef' }), /already full/)
})

test('かまどの燃料が尽きて入力が焼けないまま残ったら取り戻す。記録も「焼けているはず」にしない', async () => {
  const { tendFurnace } = await import('../src/primitives.mjs')
  const { newMemory } = await import('../src/memory.mjs')
  const pos = v(60, 78, -336)
  const slots = { input: { name: 'kelp', count: 3 }, output: null, fuel: null }
  const furnace = {
    fuel: 0,
    inputItem: () => slots.input,
    outputItem: () => slots.output,
    fuelItem: () => slots.fuel,
    takeOutput: async () => { slots.output = null },
    takeInput: async () => { slots.input = null },
    close () {}
  }
  const b = {
    registry: md,
    time: { age: 5000 },
    openFurnace: async () => furnace,
    waitForTicks: async () => {},
    inventory: { items: () => [] }
  }
  const state = { memory: newMemory(), home }
  const block = { name: 'furnace', position: pos }
  const result = await tendFurnace(b, state, { pos }, block, new AbortController().signal)
  assert.match(result, /ran out of fuel: took back 3 kelp/)
  assert.equal(slots.input, null)
  assert.ok(!/still smelting/.test(result))
  // 記録（ソルバーの「焼けているもの」）に、昆布 3 個が焼き上がる前提を残さない
  assert.deepEqual(Object.values(state.memory.furnaces ?? {}), []) // 記録ごと消える
  // 燃料があって焼いている最中なら、取り戻さない
  slots.input = { name: 'kelp', count: 3 }
  slots.fuel = { name: 'coal', count: 1 }
  b.waitForTicks = async () => { slots.input = null } // 焼き終わる
  const done = await tendFurnace(b, state, { pos }, block, new AbortController().signal)
  assert.ok(!/took back/.test(done))
})

test('smelt の道具は、持っている燃料で焼ける数までしか入れない。足りないぶんは知らせる', async () => {
  const { createTools } = await import('../src/tools.mjs')
  const call = async (inv, args) => {
    const b = bot(inv)
    const seen = []
    const tools = createTools({ bot: b, state: { home, memory: newMemory(), plan: null }, knowledge: k, run: async (c) => { seen.push(c); return { ok: true, result: 'ran' } }, candidates: () => [], check: () => [] })
    const r = await tools(args.name ?? 'smelt', args)
    if (!seen.length) assert.fail(JSON.stringify(r))
    return { r, c: seen[0] }
  }
  // 板材 1 枚（1.5 個ぶん）では、牛肉 4 個のうち 1 個だけ
  let { c } = await call({ beef: 4, oak_planks: 1 }, { input: 'beef', count: 4 })
  assert.deepEqual([c.count, c.short, c.fuel, c.fuelCount], [1, 3, 'oak_planks', 1])
  // 石炭 1 個は 8 個ぶん: 4 個全部
  ;({ c } = await call({ beef: 4, coal: 1 }, { input: 'beef', count: 4 }))
  assert.deepEqual([c.count, c.short], [4, undefined])
})
