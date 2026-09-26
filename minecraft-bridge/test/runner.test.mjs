import { test } from 'node:test'
import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import vec3Pkg from 'vec3'
import { createRunner, abortCurrent } from '../src/runner.mjs'

const { Vec3 } = vec3Pkg

// 家のない昼のボット（runner が使うものだけ）
function fakeBot () {
  const bot = new EventEmitter()
  bot.entity = { position: new Vec3(0, 70, 0) }
  bot.entities = {}
  bot.health = 20
  bot.time = { timeOfDay: 1000 }
  bot.inventory = { items: () => [] }
  bot.pathfinder = { setGoal: () => {}, isMoving: () => false }
  bot.clearControlStates = () => {}
  bot.blockAt = () => null
  return bot
}

// signal が中断されるまで待つプリミティブ
const waitsForAbort = (bot, state, c, signal) => new Promise((resolve, reject) => {
  signal.addEventListener('abort', () => reject(signal.reason))
})

function setup (primitives) {
  const bot = fakeBot()
  const state = { history: [], busy: false, reflex: false, current: null, home: null, formerHomes: [] }
  let after = 0
  const run = createRunner(bot, state, { primitives, afterRun: () => { after++ }, log: () => {} })
  return { bot, state, run, afterRuns: () => after }
}

test('成功した行動は履歴に残り、実行の後の処理が走る', async () => {
  const { state, run, afterRuns } = setup({ wait: async () => 'waited' })
  const r = await run({ verb: 'wait' }, 'wait a bit')
  assert.equal(r.ok, true)
  assert.equal(r.result, 'waited')
  assert.deepEqual(state.history.map((h) => [h.action, h.ok]), [['wait a bit', true]])
  assert.equal(afterRuns(), 1)
  assert.equal(state.busy, false)
  assert.equal(state.current, null)
})

test('実行中は state.current に進み具合があり、POST /abort の理由で止まる', async () => {
  const { state, run } = setup({ dig: waitsForAbort })
  const running = run({ verb: 'dig', pos: new Vec3(3, 70, 4), target: 'oak_log' }, 'dig oak_log')
  assert.equal(state.busy, true)
  const snap = state.current.progress.snapshot()
  assert.equal(snap.verb, 'dig')
  assert.equal(snap.target_distance_m.at_start, 5)
  assert.equal(snap.position_resets, 0)
  const aborted = await abortCurrent(state, 'woke up: 敵が近い')
  assert.deepEqual(aborted, { aborted: true, action: 'dig oak_log' })
  const r = await running
  assert.equal(r.ok, false)
  assert.equal(r.result, 'failed: woke up: 敵が近い')
})

test('終わった行動への中断は何もしない（見張りの中断が行動の終わりと競合しても安全）', async () => {
  const { state, run } = setup({ wait: async () => 'waited' })
  await run({ verb: 'wait' }, 'wait')
  assert.deepEqual(await abortCurrent(state, 'late'), { aborted: false, why: 'no action is running' })
})

test('サーバーに位置を戻された回数を数える（当たり判定の不具合の目印）', async () => {
  const { bot, state, run } = setup({ goto: waitsForAbort })
  const running = run({ verb: 'goto', pos: new Vec3(10, 70, 0) }, 'goto')
  bot.emit('forcedMove')
  bot.emit('forcedMove')
  bot.emit('path_update', { status: 'noPath' })
  const snap = state.current.progress.snapshot()
  assert.equal(snap.position_resets, 2)
  assert.equal(snap.path.no_path, 1)
  await abortCurrent(state, 'stop')
  await running
})

test('反射が動いている間と、別の行動の実行中は始めない', async () => {
  const { state, run } = setup({ dig: waitsForAbort, wait: async () => 'waited' })
  state.reflex = true
  assert.match((await run({ verb: 'wait' }, 'wait')).result, /reflex/)
  state.reflex = false
  const running = run({ verb: 'dig', pos: new Vec3(1, 70, 0) }, 'dig')
  assert.match((await run({ verb: 'wait' }, 'wait')).result, /another action is running/)
  await abortCurrent(state, 'stop')
  await running
})

test('遠くへ歩いている途中の時間切れは、進んでいれば失敗にしない（進んだ距離を返す）', async () => {
  const { TIMEOUTS_MS } = await import('../src/primitives.mjs')
  TIMEOUTS_MS.test_walk = 50
  TIMEOUTS_MS.test_stuck = 50
  const walking = (moveBy) => (bot, state, c, signal) => {
    bot.actionPhase = 'walking toward home (280m away)'
    bot.entity.position = bot.entity.position.offset(moveBy, 0, 0)
    return waitsForAbort(bot, state, c, signal)
  }
  const { run } = setup({ test_walk: walking(20), test_stuck: walking(1) })
  const moved = await run({ verb: 'test_walk' }, 'go home')
  assert.equal(moved.ok, true)
  assert.match(moved.result, /^walked toward home \(280m away\): 20m this time/)
  const stuck = await run({ verb: 'test_stuck' }, 'go home')
  assert.equal(stuck.ok, false)
  assert.match(stuck.result, /timeout \(while walking toward home \(280m away\); moved 1m\)/)
  delete TIMEOUTS_MS.test_walk
  delete TIMEOUTS_MS.test_stuck
})
