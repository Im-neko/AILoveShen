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

const { Vec3 } = vec3Pkg
const v = (x, y, z) => new Vec3(x, y, z)
const md = minecraftData('1.21.4')
const k = new Knowledge(md)
const world = ({ inventory = {}, stored = {}, blocks = {} } = {}) =>
  ({ inventory, stored, blocks, mobs: {}, table: 'near', unlocked: () => true, dig: () => [], hunt: () => [] })
const kinds = (r) => r.leaves.map((l) => `${l.kind}:${l.item ?? ''}:${l.count ?? ''}`)

// A 3x3 interior (x 1..3, z 1..3) at y 70, door at (2, 70, 4): the bed line is x = 2
const home = { door: v(2, 70, 4), inside: v(2, 70, 3), outside: v(2, 70, 5), min: v(1, 70, 1), max: v(3, 70, 3), bed: null, breach: [] }
const blockAt = (p) => (p.y < 70 ? { name: 'stone', boundingBox: 'block' } : { name: 'air', boundingBox: 'empty' })

test('what the chests hold is taken out before gathering, the rest gathered', () => {
  const all = solve(k, world({ stored: { oak_log: 5 }, blocks: { oak_log: 9 } }), [{ spec: 'log', count: 3 }])
  assert.deepEqual(kinds(all), ['withdraw:oak_log:3'])
  const part = solve(k, world({ stored: { oak_log: 2 }, blocks: { oak_log: 9 } }), [{ spec: 'log', count: 5 }])
  assert.deepEqual(kinds(part), ['withdraw:oak_log:2', 'dig:oak_log:3'])
})

test('stored(): a chest is made and placed first, then held items go in', () => {
  const state = { home, plan: null, memory: newMemory() }
  const bot = { entity: { position: v(2, 70, 2) }, time: { timeOfDay: 1000 }, inventory: { items: () => [{ name: 'oak_log', count: 4 }] } }
  const goal = makeGoal({ predicate: 'stored', item: 'log', count: 10 }, bot, state, k)

  const noChest = evaluate(bot, { ...state, goal }, k, world({ inventory: { oak_log: 4 } }))
  assert.equal(noChest.met, false)
  assert.ok(noChest.lines.some((l) => l.startsWith('have 1 chest (0/1): craft chest')), noChest.lines.join('\n'))
  assert.deepEqual(kinds(noChest), ['craft:oak_planks:']) // 8 planks for the chest, from the logs held

  rememberChest(state.memory, v(1, 70, 1), { oak_log: 3 }, 0)
  const r = evaluate(bot, { ...state, goal }, k, world({ inventory: { oak_log: 4 }, stored: storedCounts(state.memory) }))
  assert.equal(r.lines[0], 'log in the chests (3/10)')
  assert.deepEqual(r.leaves.find((l) => l.kind === 'deposit').items, [{ item: 'oak_log', count: 4 }])
  assert.ok(!kinds(r).some((l) => l.startsWith('withdraw')), 'never takes out what goes back in')

  rememberChest(state.memory, v(1, 70, 1), { oak_log: 10 }, 0)
  assert.equal(evaluate(bot, { ...state, goal }, k, world()).met, true)
})

test('stored() is a condition judged from the chests remembered', () => {
  const memory = newMemory()
  rememberChest(memory, v(1, 70, 1), { bread: 16 }, 0)
  const bot = { entity: { position: v(2, 70, 2) }, time: { timeOfDay: 1000 } }
  const [r] = checkConditions([{ predicate: 'stored', item: 'food', count: 16 }], bot, { home, plan: null, memory }, k, world())
  assert.equal(r.met, true)
})

test('the chest goes off the line from the door to the bed, far from the door', () => {
  assert.deepEqual(chestSpot({ blockAt }, home), v(1, 70, 1))
})

test('a full inventory in the house offers the biggest spare stacks to the chest', () => {
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
