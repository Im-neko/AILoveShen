import { test } from 'node:test'
import assert from 'node:assert/strict'
import minecraftData from 'minecraft-data'
import { Knowledge, useRecipes } from '../src/knowledge.mjs'
import { RecipeIndex } from '../src/recipes.mjs'
import { materialsOf } from '../src/goals.mjs'

const index = RecipeIndex.load('1.21.4')
useRecipes(index)
const k = new Knowledge(minecraftData('1.21.4'), index)

// 家があり、埋まった石の階段掘りも採掘場も使える世界（本番の snapshot の形）
const rich = {
  inventory: { cobblestone: 2 }, blocks: {}, mobs: {}, table: null, unlocked: () => true, dig: () => [], hunt: () => [],
  home: { name: 'home' }, buried: () => [{ depth: 5 }], digDepth: 12, minePlan: () => ({ make: 10 }), mine: null, mines: []
}

test('materials: 家のある世界でも、石の道具の材料（丸石・棒）が木に出る（地下の輪で「不可能」になって空にならない）', () => {
  for (const item of ['stone_hoe', 'stone_pickaxe']) {
    const { all } = materialsOf(k, rich, item)
    assert.ok(all.includes('cobblestone'), `${item}: ${all}`)
    assert.ok(all.includes('stick'), `${item}: ${all}`)
  }
})
