import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { PRIMITIVES, TIMEOUTS_MS, DEFAULT_TIMEOUT_MS, LEG } from '../src/primitives.mjs'

const { Vec3 } = vec3Pkg

// 経路探索が、渡された目的地にすぐ着くボット
function walker (x, z) {
  const bot = {
    entity: { position: new Vec3(x, 70, z) },
    pathfinder: {
      goto: async (goal) => { bot.entity.position = new Vec3(goal.x, 70, goal.z) },
      setGoal: () => {}
    }
  }
  return bot
}

const { signal } = new AbortController()

test('遠い帰宅は 1 ステップに 1 区間ずつ歩く（frun5: 270m の帰宅が時間切れより長くかかった）', async () => {
  const bot = walker(0, 0)
  const state = { home: { outside: new Vec3(0, 70, 200) } }
  const r = await PRIMITIVES.go_home(bot, state, {}, signal)
  assert.match(r, /^walked 48m toward home \(152m left\)$/)
  assert.equal(Math.round(bot.entity.position.z), LEG)
})

test('覚えている遠い場所へは 1 ステップに 1 区間ずつ歩き、やがて着く', async () => {
  const bot = walker(0, 0)
  const c = { target: 'sheep', pos: new Vec3(0, 70, 80) }
  assert.match(await PRIMITIVES.goto_memory(bot, {}, c, signal), /toward where sheep was seen \(32m left\)/)
  assert.match(await PRIMITIVES.goto_memory(bot, {}, c, signal), /^arrived where sheep was seen/)
})

test('遠いチェストやかまどへは 1 ステップに 1 区間ずつ歩く（iron run: 取り出しが 45 秒で時間切れになった）', async () => {
  const bot = walker(0, 0)
  const c = { item: 'porkchop', count: 2, pos: new Vec3(0, 70, 130) }
  assert.match(await PRIMITIVES.withdraw(bot, { home: null }, c, signal), /^walked 48m toward the chest \(82m left\)$/)
  assert.match(await PRIMITIVES.deposit(bot, { home: null }, c, signal), /toward the chest \(34m left\)$/)
  assert.match(await PRIMITIVES.smelt(bot, {}, { pos: new Vec3(0, 70, -60) }, signal), /toward the furnace/)
})

test('どの行動も、Python クライアントが待つのをやめる（60 秒）前に終わる', () => {
  assert.ok(Math.max(DEFAULT_TIMEOUT_MS, ...Object.values(TIMEOUTS_MS)) <= 45000)
})
