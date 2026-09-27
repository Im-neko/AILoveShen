import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { Knowledge } from '../src/knowledge.mjs'
import { makeGoal, evaluate, checkConditions } from '../src/goals.mjs'
import { placedNames, parseWhere } from '../src/placing.mjs'
import { ground } from '../src/candidates.mjs'

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
const WORLD = { inventory: {}, blocks: {}, mobs: {}, table: null, unlocked: () => true, dig: () => [], hunt: () => [] }
// 置く葉から出る候補
const candidatesFor = (bot, state, r) => ground({ ...bot, health: 20, food: 20, entities: {} }, state, k, WORLD, { leaves: r.leaves.filter((l) => l.kind === 'place_at') })
  .candidates.filter((c) => c.verb === 'place_at')
const leaves = (bot, state, spec) => {
  const goal = makeGoal(spec, bot, state, k)
  return { goal, r: evaluate(bot, { ...state, goal }, k, { inventory: {}, blocks: {}, mobs: {}, table: null, unlocked: () => true, dig: () => [], hunt: () => [] }) }
}

test('好きな物を好きな場所に: 家の中の松明（壁についた松明も数える）', () => {
  const { bot, state } = setup({ items: { torch: 4 }, blocks: { '-1,65,1': 'oak_planks' } }) // 西の壁の 1 マス
  const { goal, r } = leaves(bot, state, { predicate: 'placed', item: 'torch', where: 'home' })
  assert.deepEqual(goal.spec, { predicate: 'placed', item: 'torch', where: 'home', count: 1 })
  assert.equal(r.met, false)
  const leaf = r.leaves.find((l) => l.kind === 'place_at')
  assert.equal(leaf.item, 'torch')
  // 松明は壁に付ける: 目の高さ（床 +1）で、壁（家の外の列）を面に借りる
  assert.deepEqual([leaf.pos, leaf.against], [v(0, 65, 1), v(-1, 65, 1)])
  assert.ok(leaf.pos.x !== state.home.inside.x || leaf.pos.z !== state.home.inside.z) // ドアのすぐ内側は通り道
  // 提案は道具と同じ place_at（面つき）で動く（2026-09-27: 同じ名前の 2 つの定義で、提案が落ちていた）
  const [c] = candidatesFor(bot, state, r)
  assert.equal(c.verb, 'place_at')
  assert.match(c.id, /^place torch in the home on the wall at /)
  assert.deepEqual(c.against, leaf.against)

  // 壁に付けられない物は床に
  const { r: lanterns } = leaves(setup({ items: { lantern: 1 } }).bot, state, { predicate: 'placed', item: 'lantern', where: 'home' })
  const floor = lanterns.leaves.find((l) => l.kind === 'place_at')
  assert.equal(floor.pos.y, 64)
  assert.equal(floor.against.y, 63)

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

test('小目標の束（設計書 44）: 条件ごとに評価して、済んだかは全部、残りは合計、候補は束全体から', () => {
  const { bot, state } = setup({ items: { torch: 1 }, blocks: { '-1,65,1': 'oak_planks' } })
  const goal = makeGoal({
    predicate: 'placed',
    item: 'torch',
    where: 'home',
    together: [
      { predicate: 'have', item: 'stick', count: 2 },
      { predicate: 'built', name: 'annex' } // まだない建物は外して理由を出す
    ]
  }, bot, state, k)
  assert.equal(goal.together.length, 1)
  const r = evaluate(bot, { ...state, goal }, k, WORLD)
  assert.equal(r.met, false)
  assert.deepEqual(r.members.map((m) => [m.spec.predicate, m.met]), [['placed', false], ['have', false]])
  assert.equal(r.remaining, r.members.reduce((n, m) => n + m.remaining, 0))
  assert.equal(r.lines[0], '[1/2] placed(torch, 1, home): 1 left')
  assert.ok(r.lines.some((l) => /^left out of the bundle: built\(annex\)/.test(l)))
  // どの条件のための候補か
  const c = candidatesFor(bot, { ...state, goal }, r)[0]
  assert.equal(c.for, 'placed(torch, 1, home)')
  // 束には条件だけ。4 つまで
  assert.throws(() => makeGoal({ predicate: 'have', item: 'log', count: 1, together: [{ predicate: 'at_home' }] }, bot, state, k), /at_home cannot be in a bundle/)
  assert.throws(() => makeGoal({ predicate: 'have', item: 'log', count: 1, together: [1, 2, 3, 4].map(() => ({ predicate: 'have', item: 'log', count: 1 })) }, bot, state, k), /at most 4/)
})
