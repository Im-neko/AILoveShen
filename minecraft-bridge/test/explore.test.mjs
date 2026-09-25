import { test } from 'node:test'
import assert from 'node:assert/strict'
import minecraftData from 'minecraft-data'
import { Knowledge } from '../src/knowledge.mjs'
import { makeGoal, evaluate } from '../src/goals.mjs'

const k = new Knowledge(minecraftData('1.21.4'))
const world = { inventory: {}, blocks: {}, mobs: {}, table: null, unlocked: () => true, dig: () => [], hunt: () => [] }
const bot = (x, z) => ({ time: { timeOfDay: 1000 }, entity: { position: { x, y: 70, z, floored: () => ({ x, y: 70, z }) } } })

test('目標は設定した場所から離れる向きに探し、行ったり来たりしない', () => {
  const state = { home: null, plan: null }
  const goal = makeGoal({ predicate: 'have', item: 'food', count: 1 }, bot(10, 20), state, k)
  assert.deepEqual(goal.exploreFrom, { x: 10, y: 70, z: 20 })

  const r = evaluate(bot(-11, 20), { ...state, goal }, k, world)
  const explore = r.leaves.filter((l) => l.kind === 'explore')
  assert.ok(explore.length > 0)
  for (const leaf of explore) assert.deepEqual(leaf.away, { x: 10, y: 70, z: 20 })
})
