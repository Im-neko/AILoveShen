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

// 撃たれ続けるのをやめる（2026-10-07: ピリジャーに 18 秒で 18 ダメージを受けて死んだ。武器があると体力の下限がなかった）
function shooter (name, at, health) {
  const target = { id: 7, name, type: 'hostile', height: 1.95, position: at }
  const bot = {
    entities: { 7: target },
    entity: { position: new Vec3(0, 64, 0) },
    health,
    inventory: { items: () => [] },
    pathfinder: { goal: null, setGoal () {} },
    lookAt: async () => {},
    attack: () => {},
    waitForTicks: async () => {}
  }
  return { bot, target }
}

test('近づけない敵に撃たれてダメージが 6 たまったら、戦いをやめる（武器があっても）', async () => {
  const { bot, target } = shooter('pillager', new Vec3(8, 64, 0), 18)
  let ticks = 0
  bot.waitForTicks = async () => { ticks++; bot.health -= 3 }
  const text = await fight(bot, target, new AbortController().signal, 20000, 0)
  assert.match(text, /pillager still alive: under fire: lost 6 health while pillager was 8m away, out of reach/)
  assert.equal(ticks, 2) // 2 回撃たれたことを見た時点でやめる（3 回目の矢を待たない）
})

test('近づけない敵がいる間に体力が 6 を切ったら、戦いをやめる', async () => {
  const { bot, target } = shooter('skeleton', new Vec3(10, 64, 0), 5)
  const text = await fight(bot, target, new AbortController().signal, 20000, 0)
  assert.match(text, /skeleton still alive: out of reach: health is down to 5\/20 and skeleton is 10m away/)
})

test('殴り合える距離で受けたダメージは数えない・狩りの獲物（敵でない）では、やめない', async () => {
  const near = shooter('zombie', new Vec3(1, 64, 0), 18)
  let n = 0
  const controller = new AbortController()
  near.bot.waitForTicks = async () => { near.bot.health -= 3; if (++n >= 5) controller.abort() }
  assert.doesNotMatch(await fight(near.bot, near.target, controller.signal, 20000, 0), /under fire|out of reach/)
  // 殴り合いで 9 受けたあと、相手が離れても、それまでのダメージは「撃たれた」に数えない
  const away = shooter('zombie', new Vec3(1, 64, 0), 18)
  let k = 0
  const c3 = new AbortController()
  away.bot.waitForTicks = async () => {
    k++
    if (k <= 3) away.bot.health -= 3
    if (k === 3) away.target.position = new Vec3(5, 64, 0)
    if (k >= 6) c3.abort()
  }
  assert.doesNotMatch(await fight(away.bot, away.target, c3.signal, 20000, 0), /under fire|out of reach/)
  const prey = shooter('pig', new Vec3(10, 64, 0), 3)
  prey.target.type = 'passive'
  let m = 0
  const c2 = new AbortController()
  prey.bot.waitForTicks = async () => { if (++m >= 3) c2.abort() }
  assert.doesNotMatch(await fight(prey.bot, prey.target, c2.signal, 20000, 0), /under fire|out of reach/)
})
