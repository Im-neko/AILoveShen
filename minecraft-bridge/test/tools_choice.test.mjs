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
