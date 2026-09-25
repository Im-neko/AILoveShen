import { test } from 'node:test'
import assert from 'node:assert/strict'
import { equipment, timeOf } from '../src/observe.mjs'

test('着ている防具とオフハンドを報告し、空いている枠は null にする', () => {
  const slots = []
  slots[5] = { name: 'leather_helmet' }
  slots[45] = { name: 'shield' }
  assert.deepEqual(equipment({ inventory: { slots } }), {
    head: 'leather_helmet', chest: null, legs: null, feet: null, off_hand: 'shield'
  })
})

test('何日目かを報告する（寝て飛ばした夜も数える）。最初の時刻が届くまでは null', () => {
  assert.equal(timeOf({ time: { timeOfDay: 1000, day: 3 }, isRaining: false }).day, 3)
  assert.equal(timeOf({ time: { timeOfDay: null, day: null }, isRaining: false }).day, null)
})
