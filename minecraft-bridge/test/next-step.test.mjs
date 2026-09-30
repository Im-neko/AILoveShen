import { test } from 'node:test'
import assert from 'node:assert/strict'
import minecraftData from 'minecraft-data'
import { Knowledge } from '../src/knowledge.mjs'
import { solve, nextStep, isGather } from '../src/solver.mjs'
import { checkConditions } from '../src/goals.mjs'

const k = new Knowledge(minecraftData('1.21.4'))
const world = (inventory, blocks = {}, extra = {}) => ({
  inventory, blocks, mobs: {}, table: null, unlocked: () => true, dig: () => [], hunt: () => [], remembered: new Set(), furnace: null, ...extra
})
const step = (spec, count, w) => {
  const r = solve(k, w, [{ spec, count }])
  return nextStep(r.nodes[0], w, k)
}

test('仕事の次の一歩は、まだ足りず集めに出る最初の材料（板材 16 枚なら原木 4 個）', () => {
  assert.deepEqual(step('oak_planks', 16, world({}, { oak_log: 6 })), { item: 'oak_log', count: 4 })
})

test('道具が要る仕事は、まず道具（クラフトは自動なので、集めに出るものを含むものだけ）', () => {
  // 石のツルハシ: 丸石 3 と棒 2。棒は板材から作れるので、材料を持っていれば丸石を集めに行く一歩
  const w = world({ oak_planks: 8, stick: 2 }, { cobblestone: 0, stone: 5 }, { table: { x: 0, y: 64, z: 0 } })
  const s = step('stone_pickaxe', 1, w)
  assert.equal(s.item, 'cobblestone')
  assert.equal(s.count, 3)
})

test('集める物そのものが仕事なら次の一歩はない（仕事が 1 回の遠征）', () => {
  assert.equal(step('oak_log', 8, world({}, { oak_log: 6 })), null)
})

test('持ち物がもう足りている材料は一歩にしない（同じ一歩を繰り返さない）', () => {
  // 板材 8 枚 = 原木 2 個。原木を持っていれば集めに出る一歩は残らない
  assert.equal(step('oak_planks', 8, world({ oak_log: 2 })), null)
})

test('/check は have の次の一歩を返す。済んでいる条件と、集めに出ないものは null', () => {
  const bot = { time: { timeOfDay: 1000 }, entity: { position: { floored: () => ({ x: 0, y: 70, z: 0 }) } } }
  const state = { home: null, plan: null, goal: { spec: { predicate: 'at_home' } } }
  const [planks, done] = checkConditions([{ predicate: 'have', item: 'oak_planks', count: 16 }, { predicate: 'have', item: 'stick', count: 1 }],
    bot, state, k, world({ stick: 3 }, { oak_log: 6 }))
  assert.deepEqual(planks.next_step, { predicate: 'have', item: 'oak_log', count: 4 })
  assert.equal(done.met, true)
  assert.equal(done.next_step, null)
})

test('素材の採集そのものの仕事は gather（原木・丸石）。作るものや、持っていて済んだものは違う', () => {
  const gather = (spec, count, w) => isGather(solve(k, w, [{ spec, count }]).nodes[0])
  assert.equal(gather('oak_log', 8, world({}, { oak_log: 6 })), true)
  assert.equal(gather('oak_planks', 16, world({}, { oak_log: 6 })), false)
  assert.equal(gather('oak_log', 2, world({ oak_log: 5 }, { oak_log: 6 })), false) // もう足りている
})

test('/check は素材の採集の仕事を gather と知らせる', () => {
  const bot = { time: { timeOfDay: 1000 }, entity: { position: { floored: () => ({ x: 0, y: 70, z: 0 }) } } }
  const state = { home: null, plan: null, goal: { spec: { predicate: 'at_home' } } }
  const [log, planks] = checkConditions([{ predicate: 'have', item: 'oak_log', count: 4 }, { predicate: 'have', item: 'oak_planks', count: 8 }],
    bot, state, k, world({}, { oak_log: 6 }))
  assert.equal(log.gather, true)
  assert.equal(planks.gather, false)
})
