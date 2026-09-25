import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { PRIMITIVES, TIMEOUTS_MS, DEFAULT_TIMEOUT_MS, LEG } from '../src/primitives.mjs'

const { Vec3 } = vec3Pkg

// A bot whose pathfinder arrives at once at the goal it is given
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

test('a far trip home is walked one leg per step (frun5: 270m took longer than the timeout)', async () => {
  const bot = walker(0, 0)
  const state = { home: { outside: new Vec3(0, 70, 200) } }
  const r = await PRIMITIVES.go_home(bot, state)
  assert.match(r, /^walked 48m toward home \(152m left\)$/)
  assert.equal(Math.round(bot.entity.position.z), LEG)
})

test('a far remembered place is walked to one leg per step, then reached', async () => {
  const bot = walker(0, 0)
  const c = { target: 'sheep', pos: new Vec3(0, 70, 80) }
  assert.match(await PRIMITIVES.goto_memory(bot, {}, c), /toward where sheep was seen \(32m left\)/)
  assert.match(await PRIMITIVES.goto_memory(bot, {}, c), /^arrived where sheep was seen/)
})

test('a far chest or furnace is walked to one leg per step (iron run: a withdraw timed out at 45s)', async () => {
  const bot = walker(0, 0)
  const c = { item: 'porkchop', count: 2, pos: new Vec3(0, 70, 130) }
  assert.match(await PRIMITIVES.withdraw(bot, { home: null }, c), /^walked 48m toward the chest \(82m left\)$/)
  assert.match(await PRIMITIVES.deposit(bot, { home: null }, c), /toward the chest \(34m left\)$/)
  assert.match(await PRIMITIVES.smelt(bot, {}, { pos: new Vec3(0, 70, -60) }), /toward the furnace/)
})

test('every action ends before the Python client gives up on it (60s)', () => {
  assert.ok(Math.max(DEFAULT_TIMEOUT_MS, ...Object.values(TIMEOUTS_MS)) <= 45000)
})
