import { test } from 'node:test'
import assert from 'node:assert/strict'
import minecraftData from 'minecraft-data'
import vec3Pkg from 'vec3'
import { Knowledge } from '../src/knowledge.mjs'
import { makeGoal, evaluate } from '../src/goals.mjs'
import { ground } from '../src/candidates.mjs'
import { goalKeep } from '../src/spare.mjs'
import { biomeMatches, foundKind, foundNow, aheadOrder } from '../src/found.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')
const k = new Knowledge(md)
const world = { inventory: {}, blocks: {}, mobs: {}, table: null, unlocked: () => true, dig: () => [], hunt: () => [] }
const biomeId = (name) => md.biomesByName[name].id

function fakeBot ({ at = [0, 64, 0], biome = 'forest', mobs = [] } = {}) {
  const entity = { position: new Vec3(...at) }
  const entities = { 1: entity }
  mobs.forEach((m, i) => { entities[i + 2] = { name: m.name, position: new Vec3(...m.at) } })
  return { entity, entities, registry: md, time: { timeOfDay: 1000, age: 100 }, blockAt: () => ({ biome: { id: biomeId(biome) } }), inventory: { items: () => [] } }
}
const state = () => ({ home: null, plan: null, memory: { biomes: { '0,0': 'forest', '0,-1': 'taiga' }, explored: {}, places: {}, chests: {}, deaths: [] } })

test('群系名は、_ 区切りで target で終わるものも同じ群系として数える', () => {
  assert.ok(biomeMatches('plains', 'plains'))
  assert.ok(biomeMatches('sunflower_plains', 'plains'))
  assert.ok(biomeMatches('old_growth_pine_taiga', 'taiga'))
  assert.ok(!biomeMatches('plainsx', 'plains'))
  assert.ok(!biomeMatches(null, 'plains'))
})

test('found の target はモブか群系の名前だけ。距離は 8〜128', () => {
  const bot = fakeBot()
  assert.equal(foundKind(bot, 'sheep'), 'mob')
  assert.equal(foundKind(bot, 'plains'), 'biome')
  assert.equal(foundKind(bot, 'stone'), null)
  const goal = makeGoal({ predicate: 'found', item: 'Sheep', distance: 64 }, bot, state(), k)
  assert.deepEqual(goal.spec, { predicate: 'found', item: 'sheep', distance: 64 })
  assert.deepEqual(goal.start, { x: 0, y: 64, z: 0 })
  assert.throws(() => makeGoal({ predicate: 'found', item: 'cobblestone', distance: 64 }, bot, state(), k), /neither a mob nor a biome/)
  assert.throws(() => makeGoal({ predicate: 'found', item: 'sheep', distance: 4 }, bot, state(), k), /distance must be 8-128/)
  assert.throws(() => makeGoal({ predicate: 'found', item: 'sheep', distance: 200 }, bot, state(), k), /distance must be 8-128/)
})

test('モブが 48 ブロック以内に読み込まれていれば見つかった。遠ければ、始めた所から離れる探索の手が出る', () => {
  const st = state()
  const bot = fakeBot({ mobs: [{ name: 'sheep', at: [30, 64, 0] }, { name: 'cow', at: [3, 64, 0] }] })
  const goal = makeGoal({ predicate: 'found', item: 'sheep', distance: 64 }, bot, st, k)
  st.goal = goal
  const near = evaluate(bot, st, k, world)
  assert.equal(near.met, true)
  assert.match(near.lines[0], /found: sheep 30m away/)

  const far = fakeBot({ mobs: [{ name: 'sheep', at: [80, 64, 0] }, { name: 'cow', at: [3, 64, 0] }] })
  const r = evaluate(far, st, k, world)
  assert.equal(r.met, false)
  assert.equal(r.leaves.length, 1)
  assert.equal(r.leaves[0].kind, 'explore')
  assert.deepEqual(r.leaves[0].sources, ['sheep'])
  assert.equal(r.leaves[0].reach, 64)
  assert.deepEqual(r.impossible, [])
})

test('群系は、いまその群系に立っていれば見つかった', () => {
  const st = state()
  const bot = fakeBot({ biome: 'forest' })
  st.goal = makeGoal({ predicate: 'found', item: 'plains', distance: 64 }, bot, st, k)
  assert.equal(evaluate(bot, st, k, world).met, false)
  const there = fakeBot({ biome: 'sunflower_plains', at: [20, 64, 0] })
  const r = evaluate(there, st, k, world)
  assert.equal(r.met, true)
  assert.match(r.lines[0], /standing in sunflower_plains/)
  assert.equal(foundNow(bot, 'plains'), null)
})

test('始めた所から distance ブロック探しても見つからなければ行き止まり（探索の手は出さず、歩いた群系を理由に添える）', () => {
  const st = state()
  const bot = fakeBot()
  st.goal = makeGoal({ predicate: 'found', item: 'sheep', distance: 32 }, bot, st, k)
  const gone = fakeBot({ at: [0, 64, -40] })
  const r = evaluate(gone, st, k, world)
  assert.equal(r.met, false)
  assert.deepEqual(r.leaves, [])
  assert.match(r.impossible[0], /searched 40m from where it began and found no sheep \(biomes walked through: forest, taiga\)/)
})

test('探索の向きの並び: 水の手前は後ろへ、探している群系の手前は前へ（なければ変えない）', () => {
  const dirs = [{ id: 'n', biome_ahead: 'ocean' }, { id: 'e', biome_ahead: 'plains' }, { id: 's' }, { id: 'w', biome_ahead: 'cold_ocean' }]
  const order = (target) => [...dirs].sort((a, b) => aheadOrder(target, a, b)).map((d) => d.id)
  assert.deepEqual(order('plains'), ['e', 's', 'n', 'w'])
  assert.deepEqual(order('sheep'), ['e', 's', 'n', 'w'])
  assert.deepEqual(order(undefined), ['e', 's', 'n', 'w'])
  assert.equal(aheadOrder('sheep', { biome_ahead: 'forest' }, { biome_ahead: 'taiga' }), 0)
  assert.equal(aheadOrder('sheep', { biome_ahead: 'river' }, { biome_ahead: 'beach' }), 0)
})

test('found の item は生き物・群系の名前で、品物としては引かない（ground が例外にならず、残す物にも入らない）', () => {
  const st = state()
  const bot = fakeBot()
  for (const item of ['sheep', 'plains']) {
    st.goal = makeGoal({ predicate: 'found', item, distance: 96 }, bot, st, k)
    const status = evaluate(bot, st, k, world)
    assert.doesNotThrow(() => ground(bot, st, k, world, status))
    assert.ok(!goalKeep(k, st.goal.spec).has(item))
    assert.ok(goalKeep(k, st.goal.spec).size > 0) // 食料は残す
  }
})
