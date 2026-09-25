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

test('名前付きの建物（built(name)）: 知らない名前はまだ未達（目標にはできない）、登録したものは置いた数で判定する（docs/design/25_builds.md）', async () => {
  const { BuildPlan } = await import('../src/build.mjs')
  const vec3 = (await import('vec3')).default
  // まだない名前: 条件としては未達（街の段階の確認）、目標にはできない
  const [unknown] = checkConditions([{ predicate: 'built', name: 'annex' }], bot, state, k, world({}))
  assert.equal(unknown.met, false)
  assert.match(unknown.lines[0], /no build named annex yet/)
  const { makeGoal } = await import('../src/goals.mjs')
  assert.throws(() => makeGoal({ predicate: 'built', name: 'annex' }, bot, state, k), /no build named annex/)
  const plan = new BuildPlan({ blocks: [{ x: 0, y: 0, z: 0, block: 'cobblestone' }, { x: 0, y: 1, z: 0, block: 'cobblestone' }], width: 1, depth: 1, height: 2, kind: 'build' })
  plan.origin = new vec3.Vec3(5, 69, 5)
  const placed = new Set(['5,69,5'])
  const withBlocks = { ...bot, inventory: { items: () => [] }, blockAt: (p) => ({ name: placed.has(`${p.x},${p.y},${p.z}`) ? 'cobblestone' : 'air', boundingBox: 'block' }) }
  const [r] = checkConditions([{ predicate: 'built', name: 'annex' }], withBlocks, { ...state, builds: { annex: plan } }, k, world({}))
  assert.equal(r.met, false)
  assert.deepEqual(r.spec, { predicate: 'built', name: 'annex' })
  assert.match(r.lines[0], /build annex placed 1\/2/)
})
