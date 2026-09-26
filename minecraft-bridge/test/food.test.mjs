import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { Knowledge } from '../src/knowledge.mjs'
import { ground } from '../src/candidates.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')
const k = new Knowledge(md)

function eats (food, items) {
  const bot = {
    entity: { position: new Vec3(0, 70, 0) },
    entities: {},
    time: { timeOfDay: 1000 },
    health: 20,
    food,
    heldItem: null,
    blockAt: () => null, // 明るさのデータがない: 暗いとは判定しない
    registry: md,
    inventory: { items: () => items.map((name) => ({ name, count: 1 })) }
  }
  const state = { home: null, plan: null, unreachableDrops: new Set() }
  const { candidates } = ground(bot, state, k, { dig: () => [], hunt: () => [] }, { leaves: [] })
  return candidates.filter((c) => c.verb === 'eat').map((c) => c.item)
}

test('腐った肉は、飢えていてほかに良い食べ物を持っていないときだけ食べる', () => {
  assert.deepEqual(eats(3, ['rotten_flesh']), ['rotten_flesh'])
  assert.deepEqual(eats(10, ['rotten_flesh']), [])
  assert.deepEqual(eats(3, ['rotten_flesh', 'bread']), ['bread'])
})

test('体力が危ないとき、帰宅は周りに危険があるときだけ候補に出す（回復には食料が要る）', () => {
  const home = { door: new Vec3(2, 70, 4), inside: new Vec3(2, 70, 3), outside: new Vec3(2, 70, 5), min: new Vec3(1, 70, 1), max: new Vec3(3, 70, 3), bed: null, breach: [] }
  const offered = (timeOfDay) => {
    const bot = {
      entity: { position: new Vec3(40, 70, 40) },
      entities: {},
      time: { timeOfDay },
      health: 4,
      food: 5,
      heldItem: null,
      blockAt: () => null, // 明るさのデータがない: 暗いとは判定しない
      registry: md,
      inventory: { items: () => [] }
    }
    const state = { home, plan: null, unreachableDrops: new Set() }
    return ground(bot, state, k, { dig: () => [], hunt: () => [] }, { leaves: [] }).candidates.some((c) => c.verb === 'go_home')
  }
  assert.equal(offered(1000), false, '昼で脅威がなければ食料探しを続ける')
  assert.equal(offered(18000), true, '夜は帰る')
})

test('剣がなければ狩る前に作る（作るだけでできるなら狩りの候補を外す）', async () => {
  const { makeGoal, evaluate } = await import('../src/goals.mjs')
  const state = { home: null, plan: null, memory: null }
  const bot = (items) => ({
    entity: { position: new Vec3(0, 70, 0) },
    time: { timeOfDay: 1000 },
    inventory: { items: () => Object.entries(items).map(([name, count]) => ({ name, count })) }
  })
  const world = (inventory) => ({ inventory, stored: {}, blocks: {}, mobs: { cow: 2 }, table: 'near', unlocked: () => true, dig: () => [], hunt: () => [] })
  const kinds = (inv) => {
    const b = bot(inv)
    const goal = makeGoal({ predicate: 'have', item: 'beef', count: 2 }, b, state, k)
    const r = evaluate(b, { ...state, goal }, k, world(inv))
    return r.leaves.map((l) => `${l.kind}${l.also ? '*' : ''}`)
  }
  // 板材と棒があれば: 剣を作るだけ（狩りは後）
  assert.deepEqual(kinds({ oak_planks: 4, stick: 2 }), ['craft*'])
  // 剣があれば狩る
  assert.deepEqual(kinds({ stone_sword: 1 }), ['kill'])
})

test('焼いた物を先に食べ、生肉は焼けるなら焼く（飢えそうなとき・焼く手段がないときだけ生で）', () => {
  const offer = (food, items, furnace = false) => {
    const bot = {
      entity: { position: new Vec3(0, 70, 0) },
      entities: {},
      time: { timeOfDay: 1000 },
      health: 20,
      food,
      heldItem: null,
      blockAt: () => null,
      registry: md,
      findBlock: () => (furnace ? { position: new Vec3(3, 70, 0), name: 'furnace' } : null),
      inventory: { items: () => Object.entries(items).map(([name, count]) => ({ name, count })) }
    }
    const state = { home: null, plan: null, unreachableDrops: new Set() }
    const { candidates } = ground(bot, state, k, { dig: () => [], hunt: () => [] }, { leaves: [] })
    return candidates.filter((c) => c.verb === 'eat' || c.id.startsWith('cook')).map((c) => c.id)
  }
  assert.deepEqual(offer(14, { beef: 3, cooked_beef: 1 }), ['eat cooked_beef'])
  assert.deepEqual(offer(14, { beef: 3, bread: 1 }), ['eat bread'])
  assert.deepEqual(offer(14, { beef: 3 }), []) // まだ待てる
  assert.deepEqual(offer(10, { beef: 3 }), ['eat beef']) // 焼く手段がない
  assert.deepEqual(offer(10, { beef: 3, coal: 1 }, true), ['cook 3 beef in the furnace at 3,70,0']) // 焼ける
  assert.deepEqual(offer(5, { beef: 3, coal: 1 }, true), ['eat beef', 'cook 3 beef in the furnace at 3,70,0']) // 飢えそう
})
