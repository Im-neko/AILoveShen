import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { Knowledge } from '../src/knowledge.mjs'
import { ground } from '../src/candidates.mjs'
import { recoverableDeath } from '../src/memory.mjs'
import { PRIMITIVES } from '../src/primitives.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')
const k = new Knowledge(md)
const MIN = 1200

test('死んで落とした持ち物は 4 分半まで取りに戻る。取れた・2 回行った・時間切れは戻らない', () => {
  const now = 100 * MIN
  const memory = (d) => ({ deaths: [{ x: 1, y: 70, z: 2, seen: now - 2 * MIN, ...d }] })
  assert.ok(recoverableDeath(memory({}), now))
  assert.equal(recoverableDeath(memory({ seen: now - 5 * MIN }), now), null)
  assert.equal(recoverableDeath(memory({ recovered: true }), now), null)
  assert.equal(recoverableDeath(memory({ tries: 2 }), now), null)
  assert.equal(recoverableDeath({ deaths: [] }, now), null)
})

function bot (age) {
  return {
    entity: { position: new Vec3(0.5, 70, 0.5) },
    entities: {},
    time: { timeOfDay: 1000, age },
    health: 20,
    food: 20,
    heldItem: null,
    registry: md,
    inventory: { items: () => [] },
    blockAt: (p) => (p.y < 70 ? { name: 'stone', boundingBox: 'block' } : { name: 'air', boundingBox: 'empty' }),
    findBlock: () => null
  }
}
const offered = (b, memory) => ground(b, { home: null, plan: null, unreachableDrops: new Set(), memory }, k, { dig: () => [], hunt: () => [] }, { leaves: [] })
  .candidates.filter((c) => c.verb === 'recover_items')

test('死んだ直後は、目標に関係なく持ち物を取りに戻る候補が出る', () => {
  const now = 100 * MIN
  const memory = { deaths: [{ x: 30, y: 70, z: 2, seen: now - 1 * MIN }], places: {}, explored: {}, chests: {}, furnaces: {} }
  const [c] = offered(bot(now), memory)
  assert.equal(c.id, 'recover your things where you died at 30,70,2')
  assert.equal(c.distance, 29.5)
  assert.equal(c.direction, 'E')
  assert.equal(c.minutes_ago, 1)
  assert.deepEqual([c.pos.x, c.pos.z], [30, 2])
  assert.equal(offered(bot(now + 10 * MIN), memory).length, 0) // 5 分たつと消えている
})

test('死んだ場所へ戻って拾う。何も落ちていなければ失敗にして、その死は諦める記録が残る', async () => {
  const items = []
  const drop = { id: 7, name: 'item', position: new Vec3(31, 70, 2) }
  const b = {
    ...bot(0),
    entity: { position: new Vec3(31, 70, 2), onGround: true },
    entities: { 7: drop },
    on: () => {},
    off: () => {},
    waitForTicks: async () => {},
    inventory: { items: () => items },
    pathfinder: {
      goto: async () => {},
      setGoal: () => {}
    }
  }
  const state = { unreachableDrops: new Set(), memory: { deaths: [{ x: 30, y: 70, z: 2, seen: 0 }] } }
  const c = { pos: new Vec3(30, 70, 2) }
  const abort = new AbortController()
  // 拾えると持ち物が増える
  b.pathfinder.goto = async (goal) => { if (goal.x === 31) { items.push({ name: 'cobblestone', count: 5 }); delete b.entities[7] } }
  const result = await PRIMITIVES.recover_items(b, state, c, abort.signal)
  assert.match(result, /recovered 5 items/)
  assert.equal(state.memory.deaths[0].recovered, true)
  assert.equal(state.memory.deaths[0].tries, 1)
  // 何も落ちていなければ失敗
  state.memory.deaths[0].recovered = false
  await assert.rejects(PRIMITIVES.recover_items(b, state, c, abort.signal), /nothing left to pick up where you died/)
  assert.equal(state.memory.deaths[0].tries, 2)
})

test('recover_items: 行き着けない落とし物は、1 回で諦める（続けて行くと、その分も動けなくなるまで待つ）', async () => {
  const items = []
  const drop = { id: 7, name: 'item', position: new Vec3(31, 70, 2) }
  let tries = 0
  const b = {
    ...bot(0),
    entity: { position: new Vec3(31, 70, 2), onGround: true },
    entities: { 7: drop },
    on: () => {},
    off: () => {},
    waitForTicks: async () => {},
    inventory: { items: () => items },
    pathfinder: {
      goto: async (goal) => { if (goal.x === 31) { tries++; throw new Error('stuck: did not get closer to the goal') } },
      setGoal: () => {}
    }
  }
  const state = { unreachableDrops: new Set(), memory: { deaths: [{ x: 30, y: 70, z: 2, seen: 0 }] } }
  await assert.rejects(PRIMITIVES.recover_items(b, state, { pos: new Vec3(30, 70, 2) }, new AbortController().signal), /nothing left to pick up/)
  assert.equal(tries, 1)
})
