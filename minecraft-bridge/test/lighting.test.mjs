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
// 平らな地面（y 70 より下は石）の原点にある 3x3 の拠点
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

test('松明が届くより遠い地面は暗い。拠点に近い順に並べ、家は除く', () => {
  const bot = flat([])
  const { dark, unloaded } = darkGround(bot, state, 8)
  assert.equal(unloaded, 0)
  assert.ok(dark.length > 0)
  assert.ok(dark.every((p) => !(p.x >= 0 && p.x <= 4 && p.z >= 0 && p.z <= 4)), '家とその壁は除く')
  assert.ok(reach(dark[0], home.inside) <= reach(dark[dark.length - 1], home.inside))
  assert.deepEqual(bot.searchedFrom, home.inside, '明かりはボットではなく拠点の周りで探す')
  const lit = darkGround(flat([v(2, 70, 3)]), state, 8).dark
  assert.deepEqual(lit, dark.filter((p) => reach(p, v(2, 70, 3)) > LIT_REACH))
})

test('lit(radius): 暗い地面に松明を置く。持っていなければ先に作る', () => {
  const goal = makeGoal({ predicate: 'lit', distance: 16 }, flat([]), state, k)
  const noTorch = evaluate(flat([]), { ...state, goal }, k, world)
  assert.equal(noTorch.met, false)
  assert.ok(noTorch.lines.some((l) => l.startsWith('have 4 torch')), noTorch.lines.join('\n'))

  const r = evaluate(flat([], ['torch']), { ...state, goal }, k, world)
  assert.equal(r.leaves[0].kind, 'light')
  assert.match(r.lines[0], /^dark ground within 16 of the home: \d+ spots$/)

  // 周りに 8 ブロックおきに松明があれば全体が照らされる
  const grid = []
  for (let x = -24; x <= 28; x += 8) for (let z = -24; z <= 28; z += 8) grid.push(v(x, 70, z))
  assert.equal(evaluate(flat(grid), { ...state, goal }, k, world).met, true)
})

test('lit(radius) は見えていない地面（ボットが拠点から遠い）では達成にならず、拠点へ戻る', () => {
  const grid = []
  for (let x = -24; x <= 28; x += 8) for (let z = -24; z <= 28; z += 8) grid.push(v(x, 70, z))
  const bot = flat(grid, ['torch'], { seen: (p) => p.x < 10 })
  const goal = makeGoal({ predicate: 'lit', distance: 16 }, bot, state, k)
  const r = evaluate(bot, { ...state, goal }, k, world)
  assert.equal(r.met, false)
  assert.deepEqual(r.leaves, [{ kind: 'go_home' }])
})
