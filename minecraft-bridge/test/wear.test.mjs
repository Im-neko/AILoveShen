import { test } from 'node:test'
import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import { isBreak, watchWear, recentBreaks, toReplace, brokeSince } from '../src/wear.mjs'

const pick = (used) => ({ name: 'stone_pickaxe', maxDurability: 131, durabilityUsed: used })

test('使い切った道具が消えたら壊れた。チェストへ移した・持ち替えたのは違う', () => {
  assert.equal(isBreak(pick(130), null), true)
  assert.equal(isBreak(pick(10), null), false)
  assert.equal(isBreak(pick(130), pick(130)), false)
  assert.equal(isBreak({ name: 'dirt' }, null), false)
})

test('壊れたらすぐ記録し、同じ種類を持つまで作り直す対象にする', () => {
  const inventory = new EventEmitter()
  let items = []
  inventory.items = () => items
  const bot = { inventory }
  const state = {}
  const start = Date.now()
  watchWear(bot, state)
  inventory.emit('updateSlot', 36, pick(130), null)
  assert.deepEqual(toReplace(bot, state), [{ item: 'stone_pickaxe', kind: 'pickaxe' }])
  assert.equal(recentBreaks(bot, state)[0].replaced, false)
  assert.match(brokeSince(state, start), /the stone_pickaxe broke/)
  items = [{ name: 'wooden_pickaxe', count: 1 }]
  assert.deepEqual(toReplace(bot, state), [])
  assert.equal(recentBreaks(bot, state)[0].replaced, true)
})
