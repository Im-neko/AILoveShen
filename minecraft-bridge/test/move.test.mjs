import os from 'node:os'
import path from 'node:path'
process.env.STUCK_WALKS_FILE = path.join(os.tmpdir(), `stuck-walks-${process.pid}.jsonl`)
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

test('動いているのに目標へ近づかない（水に流されて泳ぎ続ける）なら、動けないとみなして失敗にする', async () => {
  const { bot } = stuckBot()
  let t
  bot.pathfinder.goto = function () {
    // 目標（x=50）から遠いまま、位置は動き続ける（近づく向きには動かない）
    t = setInterval(() => { bot.entity.position = new Vec3(-Math.random() * 3, 64, 0) }, 50)
    return new Promise((resolve, reject) => { this.reject = reject })
  }
  const setGoal = bot.pathfinder.setGoal
  bot.pathfinder.setGoal = function (g) { if (g === null) clearInterval(t); setGoal.call(this, g) }
  const goal = { x: 50, y: 64, z: 0 }
  await assert.rejects(walkTo(bot, goal, new AbortController().signal, 2000, 600), /stuck: did not get closer to the goal in 0\.6s/)
})

test('掘る・置くを続けるだけで近づかないなら、試さずに失敗にする（go_home が 15 分同じ穴から動かなかった）', async () => {
  const { bot, log } = stuckBot()
  bot.targetDigBlock = { name: 'dirt' }
  let t
  bot.pathfinder.goto = function () {
    // 掘っては置きながら、同じ穴で上下に跳ねる。位置は目標（x=50）に近づかない
    let up = false
    t = setInterval(() => { up = !up; bot.entity.position = new Vec3(0, up ? 65.2 : 64, 0) }, 40)
    return new Promise((resolve, reject) => { this.reject = reject })
  }
  const setGoal = bot.pathfinder.setGoal
  bot.pathfinder.setGoal = function (g) { if (g === null) clearInterval(t); setGoal.call(this, g) }
  await assert.rejects(
    walkTo(bot, { x: 50, y: 64, z: 0 }, new AbortController().signal, 2000, 100),
    /stuck: did not get closer to the goal in 0\.5s while digging and placing at .*\(tried: nothing\)/
  )
  assert.ok(!log.includes('jump=true'))
})

test('同じ高さの間を跳ねて上下しているだけなら、動いていない', async () => {
  const { bot } = stuckBot()
  let t
  bot.pathfinder.goto = function () {
    let up = false
    t = setInterval(() => { up = !up; bot.entity.position = new Vec3(0, up ? 65.2 : 64, 0) }, 40)
    return new Promise((resolve, reject) => { this.reject = reject })
  }
  const setGoal = bot.pathfinder.setGoal
  bot.pathfinder.setGoal = function (g) { if (g === null) clearInterval(t); setGoal.call(this, g) }
  await assert.rejects(walkTo(bot, 'far', new AbortController().signal, 300), /stuck: did not move for 0\.3s while walking/)
})

test('足場を積んで真上へ登っている間は、動いている', async () => {
  const { bot } = stuckBot()
  let t
  let y = 64
  bot.pathfinder.goto = function () {
    t = setInterval(() => { y += 0.5; bot.entity.position = new Vec3(0, y, 0) }, 40)
    return new Promise((resolve) => setTimeout(resolve, 900)) // 0.9 秒登って着く
  }
  await walkTo(bot, 'up', new AbortController().signal, 300)
  clearInterval(t)
  assert.ok(y > 70)
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


test('水の中で動けないときは、跳ぶ・慎重に歩くより先に、目標へ泳いで岸に上がる（上がれなければ、いつもの手へ）', async () => {
  const { bot } = stuckBot()
  bot.entity.isInWater = true
  bot.entity.position = new Vec3(0.5, 62.2, 0.5)
  bot.blockAt = () => ({ name: 'water', boundingBox: 'empty' })
  // 目標が足元にある（泳ぎはすぐ終わる）
  await assert.rejects(
    walkTo(bot, { x: 0.5, y: 62, z: 0.5 }, new AbortController().signal, 30),
    /stuck: .*\(tried: swam 0m \(arrived\), jumped out, swam 0m .*walked carefully.*swam 0m .*backed off/
  )
})

// 経路が空のとき、pathfinder の goto は（状態が noPath / timeout でも）成功として返す。着いていなければ失敗にする
function emptyPathBot (status, at = new Vec3(0, 64, 0)) {
  const listeners = []
  return {
    entity: { position: at },
    on: (name, fn) => { if (name === 'path_update') listeners.push(fn) },
    off: () => {},
    pathfinder: {
      setGoal: () => {},
      goto: async () => { for (const fn of listeners) fn({ status, path: [] }) }
    }
  }
}
const farGoal = { isEnd: (p) => p.x === 100 }

test('経路が空の noPath を成功と受け取らない（make_mine が 39 分動かなかった）', async () => {
  await assert.rejects(walkTo(emptyPathBot('noPath'), farGoal, new AbortController().signal), /No path to the goal/)
  await assert.rejects(walkTo(emptyPathBot('timeout'), farGoal, new AbortController().signal), /Took to long to decide path/)
})

test('経路が空でも、もう着いている・状態が success なら成功のまま', async () => {
  await walkTo(emptyPathBot('noPath', new Vec3(100, 64, 0)), farGoal, new AbortController().signal)
  await walkTo(emptyPathBot('success'), farGoal, new AbortController().signal)
})

test('legToward のように着くまで繰り返す呼び出しは、noPath で止まる（毎 tick やり直さない）', async () => {
  let calls = 0
  const bot = emptyPathBot('noPath')
  const goto = bot.pathfinder.goto
  bot.pathfinder.goto = async (g) => { calls++; if (calls > 50) throw new Error('loop'); return goto(g) }
  await assert.rejects(walkTo(bot, farGoal, new AbortController().signal), /No path/)
  assert.equal(calls, 1)
})
