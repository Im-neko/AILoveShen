import { test } from 'node:test'
import assert from 'node:assert/strict'
import minecraftData from 'minecraft-data'
import { Knowledge } from '../src/knowledge.mjs'
import { solve } from '../src/solver.mjs'

const k = new Knowledge(minecraftData('1.21.4'))
const world = ({ inventory = {}, blocks = {}, mobs = {}, table = null, unlocked = () => true } = {}) =>
  ({ inventory, blocks, mobs, table, unlocked })
const kinds = (r) => r.leaves.map((l) => `${l.kind}:${l.item}`)

test('何もない状態から板材: まず原木を掘る', () => {
  const r = solve(k, world({ blocks: { oak_log: 5 } }), [{ spec: 'planks', count: 4 }])
  assert.equal(r.met, false)
  assert.deepEqual(kinds(r), ['dig:oak_log'])
  assert.equal(r.remaining, 2)
})

test('原木 1 本から板材: クラフトする', () => {
  const r = solve(k, world({ inventory: { oak_log: 1 } }), [{ spec: 'planks', count: 4 }])
  assert.deepEqual(kinds(r), ['craft:oak_planks'])
})

test('持っている物で必要な分を満たす', () => {
  const r = solve(k, world({ inventory: { birch_planks: 2, oak_planks: 3 } }), [{ spec: 'planks', count: 5 }])
  assert.equal(r.met, true)
  assert.equal(r.remaining, 0)
  assert.deepEqual(r.leaves, [])
})

test('原木のまま要る分を先に取り分け、残りから板材を作る', () => {
  // 原木の柱 12 本と板材 20 枚の家で原木を 13 本持っている: 板材にしてよいのは 1 本だけ
  const r = solve(k, world({ inventory: { oak_log: 13 }, blocks: { oak_log: 9 } }),
    [{ spec: 'log', count: 12 }, { spec: 'planks', count: 20 }])
  assert.equal(r.nodes[0].have, 12)
  const planks = r.nodes[1]
  const craft = planks.leaf
  assert.equal(craft.item, 'oak_planks')
  assert.equal(craft.times, 5)
  assert.equal(planks.children[0].have, 1) // 余った原木 1 本
  assert.deepEqual(kinds(r), ['dig:oak_log'])
})

test('3x3 のレシピには作業台が要る: 持っている作業台を置く', () => {
  const r = solve(k, world({ inventory: { oak_planks: 2, stick: 1, crafting_table: 1 } }), [{ spec: 'wooden_sword', count: 1 }])
  assert.deepEqual(kinds(r), ['place:crafting_table'])
})

test('近くに作業台があれば 3x3 のレシピはすぐ作れる', () => {
  const r = solve(k, world({ inventory: { oak_planks: 2, stick: 1 }, table: 'near' }), [{ spec: 'wooden_sword', count: 1 }])
  assert.deepEqual(kinds(r), ['craft:wooden_sword'])
})

test('作業台がなければ板材から作る', () => {
  const r = solve(k, world({ inventory: { oak_planks: 6, stick: 1 } }), [{ spec: 'wooden_sword', count: 1 }])
  assert.deepEqual(kinds(r), ['craft:crafting_table'])
})

test('まだ解放されていないレシピは候補に出さず、報告する', () => {
  const r = solve(k, world({ inventory: { oak_planks: 2, stick: 1 }, table: 'near', unlocked: (i) => i !== 'wooden_sword' }),
    [{ spec: 'wooden_sword', count: 1 }])
  assert.deepEqual(r.leaves, [])
  assert.match(r.blocked[0], /wooden_sword is not unlocked/)
})

test('どの色のベッドでもよい: 持っている羊毛の色でレシピが決まる', () => {
  const r = solve(k, world({ inventory: { red_wool: 3, oak_planks: 3 }, table: 'reach' }), [{ spec: 'bed', count: 1 }])
  assert.deepEqual(kinds(r), ['craft:red_bed'])
})

test('羊毛は羊から取る（ドロップのデータに無い）', () => {
  const r = solve(k, world({ inventory: { oak_planks: 3 }, mobs: { sheep: 2 }, table: 'near' }), [{ spec: 'bed', count: 1 }])
  assert.deepEqual(kinds(r), ['kill:white_wool'])
})

test('染色のレシピは使わない（ベッドから別のベッドを作らない）', () => {
  const recipes = k.recipes('red_bed')
  assert.ok(recipes.length > 0)
  assert.ok(recipes.every((r) => !Object.keys(r.ingredients).some((n) => n.endsWith('_bed'))))
  // 色つきの羊毛は白い羊毛を染める（minecraft-data の黒い羊毛からのレシピは使わない）
  assert.deepEqual(k.recipes('red_wool'), [{ count: 1, ingredients: { white_wool: 1, red_dye: 1 }, needsTable: false }])
})

test('入手元が見えなければ探索になり、blocked として報告する', () => {
  const r = solve(k, world(), [{ spec: 'planks', count: 4 }])
  assert.equal(r.leaves[0].kind, 'explore')
  assert.match(r.blocked[0], /nearby/)
})

test('ドロップ: シルクタッチでのドロップ、葉、敵対モブは入手元にしない', () => {
  assert.equal(k.blockSources.get('glass'), undefined)
  assert.ok(!(k.blockSources.get('apple') ?? []).some((b) => b.endsWith('_leaves')))
  assert.ok(!(k.blockSources.get('stick') ?? []).some((b) => b.endsWith('_leaves')))
  assert.equal(k.mobSources.get('rotten_flesh'), undefined)
  assert.deepEqual(k.blockSources.get('cobblestone'), ['stone'])
  assert.ok(!k.resolve('food').members.includes('rotten_flesh'))
})

test('食料が見えないとき: 前に見た動物の所へは、探し回らずに戻る', () => {
  const w = { ...world(), remembered: new Set(['cow']) }
  const r = solve(k, w, [{ spec: 'food', count: 4 }])
  const explore = r.leaves.find((l) => l.kind === 'explore')
  assert.deepEqual(explore.sources, ['cow'])
  assert.match(explore.reason, /seen before/)
})

test('食料: 見えている動物を狩る', () => {
  const r = solve(k, world({ mobs: { cow: 1 } }), [{ spec: 'food', count: 4 }])
  assert.deepEqual(kinds(r), ['kill:beef'])
})

test('道具が要るブロックは、先に道具を求める', () => {
  const r = solve(k, world({ inventory: { oak_planks: 20, stick: 4 }, blocks: { stone: 10 }, table: 'near' }),
    [{ spec: 'cobblestone', count: 3 }])
  assert.deepEqual(kinds(r), ['craft:wooden_pickaxe'])
})

test('知らないアイテムは受け付けない', () => {
  assert.throws(() => k.resolve('unobtainium'), /unknown item/)
})

test('青いベッド: 青い羊毛 3 つ（白い羊毛＋ヤグルマギクの青の染料）と板材から作れる', () => {
  const r = solve(k, world({ blocks: { cornflower: 3, oak_log: 5 }, mobs: { sheep: 3 }, inventory: { oak_planks: 3 } }), [{ spec: 'blue_bed', count: 1 }])
  assert.equal(r.impossible?.length ?? 0, 0)
  const ks = kinds(r)
  assert.ok(ks.some((x) => x.startsWith('dig:cornflower')) || ks.some((x) => x.startsWith('kill:')), ks.join(', '))
})
