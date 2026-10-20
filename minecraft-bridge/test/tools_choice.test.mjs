import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { chooseTool } from '../src/primitives.mjs'

const require = createRequire(import.meta.url)
const md = require('minecraft-data')('1.21.4')
const Block = require('prismarine-block')('1.21.4')
const Item = require('prismarine-item')('1.21.4')
const item = (name) => new Item(md.itemsByName[name].id, 1)
const block = (name) => Block.fromStateId(md.blocksByName[name].defaultState, 0)
const bot = (...names) => ({ entity: { effects: {} }, inventory: { items: () => names.map(item) } })
const pick = (b, ...names) => chooseTool(bot(...names), block(b))?.name ?? 'hand'

test('掘る道具: 要る道具か素手より速いものだけ（原木をツルハシで、土を土で掘っていた）', () => {
  assert.equal(pick('oak_log', 'dirt', 'iron_pickaxe'), 'hand')
  assert.equal(pick('oak_log', 'iron_pickaxe', 'stone_axe'), 'stone_axe')
  assert.equal(pick('dirt', 'iron_pickaxe'), 'hand')
  assert.equal(pick('dirt', 'iron_pickaxe', 'wooden_shovel'), 'wooden_shovel')
  assert.equal(pick('stone', 'wooden_pickaxe'), 'wooden_pickaxe')
  assert.equal(pick('stone', 'oak_planks'), 'hand') // 要る道具がない
})

test('壊れかけの道具は、要るときだけ使う', () => {
  const axe = item('stone_axe')
  axe.durabilityUsed = axe.maxDurability - 1
  const b = { entity: { effects: {} }, inventory: { items: () => [axe] } }
  assert.equal(chooseTool(b, block('oak_log')), null)
  const pickaxe = item('wooden_pickaxe')
  pickaxe.durabilityUsed = pickaxe.maxDurability - 1
  assert.equal(chooseTool({ entity: { effects: {} }, inventory: { items: () => [pickaxe] } }, block('stone'))?.name, 'wooden_pickaxe')
})

test('採るのに要る道具は、採れる一番安いものを使う（鉄のツルハシを石で減らさない。ダイヤの前に 2 度折れた）', () => {
  assert.equal(pick('stone', 'iron_pickaxe', 'stone_pickaxe'), 'stone_pickaxe')
  assert.equal(pick('iron_ore', 'iron_pickaxe', 'stone_pickaxe'), 'stone_pickaxe')
  assert.equal(pick('stone', 'diamond_pickaxe', 'iron_pickaxe'), 'iron_pickaxe')
  // 安い道具では採れないブロックだけ、高い道具を使う
  assert.equal(pick('diamond_ore', 'iron_pickaxe', 'stone_pickaxe'), 'iron_pickaxe')
  // 安い道具が壊れかけなら、高い道具に回す
  const worn = item('stone_pickaxe')
  worn.durabilityUsed = worn.maxDurability - 1
  const b = { entity: { effects: {} }, inventory: { items: () => [worn, item('iron_pickaxe')] } }
  assert.equal(chooseTool(b, block('stone'))?.name, 'iron_pickaxe')
})
