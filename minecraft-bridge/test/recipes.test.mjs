import { test } from 'node:test'
import assert from 'node:assert/strict'
import minecraftData from 'minecraft-data'
import { RecipeIndex } from '../src/recipes.mjs'
import { Knowledge } from '../src/knowledge.mjs'
import { solve } from '../src/solver.mjs'

const index = RecipeIndex.load()
const k = new Knowledge(minecraftData('1.21.4'), index)
const world = { inventory: {}, blocks: { oak_log: 10, stone: 10, cornflower: 3, iron_ore: 5, coal_ore: 3, sand: 5 }, mobs: { sheep: 3 }, table: null, unlocked: () => true }

test('バニラのレシピ全部を読み、タグは入れ子も展開する', () => {
  assert.ok(index.all.length > 1000)
  assert.ok(index.tagMembers('logs_that_burn').includes('oak_log')) // #logs_that_burn → #oak_logs → oak_log
  assert.equal(index.tagMembers('wool').length, 16)
})

test('検索: 結果か材料の名前で探せる', () => {
  const lantern = index.search('lantern')
  assert.deepEqual(lantern.find((r) => r.result === 'lantern').ingredients, ['8 iron_nugget', '1 torch'])
  assert.ok(index.search('blue bed').some((r) => r.result === 'blue_bed'))
  assert.ok(index.search('cornflower').some((r) => r.result === 'blue_dye')) // 材料の名前でも
})

test('計画: 主な物は全部作れる（不可能にしない）。染め物は白いものから', () => {
  for (const item of ['planks', 'stick', 'crafting_table', 'wooden_pickaxe', 'stone_pickaxe', 'furnace', 'chest', 'door', 'bed', 'torch', 'blue_bed', 'lantern', 'shield', 'glass']) {
    const r = solve(k, world, [{ spec: item, count: 1 }])
    assert.equal(r.impossible?.length ?? 0, 0, item)
  }
  assert.deepEqual(k.recipes('blue_wool'), [{ count: 1, ingredients: { blue_dye: 1, white_wool: 1 }, needsTable: false }])
  assert.ok(k.recipes('blue_bed').some((r) => r.ingredients.white_bed === 1))
  assert.equal(k.resolve('#planks').members.length, 12)
})
