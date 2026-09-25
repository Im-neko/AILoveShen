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

function flat (torches, items = []) {
  return {
    entity: { position: v(2.5, 70, 6.5) },
    time: { timeOfDay: 1000 },
    registry: md,
    blockAt: (p) => (p.y < 70 ? { name: 'stone', boundingBox: 'block' } : { name: 'air', boundingBox: 'empty' }),
    findBlocks: () => torches,
    inventory: { items: () => items.map((name) => ({ name, count: 8 })) }
  }
}

test('ground farther than a torch reaches is dark, nearest to the home first; the house is left out', () => {
  const dark = darkGround(flat([]), { home, plan: null }, 8)
  assert.ok(dark.length > 0)
  assert.ok(dark.every((p) => !(p.x >= 0 && p.x <= 4 && p.z >= 0 && p.z <= 4)), 'the house and its walls')
  const d = (p) => Math.abs(p.x - 2) + Math.abs(p.z - 3)
  assert.ok(d(dark[0]) <= d(dark[dark.length - 1]))
  assert.equal(darkGround(flat([v(2, 70, 3)]), { home, plan: null }, 8).length, darkGround(flat([]), { home, plan: null }, 8).filter((p) => Math.abs(p.x - 2) + Math.abs(p.y - 70) + Math.abs(p.z - 3) > LIT_REACH).length)
})

test('lit(radius): torches are placed on the dark ground, made first when none are held', () => {
  const state = { home, plan: null }
  const goal = makeGoal({ predicate: 'lit', distance: 16 }, flat([]), state, k)
  const noTorch = evaluate(flat([]), { ...state, goal }, k, world)
  assert.equal(noTorch.met, false)
  assert.ok(noTorch.lines.some((l) => l.startsWith('have 4 torch')), noTorch.lines.join('\n'))

  const r = evaluate(flat([], ['torch']), { ...state, goal }, k, world)
  assert.equal(r.leaves[0].kind, 'light')
  assert.match(r.lines[0], /^dark ground within 16 of the home: \d+ spots$/)

  // Torches every 12 blocks around cover it
  const grid = []
  for (let x = -24; x <= 28; x += 8) for (let z = -24; z <= 28; z += 8) grid.push(v(x, 70, z))
  assert.equal(evaluate(flat(grid), { ...state, goal }, k, world).met, true)
})
