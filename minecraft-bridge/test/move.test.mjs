import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { walkTo } from '../src/move.mjs'
import { PRIMITIVES } from '../src/primitives.mjs'

const { Vec3 } = vec3Pkg

test('中断の後には、新しい移動を始めない', async () => {
  const goals = []
  const bot = { pathfinder: { goto: async (g) => goals.push(g), setGoal: () => {} } }
  const controller = new AbortController()
  await walkTo(bot, 'a', controller.signal)
  controller.abort(new Error('timeout'))
  await assert.rejects(walkTo(bot, 'b', controller.signal), /timeout/)
  assert.deepEqual(goals, ['a'])
})

test('掘り終えて拾う前に中断されたら、落とし物を拾いに行かない（town4: 拾いに行く移動が終わらなかった）', async () => {
  const controller = new AbortController()
  const goals = []
  const bot = {
    entity: { position: new Vec3(0, 70, 0), onGround: true },
    entities: { 7: { id: 7, name: 'item', position: new Vec3(1.5, 70, 0.5) } },
    blockAt: (p) => ({ name: 'spruce_log', boundingBox: 'block', position: p }),
    dig: async () => {},
    equip: async () => {},
    waitForTicks: async () => controller.abort(new Error('timeout')), // 落ちた物を待つ間に時間切れ
    on: () => {},
    off: () => {},
    inventory: { items: () => [], emptySlotCount: () => 30 },
    pathfinder: { bestHarvestTool: () => null, goto: async (g) => goals.push(g), setGoal: () => {} }
  }
  const state = { unreachableDrops: new Set(), unreachableBlocks: new Set() }
  const c = { pos: new Vec3(1, 70, 0), block: 'spruce_log' }

  await assert.rejects(PRIMITIVES.dig(bot, state, c, controller.signal), /timeout/)
  assert.equal(goals.length, 1) // 掘る場所へ行く移動だけ
})
