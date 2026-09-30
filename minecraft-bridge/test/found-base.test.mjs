import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { Knowledge } from '../src/knowledge.mjs'
import { makeGoal, evaluate } from '../src/goals.mjs'
import { installLedger, furniturePlacedByMe } from '../src/ledger.mjs'
import { ownFurnitureInHome } from '../src/furniture.mjs'
import { newMemory } from '../src/memory.mjs'

const { Vec3 } = vec3Pkg
const v = (x, y, z) => new Vec3(x, y, z)
const md = minecraftData('1.21.4')
const k = new Knowledge(md)
const world = ({ inventory = {} } = {}) =>
  ({ inventory, stored: {}, blocks: {}, mobs: {}, table: 'near', unlocked: () => true, dig: () => [], hunt: () => [] })

// y 70 に 3x3 の室内（x 1..3、z 1..3）。村の小屋のベッドが (3,70,1) と (3,70,2)（足側と頭側）にある
const hut = { name: '見つけた拠点', found: true, door: v(2, 70, 4), inside: v(2, 70, 3), outside: v(2, 70, 5), min: v(1, 70, 1), max: v(3, 70, 3), bed: null, breach: [] }
const blocks = () => new Map([['3,70,1', 'red_bed'], ['3,70,2', 'red_bed']])

function botOf (cells) {
  return {
    registry: md,
    findBlocks: () => [],
    entity: { position: v(2, 70, 2) },
    time: { timeOfDay: 1000, age: 100 },
    inventory: { items: () => [] },
    blockAt: (p) => ({ name: cells.get(`${p.x},${p.y},${p.z}`) ?? (p.y < 70 ? 'stone' : 'air'), boundingBox: p.y < 70 ? 'block' : 'empty', position: p }),
    on: () => {}
  }
}

test('見つけた拠点のベッドでは placed(bed, home) を満たさない（村人のベッドで大目標が済んでいた）', () => {
  const state = { home: hut, plan: null, memory: newMemory(), placed: {} }
  const bot = botOf(blocks())
  const goal = makeGoal({ predicate: 'placed', item: 'bed', where: 'home' }, bot, state, k)
  const r = evaluate(bot, { ...state, goal }, k, world())
  assert.equal(r.met, false)
  assert.ok(r.lines.some((l) => l.includes('not yours')), r.lines.join('\n'))
  // 色つきのベッドと、作業台・かまど・チェストも同じ
  const cells = blocks(); cells.set('1,70,1', 'crafting_table')
  const b2 = botOf(cells)
  const table = makeGoal({ predicate: 'placed', item: 'crafting_table', where: 'home' }, b2, state, k)
  assert.equal(evaluate(b2, { ...state, goal: table }, k, world()).met, false)
  const red = makeGoal({ predicate: 'placed', item: 'red_bed', where: 'home' }, b2, state, k)
  assert.equal(evaluate(b2, { ...state, goal: red }, k, world()).met, false)
})

test('自分で置いたベッド（足側の記録だけ）なら頭側からでも自分の物と分かり、達成する', () => {
  const state = { home: hut, plan: null, memory: newMemory(), placed: { '3,70,1': { name: 'red_bed', at: 1 } } }
  const bot = botOf(blocks())
  assert.equal(furniturePlacedByMe(bot, state, v(3, 70, 1)), true)
  assert.equal(furniturePlacedByMe(bot, state, v(3, 70, 2)), true) // 頭側
  const goal = makeGoal({ predicate: 'placed', item: 'bed', where: 'home' }, bot, state, k)
  assert.equal(evaluate(bot, { ...state, goal }, k, world()).met, true)
  const red = makeGoal({ predicate: 'placed', item: 'red_bed', where: 'home' }, bot, state, k)
  assert.equal(evaluate(bot, { ...state, goal: red }, k, world()).met, true)
})

test('置いたあとに別のブロックに変わっていれば自分の物とは数えない', () => {
  const state = { home: hut, placed: { '3,70,1': { name: 'red_bed', at: 1 } } }
  const cells = blocks(); cells.set('3,70,1', 'white_bed'); cells.set('3,70,2', 'white_bed')
  assert.equal(ownFurnitureInHome(botOf(cells), state, 'bed'), null)
})

test('自分で建てた家では、置いてあるベッドをそのまま数える（記録は要らない）', () => {
  const own = { ...hut, name: 'ログハウス', found: false }
  const state = { home: own, plan: null, memory: newMemory(), placed: {} }
  const bot = botOf(blocks())
  const goal = makeGoal({ predicate: 'placed', item: 'bed', where: 'home' }, bot, state, k)
  assert.equal(evaluate(bot, { ...state, goal }, k, world()).met, true)
})

test('自分で置いた家具は blockPlaced で記録する（原木のほかベッド・作業台・かまど・チェスト。土は記録しない）', () => {
  const handlers = {}
  const bot = { ...botOf(new Map()), dig: async () => {}, on: (name, fn) => { handlers[name] = fn } }
  const state = {}
  installLedger(bot, state)
  ;['red_bed', 'crafting_table', 'furnace', 'chest', 'oak_log', 'dirt'].forEach((name, i) => {
    handlers.blockPlaced(null, { name, position: v(i, 70, 0) })
  })
  assert.deepEqual(Object.values(state.placed).map((e) => e.name).sort(), ['chest', 'crafting_table', 'furnace', 'oak_log', 'red_bed'])
})
