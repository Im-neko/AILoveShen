import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { Knowledge, smeltingProduct } from '../src/knowledge.mjs'
import { solve } from '../src/solver.mjs'
import { ground } from '../src/candidates.mjs'
import { newMemory, rememberFurnace, smeltingCounts } from '../src/memory.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')
const k = new Knowledge(md)
const world = (o = {}) => ({ inventory: {}, stored: {}, smelting: {}, blocks: {}, mobs: {}, table: 'near', furnace: null, unlocked: () => true, remembered: new Set(), ...o })
const kinds = (r) => r.leaves.map((l) => `${l.kind}:${l.item ?? ''}`)

test('鉄の剣: 石のツルハシで鉄を掘り、かまどを作って置き、精錬してクラフトする', () => {
  const r = solve(k, world({ inventory: { stone_pickaxe: 1, oak_planks: 8 }, blocks: { iron_ore: 3, stone: 20 } }), [{ spec: 'iron_sword', count: 1 }])
  assert.equal(r.lines[1], '  have 2 iron_ingot (0/2): smelt raw_iron in a furnace')
  assert.ok(r.lines.some((l) => l.includes('a furnace nearby')), r.lines.join('\n'))
  assert.deepEqual(kinds(r), ['dig:raw_iron', 'dig:cobblestone', 'craft:stick'])
})

test('材料があり近くにかまどがあれば精錬が末端になる。燃料は持っている物を先に使う', () => {
  const r = solve(k, world({ inventory: { raw_iron: 2, coal: 1, stick: 1 }, furnace: 'near' }), [{ spec: 'iron_sword', count: 1 }])
  const smelt = r.leaves.find((l) => l.kind === 'smelt')
  assert.equal(smelt.count, 2)
  assert.ok(smelt.fuels.includes('coal') && smelt.fuelCount === 1, JSON.stringify(smelt))
})

test('かまどで作っている物は取り出し、集め直さない', () => {
  const r = solve(k, world({ inventory: { stick: 1 }, smelting: { iron_ingot: 2 } }), [{ spec: 'iron_sword', count: 1 }])
  assert.deepEqual(r.leaves, [{ kind: 'smelt', item: 'iron_ingot', count: 0 }])
})

test('石炭がなければ、原木を焼いた木炭で松明を作る', () => {
  const r = solve(k, world({ inventory: { oak_log: 3, stick: 1 }, furnace: 'near' }), [{ spec: 'torch', count: 4 }])
  assert.equal(r.lines[1], '  have 1 charcoal (0/1): smelt log in a furnace')
})

test('覚えているかまど: できた物とこれからできる物を製品ごとに数え、空にしたかまどは外す', () => {
  const m = newMemory()
  rememberFurnace(m, new Vec3(1, 70, 1), { iron_ingot: 3 }, 0)
  rememberFurnace(m, new Vec3(5, 70, 1), { iron_ingot: 1, charcoal: 2 }, 0)
  assert.deepEqual(smeltingCounts(m), { iron_ingot: 4, charcoal: 2 })
  rememberFurnace(m, new Vec3(1, 70, 1), {}, 10)
  assert.deepEqual(smeltingCounts(m), { iron_ingot: 1, charcoal: 2 })
})

test('精錬の候補は、近くのかまどで使う材料と手持ちの燃料を示す', () => {
  const furnace = { position: new Vec3(3, 70, 0), name: 'furnace' }
  const bot = {
    entity: { position: new Vec3(0.5, 70, 0.5) },
    entities: {},
    time: { timeOfDay: 1000 },
    health: 20,
    food: 20,
    heldItem: null,
    blockAt: () => null, // 明るさのデータがない: 暗いとは判定しない
    findBlock: () => furnace,
    registry: md,
    inventory: { items: () => [{ name: 'raw_iron', count: 2 }, { name: 'birch_planks', count: 5 }] }
  }
  const leaf = { kind: 'smelt', item: 'iron_ingot', input: 'raw_iron', count: 2, inputs: ['raw_iron'], fuels: k.resolve('planks').members, fuelCount: 2, ready: true }
  const state = { home: null, plan: null, unreachableDrops: new Set() }
  const c = ground(bot, state, k, { dig: () => [], hunt: () => [] }, { leaves: [leaf] }).candidates.find((c) => c.verb === 'smelt')
  assert.equal(c.id, 'smelt 2 raw_iron into iron_ingot')
  assert.equal(c.fuel, 'birch_planks')
  assert.deepEqual(c.pos, furnace.position)
})

test('遠くのかまどに残した物は、残した所まで取りに行く', () => {
  const memory = newMemory()
  rememberFurnace(memory, new Vec3(200, 70, 0), { iron_ingot: 2 }, 0)
  const bot = {
    entity: { position: new Vec3(0.5, 70, 0.5) },
    entities: {},
    time: { timeOfDay: 1000 },
    health: 20,
    food: 20,
    heldItem: null,
    blockAt: () => null, // 明るさのデータがない: 暗いとは判定しない
    findBlock: () => null, // 見える所にかまどはない
    registry: md,
    inventory: { items: () => [] }
  }
  const state = { home: null, plan: null, unreachableDrops: new Set(), memory }
  const c = ground(bot, state, k, { dig: () => [], hunt: () => [] }, { leaves: [{ kind: 'smelt', item: 'iron_ingot', count: 0 }] }).candidates.find((c) => c.verb === 'smelt')
  assert.equal(c.id, 'take iron_ingot from the furnace at 200,70,0')
})

test('かまどに入れる物は、できる物で呼ぶ', () => {
  assert.equal(smeltingProduct('raw_iron'), 'iron_ingot')
  assert.equal(smeltingProduct('birch_log'), 'charcoal')
  assert.equal(smeltingProduct('beef'), 'cooked_beef')
  assert.equal(smeltingProduct('dirt'), null)
})
