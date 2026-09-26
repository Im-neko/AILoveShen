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

// 位置が変わらないまま歩き続ける pathfinder（setGoal(null) で goto が失敗する）
function stuckBot (movesAfter = Infinity) {
  let calls = 0
  const log = []
  const bot = {
    entity: { position: new Vec3(0, 64, 0) },
    look: async () => {},
    setControlState: (k, v) => log.push(`${k}=${v}`),
    clearControlStates: () => log.push('clear'),
    waitForTicks: async () => {},
    pathfinder: {
      movements: { allowParkour: true, allowSprinting: true },
      setMovements (m) { this.movements = m; log.push(`movements parkour=${m.allowParkour}`) },
      setGoal (g) { if (g === null && this.reject) { const r = this.reject; this.reject = null; r(new Error('GoalChanged')) } },
      goto (g) {
        calls++
        if (calls > movesAfter) return Promise.resolve()
        return new Promise((resolve, reject) => { this.reject = reject })
      }
    }
  }
  return { bot, log, calls: () => calls }
}

test('歩いているのに動けないときは、跳ぶ・慎重に歩く・下がるを試してから失敗にする', async () => {
  const { bot, log } = stuckBot()
  const original = bot.pathfinder.movements
  await assert.rejects(walkTo(bot, 'far', new AbortController().signal, 30), /stuck: .*jumped out.*walked carefully.*backed off/)
  assert.ok(log.includes('jump=true'))
  assert.ok(log.includes('movements parkour=false'))
  assert.equal(bot.pathfinder.movements, original) // 元の歩き方に戻す
})

test('跳んで抜け出せたら、そのまま目的地へ歩く', async () => {
  const { bot, calls } = stuckBot(1)
  await walkTo(bot, 'far', new AbortController().signal, 30)
  assert.equal(calls(), 2)
})

test('同じブロックを 1 分に 3 回掘ったら、しばらくそこは掘らず置かない（掘っては置く繰り返し）', async () => {
  const { EventEmitter } = await import('node:events')
  const { guardDigLoops, isLoopCell } = await import('../src/move.mjs')
  const bot = new EventEmitter()
  let replans = 0
  bot.pathfinder = { movements: {}, setMovements () { replans++ } }
  const state = {}
  guardDigLoops(bot, state)
  const block = { name: 'dirt', position: new Vec3(-78, 39, -116) }
  bot.emit('diggingCompleted', block)
  bot.emit('diggingCompleted', block)
  assert.equal(isLoopCell(state, block.position), false)
  bot.emit('diggingCompleted', block)
  assert.equal(isLoopCell(state, block.position), true)
  assert.equal(isLoopCell(state, new Vec3(0, 39, 0)), false)
  assert.equal(replans, 1)
})
