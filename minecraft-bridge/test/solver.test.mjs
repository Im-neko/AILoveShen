import { test } from 'node:test'
import assert from 'node:assert/strict'
import minecraftData from 'minecraft-data'
import { Knowledge } from '../src/knowledge.mjs'
import { solve } from '../src/solver.mjs'

const k = new Knowledge(minecraftData('1.21.4'))
const world = ({ inventory = {}, blocks = {}, mobs = {}, table = null, unlocked = () => true } = {}) =>
  ({ inventory, blocks, mobs, table, unlocked })
const kinds = (r) => r.leaves.map((l) => `${l.kind}:${l.item}`)

test('planks from nothing: dig a log first', () => {
  const r = solve(k, world({ blocks: { oak_log: 5 } }), [{ spec: 'planks', count: 4 }])
  assert.equal(r.met, false)
  assert.deepEqual(kinds(r), ['dig:oak_log'])
  assert.equal(r.remaining, 2)
})

test('planks from one log: craft', () => {
  const r = solve(k, world({ inventory: { oak_log: 1 } }), [{ spec: 'planks', count: 4 }])
  assert.deepEqual(kinds(r), ['craft:oak_planks'])
})

test('held items meet the need', () => {
  const r = solve(k, world({ inventory: { birch_planks: 2, oak_planks: 3 } }), [{ spec: 'planks', count: 5 }])
  assert.equal(r.met, true)
  assert.equal(r.remaining, 0)
  assert.deepEqual(r.leaves, [])
})

test('logs needed as logs are set aside before planks are made from the rest', () => {
  // A house with 12 log pillars and 20 planks, holding 13 logs: only 1 log may become planks
  const r = solve(k, world({ inventory: { oak_log: 13 }, blocks: { oak_log: 9 } }),
    [{ spec: 'log', count: 12 }, { spec: 'planks', count: 20 }])
  assert.equal(r.nodes[0].have, 12)
  const planks = r.nodes[1]
  const craft = planks.leaf
  assert.equal(craft.item, 'oak_planks')
  assert.equal(craft.times, 5)
  assert.equal(planks.children[0].have, 1) // the one spare log
  assert.deepEqual(kinds(r), ['dig:oak_log'])
})

test('a 3x3 recipe needs a crafting table: place one that is held', () => {
  const r = solve(k, world({ inventory: { oak_planks: 2, stick: 1, crafting_table: 1 } }), [{ spec: 'wooden_sword', count: 1 }])
  assert.deepEqual(kinds(r), ['place:crafting_table'])
})

test('a 3x3 recipe with a table nearby is ready', () => {
  const r = solve(k, world({ inventory: { oak_planks: 2, stick: 1 }, table: 'near' }), [{ spec: 'wooden_sword', count: 1 }])
  assert.deepEqual(kinds(r), ['craft:wooden_sword'])
})

test('without a table, craft one from planks', () => {
  const r = solve(k, world({ inventory: { oak_planks: 6, stick: 1 } }), [{ spec: 'wooden_sword', count: 1 }])
  assert.deepEqual(kinds(r), ['craft:crafting_table'])
})

test('a recipe not unlocked yet is reported, not offered', () => {
  const r = solve(k, world({ inventory: { oak_planks: 2, stick: 1 }, table: 'near', unlocked: (i) => i !== 'wooden_sword' }),
    [{ spec: 'wooden_sword', count: 1 }])
  assert.deepEqual(r.leaves, [])
  assert.match(r.blocked[0], /wooden_sword is not unlocked/)
})

test('any bed: the color of the wool held decides the recipe', () => {
  const r = solve(k, world({ inventory: { red_wool: 3, oak_planks: 3 }, table: 'reach' }), [{ spec: 'bed', count: 1 }])
  assert.deepEqual(kinds(r), ['craft:red_bed'])
})

test('wool comes from sheep (missing from the loot data)', () => {
  const r = solve(k, world({ inventory: { oak_planks: 3 }, mobs: { sheep: 2 }, table: 'near' }), [{ spec: 'bed', count: 1 }])
  assert.deepEqual(kinds(r), ['kill:white_wool'])
})

test('dyeing recipes are not used (no bed from another bed)', () => {
  const recipes = k.recipes('red_bed')
  assert.ok(recipes.length > 0)
  assert.ok(recipes.every((r) => !Object.keys(r.ingredients).some((n) => n.endsWith('_bed'))))
  assert.ok(k.recipes('red_wool').every((r) => !Object.keys(r.ingredients).some((n) => n.endsWith('_wool'))))
})

test('a source not in sight means exploring, reported as blocked', () => {
  const r = solve(k, world(), [{ spec: 'planks', count: 4 }])
  assert.equal(r.leaves[0].kind, 'explore')
  assert.match(r.blocked[0], /nearby/)
})

test('loot: silk-touch drops, leaves and hostile mobs are no sources', () => {
  assert.equal(k.blockSources.get('glass'), undefined)
  assert.ok(!(k.blockSources.get('apple') ?? []).some((b) => b.endsWith('_leaves')))
  assert.ok(!(k.blockSources.get('stick') ?? []).some((b) => b.endsWith('_leaves')))
  assert.equal(k.mobSources.get('rotten_flesh'), undefined)
  assert.deepEqual(k.blockSources.get('cobblestone'), ['stone'])
  assert.ok(!k.resolve('food').members.includes('rotten_flesh'))
})

test('food out of sight: an animal seen before is gone back to rather than searched for', () => {
  const w = { ...world(), remembered: new Set(['cow']) }
  const r = solve(k, w, [{ spec: 'food', count: 4 }])
  const explore = r.leaves.find((l) => l.kind === 'explore')
  assert.deepEqual(explore.sources, ['cow'])
  assert.match(explore.reason, /seen before/)
})

test('food: hunt an animal in sight', () => {
  const r = solve(k, world({ mobs: { cow: 1 } }), [{ spec: 'food', count: 4 }])
  assert.deepEqual(kinds(r), ['kill:beef'])
})

test('a block needing a tool asks for the tool first', () => {
  const r = solve(k, world({ inventory: { oak_planks: 20, stick: 4 }, blocks: { stone: 10 }, table: 'near' }),
    [{ spec: 'cobblestone', count: 3 }])
  assert.deepEqual(kinds(r), ['craft:wooden_pickaxe'])
})

test('unknown items are rejected', () => {
  assert.throws(() => k.resolve('unobtainium'), /unknown item/)
})
