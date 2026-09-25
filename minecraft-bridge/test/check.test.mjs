import { test } from 'node:test'
import assert from 'node:assert/strict'
import minecraftData from 'minecraft-data'
import { Knowledge } from '../src/knowledge.mjs'
import { checkConditions } from '../src/goals.mjs'

const k = new Knowledge(minecraftData('1.21.4'))
const world = (inventory) => ({ inventory, blocks: {}, mobs: {}, table: null, unlocked: () => true, dig: () => [], hunt: () => [] })
const bot = { time: { timeOfDay: 1000 }, entity: { position: { floored: () => ({ x: 0, y: 70, z: 0 }) } } }
const state = { home: null, plan: null, goal: { spec: { predicate: 'at_home' } } }

test('条件は目標に触れずに判定する', () => {
  const r = checkConditions([{ predicate: 'have', item: 'stick', count: 2 }, { predicate: 'have', item: 'planks', count: 4 }],
    bot, state, k, world({ stick: 3 }))
  assert.deepEqual(r.map((c) => c.met), [true, false])
  assert.deepEqual(r[0].spec, { predicate: 'have', item: 'stick', count: 2 })
  assert.deepEqual(state.goal, { spec: { predicate: 'at_home' } })
})

test('世界から判定できる述語だけが条件になれる', () => {
  assert.throws(() => checkConditions([{ predicate: 'explored', distance: 30 }], bot, state, k, world({})), /cannot be a condition/)
  assert.throws(() => checkConditions([{ predicate: 'have', item: 'nonsense_item', count: 1 }], bot, state, k, world({})))
  assert.throws(() => checkConditions([{ predicate: 'placed', item: 'torch', where: 'home' }], bot, state, k, world({})), /only placed\(bed, home\)/)
})

test('まだ判定できない条件は未達とし、ほかの条件の判定は続ける', () => {
  const r = checkConditions([{ predicate: 'built' }, { predicate: 'placed', item: 'bed', where: 'home' }, { predicate: 'have', item: 'stick', count: 1 }],
    bot, state, k, world({ stick: 1 }))
  assert.deepEqual(r.map((c) => c.met), [false, false, true])
  assert.match(r[0].lines[0], /no house plan/)
  assert.match(r[1].lines[0], /no home yet/)
})
