import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { Knowledge } from '../src/knowledge.mjs'
import { solve } from '../src/solver.mjs'
import { makeGoal, evaluate, checkConditions } from '../src/goals.mjs'
import { chestSpot } from '../src/home.mjs'
import { ground } from '../src/candidates.mjs'
import { newMemory, rememberChest, storedCounts } from '../src/memory.mjs'
import { takeable } from '../src/world.mjs'

const { Vec3 } = vec3Pkg
const v = (x, y, z) => new Vec3(x, y, z)
const md = minecraftData('1.21.4')
const k = new Knowledge(md)
const world = ({ inventory = {}, stored = {}, blocks = {} } = {}) =>
  ({ inventory, stored, blocks, mobs: {}, table: 'near', unlocked: () => true, dig: () => [], hunt: () => [] })
const kinds = (r) => r.leaves.map((l) => `${l.kind}:${l.item ?? ''}:${l.count ?? ''}`)

// y 70 に 3x3 の室内（x 1..3、z 1..3）、ドアは (2, 70, 4)。ドアからベッドへの列は x = 2
const home = { door: v(2, 70, 4), inside: v(2, 70, 3), outside: v(2, 70, 5), min: v(1, 70, 1), max: v(3, 70, 3), bed: null, breach: [] }
const blockAt = (p) => (p.y < 70 ? { name: 'stone', boundingBox: 'block' } : { name: 'air', boundingBox: 'empty' })

test('チェストにある分は集める前に取り出し、足りない分だけ集める', () => {
  const all = solve(k, world({ stored: { oak_log: 5 }, blocks: { oak_log: 9 } }), [{ spec: 'log', count: 3 }])
  assert.deepEqual(kinds(all), ['withdraw:oak_log:3'])
  const part = solve(k, world({ stored: { oak_log: 2 }, blocks: { oak_log: 9 } }), [{ spec: 'log', count: 5 }])
  assert.deepEqual(kinds(part), ['withdraw:oak_log:2', 'dig:oak_log:3'])
})

test('stored(): まずチェストを作って置き、それから持ち物を入れる', () => {
  const state = { home, plan: null, memory: newMemory() }
  const bot = { entity: { position: v(2, 70, 2) }, time: { timeOfDay: 1000 }, inventory: { items: () => [{ name: 'oak_log', count: 4 }] } }
  const goal = makeGoal({ predicate: 'stored', item: 'log', count: 10 }, bot, state, k)

  const noChest = evaluate(bot, { ...state, goal }, k, world({ inventory: { oak_log: 4 } }))
  assert.equal(noChest.met, false)
  assert.ok(noChest.lines.some((l) => l.startsWith('have 1 chest (0/1): craft chest')), noChest.lines.join('\n'))
  assert.ok(kinds(noChest).includes('craft:oak_planks:'), kinds(noChest).join()) // チェスト用の板材 8 枚

  // チェストを置くのは前進で、残りの作業は減る（frun5 では増えて停滞と判定された）
  rememberChest(state.memory, v(1, 70, 1), {}, 0)
  const placed = evaluate(bot, { ...state, goal }, k, world({ inventory: { oak_log: 4 } }))
  assert.ok(placed.remaining < noChest.remaining, `置いた後 ${placed.remaining}、置く前 ${noChest.remaining}`)

  rememberChest(state.memory, v(1, 70, 1), { oak_log: 3 }, 0)
  const r = evaluate(bot, { ...state, goal }, k, world({ inventory: { oak_log: 4 }, stored: storedCounts(state.memory) }))
  assert.equal(r.lines[0], 'log in the chests (3/10)')
  assert.deepEqual(r.leaves.find((l) => l.kind === 'deposit').items, [{ item: 'oak_log', count: 4 }])
  assert.ok(!kinds(r).some((l) => l.startsWith('withdraw')), '入れ直す物は取り出さない')

  rememberChest(state.memory, v(1, 70, 1), { oak_log: 10 }, 0)
  assert.equal(evaluate(bot, { ...state, goal }, k, world()).met, true)
})

test('stored() は覚えているチェストの中身から判定する条件', () => {
  const memory = newMemory()
  rememberChest(memory, v(1, 70, 1), { bread: 16 }, 0)
  const bot = { entity: { position: v(2, 70, 2) }, time: { timeOfDay: 1000 } }
  const [r] = checkConditions([{ predicate: 'stored', item: 'food', count: 16 }], bot, { home, plan: null, memory }, k, world())
  assert.equal(r.met, true)
})

test('チェストはドアからベッドへの列を避け、ドアから遠い所に置く', () => {
  assert.deepEqual(chestSpot({ blockAt }, home), v(1, 70, 1))
})

test('家の中で持ち物が一杯なら、余っている大きいスタックからチェストに入れる候補を出す', () => {
  const memory = newMemory()
  rememberChest(memory, v(1, 70, 1), {}, 0)
  const items = [{ name: 'cobblestone', count: 60 }, { name: 'dirt', count: 30 }, { name: 'bread', count: 5 }, { name: 'wooden_sword', count: 1 }, { name: 'oak_log', count: 20 }]
  const bot = {
    entity: { position: v(2.5, 70, 2.5) },
    entities: {},
    time: { timeOfDay: 1000 },
    health: 20,
    food: 20,
    heldItem: null,
    blockAt: () => null, // 明るさのデータがない: 暗いとは判定しない
    registry: md,
    inventory: { items: () => items, emptySlotCount: () => 3 }
  }
  const state = { home, plan: null, unreachableDrops: new Set(), memory, goal: { spec: { predicate: 'have', item: 'log', count: 30 } } }
  const { candidates } = ground(bot, state, k, world(), { leaves: [] })
  assert.deepEqual(candidates.filter((c) => c.verb === 'deposit').map((c) => c.id), [
    'put 60 cobblestone in the chest at 1,70,1',
    'put 30 dirt in the chest at 1,70,1'
  ])
})

test('中目標がチェストに取っておく物は取り出さない（飢えているときの食料は除く）', () => {
  const state = { home, plan: null, memory: newMemory() }
  const bot = { entity: { position: v(2, 70, 2) }, time: { timeOfDay: 1000 }, inventory: { items: () => [] } }
  const goal = makeGoal({ predicate: 'have', item: 'food', count: 6, keep: [{ item: 'food', count: 10 }] }, bot, state, k)
  assert.equal(goal.keep[0].food, true)

  const stored = { mutton: 4, oak_log: 5 }
  assert.deepEqual(takeable(stored, goal.keep, false), { mutton: 0, oak_log: 5 })
  assert.deepEqual(takeable(stored, goal.keep, true), stored)
  const logs = makeGoal({ predicate: 'have', item: 'log', count: 3, keep: [{ item: 'log', count: 2 }] }, bot, state, k).keep
  assert.deepEqual(takeable(stored, logs, true), { mutton: 4, oak_log: 3 })

  // frun5: 備蓄のための have(food) が、入れたばかりの羊肉を取り出した
  const r = solve(k, world({ stored: takeable(stored, goal.keep, false) }), [{ spec: 'food', count: 6 }])
  assert.ok(!kinds(r).some((l) => l.startsWith('withdraw')), kinds(r).join())
})

test('check はボットに手に入れる手段がない物を挙げる（街の段階でそれを求めてはならない）', () => {
  const bot = { entity: { position: v(2, 70, 2) }, time: { timeOfDay: 1000 }, inventory: { items: () => [] } }
  const state = { home, plan: null, memory: newMemory() }
  // 何もない状態から: 原木から……と作った石のツルハシで鉄の原石を掘り、精錬する（以前はソルバーには深すぎた）
  const [sword] = checkConditions([{ predicate: 'have', item: 'iron_sword', count: 1 }], bot, state, k, world())
  assert.equal(sword.met, false)
  assert.deepEqual(sword.impossible, [])
  const [bedrock] = checkConditions([{ predicate: 'have', item: 'bedrock', count: 1 }], bot, state, k, world())
  assert.deepEqual(bedrock.impossible, ['no way to get bedrock'])
})

test('集めた物は 1 個ずつ運ばない: たまるか、ここで集める物がなくなるか、夕方になってから入れに行く', () => {
  const memory = newMemory()
  rememberChest(memory, v(1, 70, 1), {}, 0)
  const state = { home, plan: null, memory }
  const at = (logs, timeOfDay = 1000, pos = v(40, 70, 40)) => {
    const bot = { entity: { position: pos }, time: { timeOfDay }, inventory: { items: () => [{ name: 'oak_log', count: logs }], emptySlotCount: () => 30 } }
    const goal = makeGoal({ predicate: 'stored', item: 'log', count: 40 }, bot, state, k)
    const r = evaluate(bot, { ...state, goal }, k, world({ inventory: { oak_log: logs }, blocks: { oak_log: 9 } }))
    return r.leaves.some((l) => l.kind === 'deposit')
  }
  assert.equal(at(4), false) // 遠くで 4 本: 集め続ける
  assert.equal(at(32), true) // たまった
  assert.equal(at(4, 12500), true) // 夕方: どうせ帰る
  assert.equal(at(4, 1000, v(3, 70, 6)), true) // 家のそば
})

test('ほかの中目標の物がそばで取れるなら一緒に取る（木を見つけたら原木と、葉から苗木）', () => {
  const state = { home, plan: null, memory: newMemory(), unreachableDrops: new Set() }
  const bot = {
    entity: { position: v(40, 70, 40) },
    entities: {},
    time: { timeOfDay: 1000, age: 0 },
    health: 20,
    food: 20,
    heldItem: null,
    blockAt: () => null,
    inventory: { items: () => [] }
  }
  const goal = makeGoal({ predicate: 'have', item: 'log', count: 8, also: [{ item: 'sapling', count: 2 }, { item: 'nonsense_item', count: 1 }] }, bot, state, k)
  assert.deepEqual(goal.also.map((a) => a.item), ['sapling'])
  const w = {
    ...world({ blocks: { oak_log: 9, oak_leaves: 20 } }),
    dig: (name) => name === 'oak_log' ? [v(42, 70, 40)] : name === 'oak_leaves' ? [v(43, 73, 40), v(60, 72, 40)] : []
  }
  const status = evaluate(bot, { ...state, goal }, k, w)
  const { candidates } = ground(bot, { ...state, goal }, k, w, status)
  const leaves = candidates.filter((c) => c.target === 'oak_leaves')
  assert.deepEqual(leaves.map((c) => c.id), ['dig oak_leaves at 43,73,40']) // 遠い葉は出さない
  assert.equal(leaves[0].also, 'sapling for another mid goal')
  assert.ok(candidates.some((c) => c.id === 'dig oak_log at 42,70,40'))
})
