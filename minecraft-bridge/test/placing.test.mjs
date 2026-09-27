import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { Knowledge } from '../src/knowledge.mjs'
import { makeGoal, evaluate, checkConditions } from '../src/goals.mjs'
import { placedNames, parseWhere } from '../src/placing.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')
const k = new Knowledge(md)
const v = (x, y, z) => new Vec3(x, y, z)

// 家（部屋 0..2 x 0..2、床の高さ y 64）と、置いてあるブロック
function setup ({ blocks = {}, items = {} } = {}) {
  const home = { door: v(1, 64, 3), inside: v(1, 64, 2), outside: v(1, 64, 4), min: v(0, 64, 0), max: v(2, 64, 2), bed: null, breach: [] }
  const bot = {
    registry: md,
    entity: { position: v(1, 64, 1) },
    time: { timeOfDay: 1000 },
    isSleeping: false,
    inventory: { items: () => Object.entries(items).map(([name, count]) => ({ name, count })) },
    findBlocks: () => [],
    blockAt: (p) => {
      const name = blocks[`${p.x},${p.y},${p.z}`] ?? (p.y < 64 ? 'stone' : 'air')
      return { name, boundingBox: name === 'air' || name.endsWith('torch') ? 'empty' : 'block', position: p, getProperties: () => ({}) }
    }
  }
  return { bot, state: { home, plan: null } }
}
const leaves = (bot, state, spec) => {
  const goal = makeGoal(spec, bot, state, k)
  return { goal, r: evaluate(bot, { ...state, goal }, k, { inventory: {}, blocks: {}, mobs: {}, table: null, unlocked: () => true, dig: () => [], hunt: () => [] }) }
}

test('好きな物を好きな場所に: 家の中の松明（壁についた松明も数える）', () => {
  const { bot, state } = setup({ items: { torch: 4 } })
  const { goal, r } = leaves(bot, state, { predicate: 'placed', item: 'torch', where: 'home' })
  assert.deepEqual(goal.spec, { predicate: 'placed', item: 'torch', where: 'home', count: 1 })
  assert.equal(r.met, false)
  const leaf = r.leaves.find((l) => l.kind === 'place_at')
  assert.equal(leaf.item, 'torch')
  assert.equal(leaf.pos.y, 64) // 部屋の床の高さ
  assert.ok(!leaf.pos.equals(state.home.inside)) // ドアのすぐ内側は通り道

  const lit = setup({ blocks: { '0,65,0': 'wall_torch' } })
  assert.equal(leaves(lit.bot, lit.state, { predicate: 'placed', item: 'torch', where: 'home' }).r.met, true)
})

test('家のまわり・座標のまわりに数を決めて置く。持っていなければ作る', () => {
  const { bot, state } = setup({ blocks: { '6,64,1': 'lantern' } })
  const { r } = leaves(bot, state, { predicate: 'placed', item: 'lantern', where: 'near_home', count: 2 })
  assert.equal(r.met, false)
  assert.ok(r.lines.includes('lantern around the home: 1/2'))
  assert.equal(r.leaves.some((l) => l.kind === 'place_at'), false) // 持っていない
  const [c] = checkConditions([{ predicate: 'placed', item: 'lantern', where: '6,64,1' }], bot, state, k, { inventory: {}, blocks: {}, mobs: {}, table: null, unlocked: () => true, dig: () => [], hunt: () => [] })
  assert.equal(c.met, true)
})

test('置けない物、知らない場所は断る', () => {
  const { bot, state } = setup()
  assert.throws(() => makeGoal({ predicate: 'placed', item: 'stick', where: 'home' }, bot, state, k), /a block when placed/)
  assert.throws(() => makeGoal({ predicate: 'placed', item: 'torch', where: 'the moon' }, bot, state, k), /where must be/)
  assert.throws(() => makeGoal({ predicate: 'placed', item: 'torch', where: 'build:tower' }, bot, state, k), /no build named tower/)
  assert.deepEqual([...placedNames('oak_sign')].sort(), ['oak_sign', 'oak_wall_sign'])
  assert.equal(parseWhere('10, 64, -3', state).kind, 'point')
})
