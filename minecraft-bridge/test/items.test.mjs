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

test('find_foods: 持っている物、今作れる・焼ける物が先。取り方と、悪い効果の注意', async () => {
  const { foodsInfo } = await import('../src/items.mjs')
  const r = foodsInfo(deps, { wheat: 3, beef: 1, cooked_beef: 2 })
  const by = Object.fromEntries(r.foods.map((f) => [f.name, f]))
  assert.equal(r.foods[0].name, 'cooked_beef') // 持っている物が先（満腹度の大きい順）
  assert.equal(by.cooked_beef.carried, 2)
  assert.equal(by.bread.can_craft_now, true) // 小麦 3
  assert.match(by.bread.how[0], /^craft 3 wheat/)
  assert.equal(by.beef.can_smelt_now, undefined) // 燃料は見ない（かまどで焼ける印は材料だけ）
  assert.ok(r.foods.findIndex((f) => f.name === 'bread') < r.foods.findIndex((f) => f.name === 'golden_carrot'))
  const flesh = foodsInfo(deps, {}, { query: 'rotten' }).foods[0]
  assert.match(flesh.caution, /starving/)
  const mixed = foodsInfo(deps, { rotten_flesh: 5, apple: 1 }).foods
  assert.deepEqual(mixed.slice(0, 2).map((f) => f.name), ['apple', mixed[1].name]) // 悪い効果の物を持っていても、先には出さない
  assert.notEqual(mixed[1].name, 'rotten_flesh')
  assert.equal(foodsInfo(deps, {}, { query: 'wheat', limit: 30 }).foods.some((f) => f.name === 'wheat'), false) // 小麦は食べられない
})

test('find_foods: 検索語は名前・日本語・材料に含まれる語。have は今の持ち物で食べる・作れる物だけ', async () => {
  const { foodsInfo } = await import('../src/items.mjs')
  const bread = foodsInfo(deps, {}, { query: 'パン' }).foods.map((f) => f.name)
  assert.ok(bread.includes('bread'))
  const fromWheat = foodsInfo(deps, {}, { query: 'wheat' }).foods.map((f) => f.name)
  assert.ok(fromWheat.includes('bread') && fromWheat.includes('cookie'))
  assert.deepEqual(foodsInfo(deps, {}, { have: true }).foods, [])
  const now = foodsInfo(deps, { wheat: 3, apple: 1 }, { have: true }).foods.map((f) => f.name)
  assert.ok(now.includes('bread') && now.includes('apple') && !now.includes('cookie'))
})
