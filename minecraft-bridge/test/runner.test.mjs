import { test } from 'node:test'
import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { createRunner, abortCurrent } from '../src/runner.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')

// 家のない昼のボット（runner が使うものだけ）
function fakeBot () {
  const bot = new EventEmitter()
  bot.entity = { position: new Vec3(0, 70, 0) }
  bot.entities = {}
  bot.health = 20
  bot.food = 20
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

// 危なさが高い（risk.mjs）: 値打ちのある持ち物（ダイヤのツルハシ）を持って体力が 8。夕方 1 を足して 5 になる
const endangered = (bot) => {
  bot.health = 8
  bot.inventory = { items: () => [{ name: 'diamond_pickaxe', count: 1 }, { name: 'torch', count: 4 }] }
}

test('夕方になっても、危なさが低ければ（元気で、失うものもない）止めない', async () => {
  const walking = (bot, state, c, signal) => waitsForAbort(bot, state, c, signal)
  const { bot, state, run } = setup({ explore: walking })
  const running = run({ verb: 'explore' }, 'explore east')
  setTimeout(() => { bot.time.timeOfDay = 12500 }, 100)
  await new Promise((resolve) => setTimeout(resolve, 900))
  assert.ok(state.current, 'still walking at dusk')
  await abortCurrent(state, 'stop')
  assert.match((await running).result, /^failed: stop$/)
})

test('時間では止めない: 危なさが高い夕方になった・空腹になったときだけ止め、失敗には数えない', async () => {
  const walking = (bot, state, c, signal) => {
    bot.actionPhase = 'walking toward home (280m away)'
    bot.entity.position = bot.entity.position.offset(20, 0, 0)
    return waitsForAbort(bot, state, c, signal)
  }
  const { bot, state, run } = setup({ explore: walking, sleep: walking })
  endangered(bot)
  setTimeout(() => { bot.time.timeOfDay = 12500 }, 700)
  const dusk = await run({ verb: 'explore' }, 'explore east')
  assert.equal(dusk.ok, false)
  assert.equal(dusk.interrupted, true)
  assert.match(dusk.result, /^failed: stopped: it is dusk now and going on is too dangerous \(while walking toward home \(280m away\); moved 20m\)/)
  // 夜を過ごす行動は止めない（始めたときより暗くなっても）。止めるのは POST /abort
  bot.time.timeOfDay = 1000
  const sleeping = run({ verb: 'sleep' }, 'sleep')
  setTimeout(() => { bot.time.timeOfDay = 18000 }, 100)
  await new Promise((resolve) => setTimeout(resolve, 800))
  assert.ok(state.current, 'still sleeping at night')
  await abortCurrent(state, 'woke up')
  assert.match((await sleeping).result, /^failed: woke up$/)
  // 空腹は、始めたときになかったときだけ（空腹のまま食料を探す行動は止めない）
  const { run: run2, state: state2, bot: bot2 } = setup({ explore: walking })
  bot2.food = 5
  const hungry = run2({ verb: 'explore' }, 'explore')
  await new Promise((resolve) => setTimeout(resolve, 700))
  assert.ok(state2.current, 'still walking while hungry from the start')
  await abortCurrent(state2, 'stop')
  assert.match((await hungry).result, /^failed: stop$/)
  const { run: run3, bot: bot3 } = setup({ explore: walking })
  setTimeout(() => { bot3.food = 6 }, 100)
  assert.match((await run3({ verb: 'explore' }, 'explore')).result, /^failed: stopped: hungry \(food 6\/20\)/)
  // 帰る・戦う・逃げる・食べる・夜を過ごす行動は、空腹になっても止めない（食べ物がなければ、帰る歩きを途中で
  // 止めるだけ。2026-09-30 18:07: 家まで 64 m の帰り道を 14 m で止め、夜の外で死んだ）
  const { run: run4, state: state4, bot: bot4 } = setup({ go_home: walking })
  const going = run4({ verb: 'go_home' }, 'go_home')
  setTimeout(() => { bot4.food = 6 }, 100)
  await new Promise((resolve) => setTimeout(resolve, 900))
  assert.ok(state4.current, 'still walking home while hungry')
  await abortCurrent(state4, 'stop')
  assert.match((await going).result, /^failed: stop$/)
})

test('食べ物を持っているなら、夜の帰り道も空腹になったら止めて食べさせる（満腹度 0 で肉を持ったまま 11 分歩いた）', async () => {
  const walking = (bot, state, c, signal) => {
    bot.actionPhase = 'walking toward home'
    return waitsForAbort(bot, state, c, signal)
  }
  const carrying = (name) => {
    const s = setup({ go_home: walking })
    s.bot.registry = { foodsByName: { beef: {}, rotten_flesh: {} } }
    s.bot.inventory = { items: () => [{ name, count: 2 }] }
    setTimeout(() => { s.bot.food = 6 }, 100)
    return s
  }
  // 牛肉があれば止めて食べる
  const a = carrying('beef')
  assert.match((await a.run({ verb: 'go_home' }, 'go_home')).result, /^failed: stopped: hungry \(food 6\/20\)/)
  // 腐った肉は食べ物に数えない: 止めない
  const b = carrying('rotten_flesh')
  const going = b.run({ verb: 'go_home' }, 'go_home')
  await new Promise((resolve) => setTimeout(resolve, 900))
  assert.ok(b.state.current, 'still walking home')
  await abortCurrent(b.state, 'stop')
  assert.match((await going).result, /^failed: stop$/)
})

test('暗くて松明がないと止める（引き返して松明を作らせる）。松明を持っていれば、暗い所で始めたのなら、松明や作る手なら止めない', async () => {
  const walking = (bot, state, c, signal) => {
    bot.actionPhase = 'walking toward the ore'
    return waitsForAbort(bot, state, c, signal)
  }
  const roofed = (bot) => { bot.blockAt = () => ({ boundingBox: 'block', name: 'stone' }); bot.registry = { blocksByName: { stone: { filterLight: 15 } } }; bot.findBlock = () => null }
  // 屋根が付いた（坑道に入った）: 松明がなければ止める
  const a = setup({ dig: walking })
  a.bot.registry = { blocksByName: {} }
  a.bot.findBlock = () => null
  setTimeout(() => roofed(a.bot), 100)
  const stopped = await a.run({ verb: 'dig' }, 'dig stone')
  assert.equal(stopped.interrupted, true)
  assert.match(stopped.result, /^failed: stopped: too dark to see and no torch/)
  // 松明を持っていれば止めない（置く手が出る）
  const b = setup({ dig: walking })
  b.bot.inventory = { items: () => [{ name: 'torch', count: 3 }] }
  b.bot.registry = { blocksByName: {} }
  b.bot.findBlock = () => null
  setTimeout(() => roofed(b.bot), 100)
  const carrying = b.run({ verb: 'dig' }, 'dig stone')
  await new Promise((resolve) => setTimeout(resolve, 900))
  assert.ok(b.state.current, 'keeps digging with torches in the pack')
  await abortCurrent(b.state, 'stop')
  await carrying
  // 松明を作る手・置く手は、暗くても止めない
  const c = setup({ craft: walking })
  roofed(c.bot)
  const crafting = c.run({ verb: 'craft' }, 'craft torch')
  await new Promise((resolve) => setTimeout(resolve, 900))
  assert.ok(c.state.current, 'crafting torches is not stopped in the dark')
  await abortCurrent(c.state, 'stop')
  await crafting
})

test('深い地下では、夕方・夜になっても止めない。浅い所（空が近い）では止める', async () => {
  const walking = (bot, state, c, signal) => {
    bot.actionPhase = 'digging the mine'
    return waitsForAbort(bot, state, c, signal)
  }
  // 頭の上 2〜6 マスが石（どの柱も同じ: 空が開いた柱はない）
  const deep = (open = () => false) => {
    const { bot, state, run } = setup({ dig_mine: walking })
    bot.registry = md
    bot.findBlock = () => null
    endangered(bot) // 地下でなければ止まるだけの危なさ。暗いのは地下では当たり前: 松明があれば止めない
    bot.entity.position = new Vec3(0, 30, 0)
    bot.blockAt = (p) => (p.y > 31 && p.y < 37 && !open(p) ? { name: 'stone', boundingBox: 'block' } : { name: 'air', boundingBox: 'empty' })
    return { bot, state, run }
  }
  const { bot, state, run } = deep()
  const mining = run({ verb: 'dig_mine' }, 'dig the mine')
  setTimeout(() => { bot.time.timeOfDay = 12500 }, 100)
  setTimeout(() => { bot.time.timeOfDay = 14000 }, 300)
  await new Promise((resolve) => setTimeout(resolve, 900))
  assert.ok(state.current, 'still digging at night underground')
  await abortCurrent(state, 'stop')
  assert.match((await mining).result, /^failed: stop$/)
  // 空が開いた柱が近くにある（洞窟の入口など）: いつもどおり止める
  const shallow = deep((p) => p.x >= 3)
  const cave = shallow.run({ verb: 'dig_mine' }, 'dig the mine')
  setTimeout(() => { shallow.bot.time.timeOfDay = 12500 }, 100)
  assert.match((await cave).result, /^failed: stopped: it is dusk now and going on is too dangerous/)
})
