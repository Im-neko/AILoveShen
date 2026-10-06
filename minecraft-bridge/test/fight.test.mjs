import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { fight } from '../src/primitives.mjs'

const { Vec3 } = vec3Pkg

test('離れた相手を追う目標は一度だけ置く（置き直すと歩く途中の掘りが止まる）', async () => {
  const target = { id: 7, name: 'zombie', height: 1.95, position: new Vec3(10, 64, 0) }
  const goals = []
  let ticks = 0
  const controller = new AbortController()
  const bot = {
    entities: { 7: target },
    entity: { position: new Vec3(0, 64, 0) },
    pathfinder: {
      goal: null,
      setGoal (g) { this.goal = g; goals.push(g) }
    },
    waitForTicks: async () => { if (++ticks >= 5) controller.abort() }
  }
  await fight(bot, target, controller.signal)
  assert.equal(goals.filter((g) => g !== null).length, 1)
  assert.equal(goals.at(-1), null) // 終わったら止める
})

test('下にいる相手には掘って近づかない。同じ高さや上なら掘る。終わったら戻す', async () => {
  const target = { id: 7, name: 'spider', height: 0.9, position: new Vec3(5, 60, 0) }
  const movements = { canDig: true }
  const digging = []
  let ticks = 0
  const controller = new AbortController()
  const bot = {
    entities: { 7: target },
    entity: { position: new Vec3(0, 64, 0) },
    pathfinder: { goal: null, movements, setGoal (g) { this.goal = g } },
    waitForTicks: async () => {
      digging.push(movements.canDig)
      if (++ticks === 1) target.position = new Vec3(5, 63, 0) // 1 段ほど下: 掘ってよい
      if (ticks >= 2) controller.abort()
    }
  }
  await fight(bot, target, controller.signal)
  assert.deepEqual(digging, [false, true]) // 4 m 下 → 掘らない、1 m 下 → 掘る
  assert.equal(movements.canDig, true)
})

test('洞窟へ逃げ込む相手は、戦い始めの高さから 4 ブロックより下へは追わない', async () => {
  const target = { id: 7, name: 'spider', height: 0.9, position: new Vec3(20, 60, 0) }
  const controller = new AbortController()
  const bot = {
    entities: { 7: target },
    entity: { position: new Vec3(0, 76, 0) },
    pathfinder: { goal: null, setGoal () {} },
    waitForTicks: async () => { bot.entity.position = new Vec3(5, 70, 0) } // 6 m 降りた
  }
  const r = await fight(bot, target, controller.signal)
  assert.match(r, /will not follow it .*below/)
})

test('素手の戦いは、体力が minHealth を切ったらやめる（次の反射は逃げる）', async () => {
  const target = { id: 7, name: 'zombie', height: 1.95, position: new Vec3(1, 64, 0) }
  const bot = {
    entities: { 7: target },
    entity: { position: new Vec3(0, 64, 0) },
    health: 7,
    inventory: { items: () => [] },
    pathfinder: { goal: null, setGoal () {} },
    lookAt: async () => {},
    attack: () => { throw new Error('should not attack') },
    waitForTicks: async () => {}
  }
  const text = await fight(bot, target, new AbortController().signal, 20000, 8)
  assert.match(text, /zombie still alive: health is down to 7\/20: not trading blows with bare hands/)
})
