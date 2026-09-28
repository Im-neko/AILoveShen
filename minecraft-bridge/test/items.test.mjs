import { test } from 'node:test'
import assert from 'node:assert/strict'
import minecraftData from 'minecraft-data'
import { RecipeIndex } from '../src/recipes.mjs'
import { Knowledge, useRecipes } from '../src/knowledge.mjs'
import { loadNames, itemList, itemInfo, usesOf } from '../src/items.mjs'

const md = minecraftData('1.21.4')
const index = RecipeIndex.load('1.21.4')
useRecipes(index)
const knowledge = new Knowledge(md, index)
const names = loadNames('1.21.4')
const deps = { md, knowledge, index, names }

test('一覧: アイテムの名前と日本語名（ゲームの ja_jp から）', () => {
  const list = itemList(md, names)
  assert.ok(list.length > 1300)
  assert.equal(list.find((i) => i.name === 'iron_ingot').ja, '鉄インゴット')
  assert.ok(list.find((i) => i.name === 'carrots'), '作物のブロックも引ける')
})

test('item_info: 焼く・掘る（要る道具）・モブ・置ける・食べられる・何に使うか', () => {
  const iron = itemInfo('iron_ingot', deps)
  assert.ok(iron.how_to_get.smelt_from.includes('raw_iron'))
  assert.ok(iron.how_to_get.craft.some((r) => r.ingredients.some((i) => i.includes('iron_nugget'))))
  assert.match(iron.used_in, /recipes/)

  const raw = itemInfo('raw_iron', deps)
  const ore = raw.how_to_get.dig.find((d) => d.block === 'iron_ore')
  assert.equal(ore.ja, '鉄鉱石')
  assert.equal(ore.tool, 'stone_pickaxe')

  const wool = itemInfo('white_wool', deps)
  assert.ok(wool.how_to_get.mob_drop.some((m) => m.mob === 'sheep' && m.huntable))
  assert.equal(wool.block.placeable, true)

  assert.equal(itemInfo('bread', deps).food.hunger, 5)
  assert.match(itemInfo('wheat_seeds', deps).how_to_get.farm, /farmed\(wheat/)
  assert.match(itemInfo('oak_sapling', deps).how_to_get.plant, /planted/)
  assert.equal(itemInfo('no_such_thing', deps), null)
})

test('uses_of: 素材を使うレシピ、今の持ち物で作れるものが先、have なら作れるものだけ', () => {
  const all = usesOf(index, 'stick', { stick: 2, cobblestone: 3 })
  assert.ok(all.total > 20)
  assert.equal(all.recipes.length, 20)
  assert.equal(all.recipes[0].can_make_now, true)
  const now = usesOf(index, 'stick', { stick: 2, cobblestone: 3 }, true)
  assert.ok(now.recipes.every((r) => r.can_make_now))
  assert.ok(now.recipes.some((r) => r.result === 'stone_pickaxe'))
  assert.equal(new Set(all.recipes.map((r) => r.result)).size, all.recipes.length, '同じ物は 1 つだけ')
})
