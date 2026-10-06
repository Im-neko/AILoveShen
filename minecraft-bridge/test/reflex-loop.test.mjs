import { test, mock } from 'node:test'
import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import vec3Pkg from 'vec3'
import { startReflex, releaseReflex, LOOP_FIGHT, LOOP_GIVE_UP } from '../src/reflex.mjs'

const { Vec3 } = vec3Pkg
const v = (x, y, z) => new Vec3(x, y, z)

// 6 秒の STUCK_MS を、偽の時計を進めて待たずに通す
function fakeBot (items = []) {
  const bot = new EventEmitter()
  const skeleton = { id: 7, name: 'skeleton', type: 'hostile', position: v(0.6, 70, 0), height: 1.99, health: 20 }
  Object.assign(bot, {
    entity: { position: v(0, 70, 0), eyeHeight: 1.62, isInWater: false },
    entities: { 7: skeleton },
    time: { timeOfDay: 18000 },
    health: 11,
    world: { raycast: () => null },
    blockAt: () => ({ name: 'air', boundingBox: 'empty' }),
    heldItem: null,
    inventory: { items: () => items.map(([name, count]) => ({ name, count })) },
    pathfinder: { setGoal () {}, isMoving: () => false, goal: null },
    setControlState () {},
    clearControlStates () {},
    equip: async () => {},
    lookAt: async () => {},
    attack: () => {},
    // 1 回待つたびに 1 秒進む（動かないのでいつまでも離れられない）
    waitForTicks: async () => { mock.timers.tick(1000); await new Promise((r) => setImmediate(r)) }
  })
  return { bot, skeleton }
}

async function settle (state) {
  for (let i = 0; i < 50 && state.reflex; i++) await new Promise((r) => setImmediate(r))
}

test('逃げても離れられない相手から逃げ続けない: 1 回で戦う（家が近ければ帰る）、4 回で 1 分反応をやめる', async () => {
  mock.timers.enable({ apis: ['Date'], now: 1_000_000 })
  try {
    const { bot } = fakeBot()
    const state = { home: null }
    const records = []
    startReflex(bot, state, { preempt: async () => true, record: (e) => records.push(e) })
    const rounds = []
    const fire = async () => {
      for (let i = 0; i < 4; i++) bot.emit('physicsTick')
      while (state.reflex || state.danger) await new Promise((r) => setImmediate(r))
      rounds.push(records.at(-1))
    }
    for (let i = 0; i < LOOP_GIVE_UP; i++) await fire()
    // 1 回目は逃げる。逃げられなかった（成功にしない）
    assert.equal(rounds[0].action, 'reflex_flee')
    assert.ok(rounds[0].ok === false && /could not get away/.test(rounds[0].result))
    // その次からは逃げずに戦う（家が遠いので帰れない）
    assert.equal(rounds[1].action, 'reflex_fight')
    assert.equal(rounds[LOOP_FIGHT].action, 'reflex_fight')
    // 数えた最後は、あきらめたと書く
    assert.match(rounds.at(-1).result, /gave up on skeleton/)
    // そのあとの 1 分は、近くにいても反応しない
    const before = records.length
    for (let i = 0; i < 8; i++) bot.emit('physicsTick')
    await settle(state)
    assert.equal(records.length, before)
    // 1 分たてば、また反応する
    mock.timers.tick(61000)
    await fire()
    assert.equal(records.length, before + 1)
  } finally {
    mock.timers.reset()
  }
})

test('素手で体力が低くても、逃げても離れられなかった後は殴る（下限は、逃げる手が残るうちに選んだ戦いだけ）', async () => {
  mock.timers.enable({ apis: ['Date'], now: 3_000_000 })
  try {
    const { bot } = fakeBot()
    bot.health = 7 // 素手の規則（体力 12 以上）にも、素手の戦いの下限（8）にも届かない
    let attacks = 0
    bot.attack = () => { attacks++ }
    const state = { home: null }
    const records = []
    startReflex(bot, state, { preempt: async () => true, record: (e) => records.push(e) })
    const fire = async () => {
      for (let i = 0; i < 4; i++) bot.emit('physicsTick')
      while (state.reflex || state.danger) await new Promise((r) => setImmediate(r))
    }
    await fire()
    assert.equal(records.at(-1).action, 'reflex_flee') // 体力が低いので、1 回目は逃げる
    assert.equal(attacks, 0)
    await fire()
    assert.equal(records.at(-1).action, 'reflex_fight')
    assert.ok(attacks > 0, 'the stall escalation punches even at low health')
  } finally {
    mock.timers.reset()
  }
})

test('POST /reflex/release: 動いている反射を止め、しばらく近づいた相手に反応しない', async () => {
  mock.timers.enable({ apis: ['Date'], now: 2_000_000 })
  try {
    const { bot } = fakeBot()
    const state = { home: null }
    const records = []
    startReflex(bot, state, { preempt: async () => true, record: (e) => records.push(e) })
    const aborted = []
    state.danger = { controller: { abort: (e) => aborted.push(e.message) }, next: 'x' }
    const r = releaseReflex(state, 30)
    assert.equal(r.released, true)
    assert.match(aborted[0], /released/)
    assert.equal(state.danger.next, null)
    state.danger = null
    for (let i = 0; i < 8; i++) bot.emit('physicsTick')
    await settle(state)
    assert.equal(records.length, 0)
    mock.timers.tick(31000)
    for (let i = 0; i < 4; i++) bot.emit('physicsTick')
    assert.equal(state.reflex, true)
    assert.equal(releaseReflex({ danger: null }, 5).released, false)
    await settle(state)
  } finally {
    mock.timers.reset()
  }
})

test('家が近ければ、逃げられなかった次は帰る。帰れなければ戦う', async () => {
  mock.timers.enable({ apis: ['Date'], now: 3_000_000 })
  try {
    const { bot } = fakeBot()
    // 家の戸口が近い（家の外）
    const home = { door: v(4, 70, 0), inside: v(5, 70, 0), outside: v(3, 70, 0), min: v(5, 70, -1), max: v(7, 70, 1), breach: [] }
    const state = { home }
    const records = []
    startReflex(bot, state, { preempt: async () => true, record: (e) => records.push(e) })
    for (let round = 0; round < 2; round++) {
      for (let i = 0; i < 4; i++) bot.emit('physicsTick')
      while (state.reflex || state.danger) await new Promise((r) => setImmediate(r))
    }
    assert.equal(records[0].action, 'reflex_flee')
    assert.equal(records[1].action, 'reflex_go_home')
  } finally {
    mock.timers.reset()
  }
})

test('逃げるときは掘らない。ダメージが続けば（矢 2 本）離れられなかったとして止める', async () => {
  const { flee } = await import('../src/primitives.mjs')
  mock.timers.enable({ apis: ['Date'], now: 4_000_000 })
  try {
    const { bot, skeleton } = fakeBot()
    bot.pathfinder.movements = { canDig: true }
    const digging = []
    bot.pathfinder.setGoal = (g) => { if (g) digging.push(bot.pathfinder.movements.canDig) }
    // 1 回待つごとに、動きながら（止まったとみなされない）3 ダメージを受ける
    bot.waitForTicks = async () => { mock.timers.tick(1000); bot.entity.position = bot.entity.position.offset(0.3, 0, 0); skeleton.position = skeleton.position.offset(0.3, 0, 0); bot.health -= 3; await new Promise((r) => setImmediate(r)) }
    await assert.rejects(flee(bot, skeleton, new AbortController().signal), /could not get away from skeleton.*took 6 damage while fleeing/)
    assert.deepEqual(digging, [false]) // 逃げている間は掘らない
    assert.equal(bot.pathfinder.movements.canDig, true) // 終わったら戻す
  } finally {
    mock.timers.reset()
  }
})

test('ダメージがなくても、FLEE_MS たっても離れられなければ止める', async () => {
  const { flee } = await import('../src/primitives.mjs')
  mock.timers.enable({ apis: ['Date'], now: 5_000_000 })
  try {
    const { bot, skeleton } = fakeBot()
    bot.pathfinder.movements = { canDig: true }
    bot.waitForTicks = async () => { mock.timers.tick(1000); bot.entity.position = bot.entity.position.offset(0, 0, 0.3); skeleton.position = skeleton.position.offset(0, 0, 0.3); await new Promise((r) => setImmediate(r)) }
    await assert.rejects(flee(bot, skeleton, new AbortController().signal), /could not get away from skeleton.*15s/)
  } finally {
    mock.timers.reset()
  }
})
