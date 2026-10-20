import { test, afterEach } from 'node:test'
import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import { runSkill, validateSkill, SkillStore, LIMITS, rawBotEnabled, expectsBaseline, judgeExpects } from '../src/skills.mjs'

const EXPECTS = { predicate: 'have', item: 'food', count: '+1' }
const rawSkill = (body) => ({ raw: true, code: `export default async function (t, args) {\n${body}\n}`, expects: EXPECTS })

afterEach(() => { delete process.env.BRIDGE_RAW_BOT; delete process.env.BRIDGE_RAW_ONLY })

// 操作キー・経路・掘りと、tick のイベントを持つ bot の代役
function fakeBot () {
  const bot = new EventEmitter()
  bot.cleared = 0
  bot.goals = []
  bot.stopped = 0
  bot.controlState = { jump: false }
  bot.clearControlStates = function () { assert.equal(this, bot); bot.cleared += 1; bot.controlState.jump = false }
  bot.setControlState = function (k, v) { assert.equal(this, bot); bot.controlState[k] = v }
  bot.pathfinder = { setGoal: (g) => bot.goals.push(g) }
  bot.stopDigging = () => { bot.stopped += 1 }
  bot._client = new EventEmitter()
  bot._client.written = []
  bot._client.write = function (name, data) { assert.equal(this, bot._client); this.written.push([name, data]) }
  bot.entity = { position: { x: 1, y: 64, z: 2 } }
  return bot
}

function deps (bot, { tool = async () => ({ ok: true, result: 'done', seconds: 0 }) } = {}) {
  const called = []
  return {
    bot,
    called,
    callTool: async (name, args) => { called.push({ name, args }); return tool(name, args) },
    state: () => ({ inventory: {} }),
    blockAt: () => ({ name: 'air' }),
    judge: async () => ({ answer: null }),
    abortTool: () => {}
  }
}

test('a raw skill is refused unless the bridge was started with BRIDGE_RAW_BOT=1', () => {
  const def = rawSkill('return t.done()')
  assert.throws(() => validateSkill({ ...def, description: 'x' }), /raw skills are off/)
  process.env.BRIDGE_RAW_BOT = '1'
  const v = validateSkill({ ...def, description: 'x' })
  assert.equal(v.raw, true)
})

test('a skill that is not raw is saved without the flag and has no bot', async () => {
  const v = validateSkill({ description: 'x', code: 'export default async function (t) { return t.done() }', expects: EXPECTS })
  assert.equal('raw' in v, false)
  const r = await runSkill({ code: 'export default async function (t) { return t.done(typeof t.bot) }', expects: EXPECTS }, {}, deps(fakeBot()))
  assert.equal(r.summary, 'undefined')
})

test('a raw skill drives the real bot object and the t tools together', async () => {
  const bot = fakeBot()
  const d = deps(bot)
  const r = await runSkill(rawSkill(`
    const p = t.bot.entity.position
    t.bot.setControlState('jump', true)
    await t.sleep(5)
    const v = new t.Vec3(p.x, p.y + 1, p.z)
    const r = await t.place({ item: 'cobblestone', x: v.x, y: v.y, z: v.z })
    t.bot._client.write('block_place', { x: v.x })
    return r.ok ? t.done('placed at ' + v.y + ' jump=' + t.bot.controlState.jump) : t.fail(r.result)`), {}, d)
  assert.equal(r.ended, 'done')
  assert.equal(r.summary, 'placed at 65 jump=true')
  assert.deepEqual(d.called, [{ name: 'place', args: { item: 'cobblestone', x: 1, y: 65, z: 2 } }])
  assert.equal(JSON.stringify(bot._client.written), JSON.stringify([['block_place', { x: 1 }]]))
})

test('what a raw skill left on the bot is released when it ends', async () => {
  const bot = fakeBot()
  const r = await runSkill(rawSkill(`
    t.bot.on('physicsTick', () => {})
    t.bot._client.on('packet', () => {})
    t.bot.setControlState('jump', true)
    t.sleep(60000)
    return t.done('left things behind')`), {}, deps(bot))
  assert.equal(r.ended, 'done')
  assert.equal(bot.listenerCount('physicsTick'), 0)
  assert.equal(bot._client.listenerCount('packet'), 0)
  assert.equal(bot.controlState.jump, false)
  assert.deepEqual(bot.goals, [null])
  assert.equal(bot.stopped, 1)
})

test('stopping a raw skill ends a sleep at once and lets go of the bot', async () => {
  const bot = fakeBot()
  const control = {}
  const run = runSkill(rawSkill(`
    t.bot.setControlState('jump', true)
    await t.sleep(60000)
    return t.done('too late')`), {}, deps(bot), LIMITS, control)
  await new Promise((resolve) => setTimeout(resolve, 20))
  assert.equal(control.raw, true)
  const t0 = Date.now()
  control.stop('reflex: zombie 3m away')
  const r = await run
  assert.ok(Date.now() - t0 < 1000)
  assert.equal(r.ended, 'stopped')
  assert.match(r.reason, /zombie/)
  assert.equal(bot.controlState.jump, false)
  assert.ok(bot.goals.includes(null))
})

test('after a stop, a body that comes back from a pending dig cannot touch the bot again', async () => {
  const bot = fakeBot()
  let finishDig
  bot.dig = () => new Promise((resolve) => { finishDig = resolve })
  const control = {}
  const run = runSkill(rawSkill(`
    await t.bot.dig({})
    t.bot.setControlState('jump', true)
    t.bot.pathfinder.setGoal('late')
    return t.done('ran on')`), {}, deps(bot), LIMITS, control)
  await new Promise((resolve) => setTimeout(resolve, 20))
  control.stop('reflex: skeleton 5m away')
  const r = await run
  finishDig()
  await new Promise((resolve) => setTimeout(resolve, 20))
  assert.equal(r.ended, 'stopped')
  assert.equal(bot.controlState.jump, false)
  assert.ok(bot.goals.length > 0 && bot.goals.every((g) => g === null)) // 後から setGoal('late') が届いていない
})

test('経路の設定を t.bot で作った技が止められても、pathfinder には技の前の設定が戻り、例外には目印が付く', async () => {
  const bot = fakeBot()
  const before = { name: 'bridge movements' }
  bot.pathfinder = { movements: before, setGoal: () => {}, setMovements (m) { this.movements = m } }
  let mine
  const control = {}
  const run = runSkill(rawSkill(`
    t.bot.pathfinder.setMovements({ name: 'skill movements', bot: t.bot })
    await t.sleep(5000)
    return t.done()`), {}, deps(bot), LIMITS, control)
  await new Promise((resolve) => setTimeout(resolve, 20))
  mine = bot.pathfinder.movements
  assert.equal(mine.name, 'skill movements')
  control.stop('reflex: skeleton 6.7m away (hurt)')
  const r = await run
  assert.equal(r.ended, 'stopped')
  assert.equal(bot.pathfinder.movements, before) // 残ると、次の tick で mine.bot の中身に触れた pathfinder が投げる
  let thrown
  try { mine.bot.entity } catch (e) { thrown = e }
  assert.equal(thrown.skillStopped, true)
})

test('the switch reads on/off words the way the Python setting does', () => {
  for (const [v, on] of [['1', true], ['true', true], ['yes', true], ['', false], ['0', false], ['false', false], ['off', false], [undefined, false]]) {
    if (v === undefined) delete process.env.BRIDGE_RAW_BOT
    else process.env.BRIDGE_RAW_BOT = v
    assert.equal(rawBotEnabled(), on, String(v))
  }
})

test('a thrown error comes back as the reason with where it happened', async () => {
  const r = await runSkill(rawSkill('await t.bot.dig({ position: 1 })'), {}, deps({ ...fakeBot(), dig: async () => { throw new Error('Cannot dig that') } }))
  assert.equal(r.ended, 'error')
  assert.match(r.reason, /dig/)
  const r2 = await runSkill(rawSkill('throw new Error("no luck here")'), {}, deps(fakeBot()))
  assert.equal(r2.ended, 'error')
  assert.match(r2.reason, /no luck here/)
})

test('t tools inside a raw skill keep the action limit', async () => {
  const r = await runSkill(rawSkill('for (;;) await t.wait()'), {}, deps(fakeBot()), { ...LIMITS, actions: 3 })
  assert.equal(r.ended, 'stopped')
  assert.match(r.reason, /too many actions/)
})

test('a raw skill that runs too long is stopped', async () => {
  const bot = fakeBot()
  const r = await runSkill(rawSkill('for (;;) await t.sleep(5)'), {}, deps(bot), { ...LIMITS, rawMs: 80 })
  assert.equal(r.ended, 'stopped')
  assert.match(r.reason, /ran longer/)
})

test('a loop that never awaits is stopped before it freezes the bridge', async () => {
  const r = await runSkill(rawSkill('for (;;) {}'), {}, deps(fakeBot()), { ...LIMITS, cpuMs: 100 })
  assert.equal(r.ended, 'stopped')
})

test('the sandbox of a raw skill still has no require or process', async () => {
  const r = await runSkill(rawSkill('return t.done([typeof require, typeof process, typeof fetch].join(","))'), {}, deps(fakeBot()))
  assert.equal(r.summary, 'undefined,undefined,undefined')
})

test('the list shows which skills are raw', () => {
  process.env.BRIDGE_RAW_BOT = '1'
  const dir = `${process.env.TMPDIR ?? '/tmp'}/raw-skill-${process.pid}`
  const store = new SkillStore(dir)
  store.put('jump_tower', { ...rawSkill('return t.done()'), description: 'tower' })
  store.put('plain_one', { description: 'plain', code: 'export default async function (t) { return t.done() }', expects: EXPECTS })
  const list = Object.fromEntries(store.list().map((s) => [s.name, s.raw]))
  assert.deepEqual(list, { jump_tower: true, plain_one: false })
  assert.equal(store.get('jump_tower').raw, true)
})

// ---- 版の成績と切り戻し（設計書 168 §4）

const plain = (body = 'return t.done()') => ({ description: 'd', code: `export default async function (t) { ${body} }`, expects: EXPECTS })
const tmpStore = () => new SkillStore(`${process.env.TMPDIR ?? '/tmp'}/skill-versions-${process.pid}-${Math.random().toString(36).slice(2)}`)

test('a new version that has not run yet is the one to run (its trial)', () => {
  const store = tmpStore()
  store.put('sk_one', plain())
  store.record('sk_one', 1, true)
  store.put('sk_one', plain('return t.done("v2")'))
  assert.equal(store.defaultVersion('sk_one'), null)
})

test('after the trial of a new version failed, the version that worked is run', () => {
  const store = tmpStore()
  store.put('sk_one', plain())
  store.record('sk_one', 1, true)
  store.put('sk_one', plain('return t.fail("x")'))
  store.record('sk_one', 2, false, 'no good')
  assert.deepEqual(store.defaultVersion('sk_one'), { version: 1, from: 2 })
  const item = store.list()[0]
  assert.equal(item.verified, true)
  assert.equal(item.runs_version, 1)
  assert.equal(item.version, 2)
})

test('the version that worked best is the one to go back to', () => {
  const store = tmpStore()
  store.put('sk_one', plain())
  store.record('sk_one', 1, true)
  store.record('sk_one', 1, false, 'a')
  store.put('sk_one', plain('return t.done("v2")'))
  store.record('sk_one', 2, true)
  store.record('sk_one', 2, true)
  store.put('sk_one', plain('return t.fail("x")'))
  store.record('sk_one', 3, false, 'bad')
  assert.deepEqual(store.defaultVersion('sk_one'), { version: 2, from: 3 })
})

test('with no version that worked there is nothing to go back to', () => {
  const store = tmpStore()
  store.put('sk_one', plain())
  store.record('sk_one', 1, false, 'a')
  store.put('sk_one', plain('return t.fail("x")'))
  store.record('sk_one', 2, false, 'b')
  assert.equal(store.defaultVersion('sk_one'), null)
  assert.equal(store.list()[0].verified, false)
})

test('the list keeps each version\'s runs and average seconds', () => {
  const store = tmpStore()
  store.put('sk_one', plain())
  store.record('sk_one', 1, true, null, 4)
  store.record('sk_one', 1, false, 'x', 2)
  const [item] = store.list()
  assert.deepEqual(item.versions, [{ version: 1, uses: 2, successes: 1, failures: 1, avg_seconds: 3 }])
})

// 判断なしの実験（設計書 191）
test('BRIDGE_RAW_ONLY: 技は生の bot だけで書く。道具・判定・助言入りの state は技から消え、expects は 4 種に限る', async () => {
  process.env.BRIDGE_RAW_BOT = '1'
  process.env.BRIDGE_RAW_ONLY = '1'
  try {
    const rawHave = { predicate: 'have', item: 'oak_log', count: '+1' }
    assert.throws(() => validateSkill({ description: 'x', code: 'export default async function (t) { return t.done() }', expects: rawHave }), /only raw skills/)
    assert.throws(() => validateSkill({ ...rawSkill('return t.done()'), description: 'x', expects: { predicate: 'progress' } }), /one of have, at, height, out_of_closed_space, block_near/)
    assert.equal(validateSkill({ ...rawSkill('return t.done()'), description: 'x', expects: rawHave }).raw, true)
    const bot = fakeBot()
    const d = deps(bot)
    d.state = () => { throw new Error('the advice state must not be used') }
    d.rawState = () => ({ self: { health: 20 } })
    const r = await runSkill(rawSkill(`
      const s = await t.state()
      return t.done([typeof t.goto, typeof t.dig, typeof t.judge, typeof t.how_to_get, typeof t.find_blocks, typeof t.inspect, typeof t.cross_section, typeof t.recipe_of, typeof t.bot, s.self.health, 'needs' in s].join(','))`), {}, d)
    assert.equal(r.summary, 'undefined,undefined,undefined,undefined,undefined,undefined,function,function,object,20,false')
  } finally {
    delete process.env.BRIDGE_RAW_ONLY
  }
})

// 設計書 193: 置いた物は持ち物から消える。置けたかは世界のブロックで見る
test('expects block_near: 近くにそのブロックがあれば成功。名前と半径を事前に確かめ、持ち物の個数 0 は断る', () => {
  process.env.BRIDGE_RAW_BOT = '1'
  process.env.BRIDGE_RAW_ONLY = '1'
  try {
    const near = { predicate: 'block_near', block: 'crafting_table', distance: 4 }
    assert.equal(validateSkill({ ...rawSkill('return t.done()'), description: 'x', expects: near }).raw, true)
    assert.throws(() => validateSkill({ ...rawSkill('return t.done()'), description: 'x', expects: { predicate: 'block_near' } }), /needs block/)
    assert.throws(() => validateSkill({ ...rawSkill('return t.done()'), description: 'x', expects: { ...near, distance: 99 } }), /distance must be 1 to 32/)
    assert.throws(() => validateSkill({ ...rawSkill('return t.done()'), description: 'x', expects: { predicate: 'have', item: 'crafting_table', count: 0 } }), /count must be 1 or more.*block_near/)
    // 判定: 近くにある → 成功、ない → 失敗（半径と名前が記録に出る）
    const deps = (found) => ({ knownBlock: (n) => n === 'crafting_table', blockNear: (n, d) => (found ? { x: 1, y: 70, z: 2, distance: 2.5 } : null) })
    assert.deepEqual(expectsBaseline(near, {}, deps(true)), {})
    assert.throws(() => expectsBaseline({ predicate: 'block_near', block: 'craft_table' }, {}, deps(true)), /not a block name/)
    const yes = judgeExpects(near, {}, {}, deps(true))
    assert.equal(yes.met, true)
    assert.match(yes.lines[0], /crafting_table is 2.5 blocks away at 1,70,2/)
    const no = judgeExpects({ predicate: 'block_near', block: '$what' }, { what: 'furnace' }, {}, deps(false))
    assert.equal(no.met, false)
    assert.match(no.lines[0], /no furnace within 5 blocks/)
  } finally {
    delete process.env.BRIDGE_RAW_ONLY
  }
})
