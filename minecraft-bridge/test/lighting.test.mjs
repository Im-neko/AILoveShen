import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { Knowledge } from '../src/knowledge.mjs'
import { makeGoal, evaluate } from '../src/goals.mjs'
import { darkGround, LIT_REACH } from '../src/lighting.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')
const k = new Knowledge(md)
const v = (x, y, z) => new Vec3(x, y, z)
// A 3x3 home at the origin on flat ground (stone below y 70)
const home = { door: v(2, 70, 4), inside: v(2, 70, 3), outside: v(2, 70, 5), min: v(1, 70, 1), max: v(3, 70, 3), bed: null, breach: [] }
const world = { inventory: {}, stored: {}, smelting: {}, blocks: {}, mobs: {}, table: 'near', furnace: null, unlocked: () => true, remembered: new Set(), dig: () => [], hunt: () => [] }

function flat (torches, items = [], { seen = () => true } = {}) {
  const bot = {
    entity: { position: v(2.5, 70, 6.5) },
    time: { timeOfDay: 1000 },
    registry: md,
    blockAt: (p) => (!seen(p) ? null : p.y < 70 ? { name: 'stone', boundingBox: 'block' } : { name: 'air', boundingBox: 'empty' }),
    findBlocks: (opts) => { bot.searchedFrom = opts.point; return torches },
    inventory: { items: () => items.map((name) => ({ name, count: 8 })) }
  }
  return bot
}
const state = { home, plan: null }
const reach = (p, q) => Math.abs(p.x - q.x) + Math.abs(p.y - q.y) + Math.abs(p.z - q.z)

test('ground farther than a torch reaches is dark, nearest to the home first; the house is left out', () => {
  const bot = flat([])
  const { dark, unloaded } = darkGround(bot, state, 8)
  assert.equal(unloaded, 0)
  assert.ok(dark.length > 0)
  assert.ok(dark.every((p) => !(p.x >= 0 && p.x <= 4 && p.z >= 0 && p.z <= 4)), 'the house and its walls')
  assert.ok(reach(dark[0], home.inside) <= reach(dark[dark.length - 1], home.inside))
  assert.deepEqual(bot.searchedFrom, home.inside, 'the lights are searched around the home, not the bot')
  const lit = darkGround(flat([v(2, 70, 3)]), state, 8).dark
  assert.deepEqual(lit, dark.filter((p) => reach(p, v(2, 70, 3)) > LIT_REACH))
})

test('lit(radius): torches are placed on the dark ground, made first when none are held', () => {
  const goal = makeGoal({ predicate: 'lit', distance: 16 }, flat([]), state, k)
  const noTorch = evaluate(flat([]), { ...state, goal }, k, world)
  assert.equal(noTorch.met, false)
  assert.ok(noTorch.lines.some((l) => l.startsWith('have 4 torch')), noTorch.lines.join('\n'))

  const r = evaluate(flat([], ['torch']), { ...state, goal }, k, world)
  assert.equal(r.leaves[0].kind, 'light')
  assert.match(r.lines[0], /^dark ground within 16 of the home: \d+ spots$/)

  // Torches every 8 blocks around cover it
  const grid = []
  for (let x = -24; x <= 28; x += 8) for (let z = -24; z <= 28; z += 8) grid.push(v(x, 70, z))
  assert.equal(evaluate(flat(grid), { ...state, goal }, k, world).met, true)
})

test('lit(radius) is never met on ground out of view (the bot far from home): it goes back', () => {
  const grid = []
  for (let x = -24; x <= 28; x += 8) for (let z = -24; z <= 28; z += 8) grid.push(v(x, 70, z))
  const bot = flat(grid, ['torch'], { seen: (p) => p.x < 10 })
  const goal = makeGoal({ predicate: 'lit', distance: 16 }, bot, state, k)
  const r = evaluate(bot, { ...state, goal }, k, world)
  assert.equal(r.met, false)
  assert.deepEqual(r.leaves, [{ kind: 'go_home' }])
})
