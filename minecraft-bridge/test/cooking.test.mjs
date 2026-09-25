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
const FURNACE = { name: 'furnace', position: v(5, 70, 2) }
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
    blockAt: () => null,
    findBlock: ({ matching }) => (furnace && matching === md.blocksByName.furnace.id ? FURNACE : null),
    inventory: { items: () => Object.entries(items).map(([name, count]) => ({ name, count })), emptySlotCount: () => 20 }
  }
}

test('燃料を持っていれば生肉をかまどで焼く。焼く物・燃料・かまどのどれかが無ければ候補に出さない', () => {
  assert.deepEqual(cooking(bot({ beef: 5, oak_planks: 4 }), k), {
    furnace: FURNACE, input: 'beef', product: 'cooked_beef', count: 5, fuel: 'oak_planks', fuelCount: 4
  })
  assert.equal(cooking(bot({ beef: 5, coal: 1 }), k).fuelCount, 1)
  assert.equal(cooking(bot({ bread: 5, coal: 1 }), k), null)
  assert.equal(cooking(bot({ beef: 5 }), k), null)
  assert.equal(cooking(bot({ beef: 5, coal: 1 }, { furnace: false }), k), null)
})

test('焼く候補は目標に関係なく出す', () => {
  const state = { home, plan: null, unreachableDrops: new Set(), memory: newMemory() }
  const { candidates } = ground(bot({ beef: 3, coal: 1 }), state, k, world, { leaves: [] })
  assert.ok(candidates.some((c) => c.id === 'cook 3 beef in the furnace at 5,70,2' && c.verb === 'smelt' && c.fuel === 'coal'))
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
