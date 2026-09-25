import { test } from 'node:test'
import assert from 'node:assert/strict'
import { equipment } from '../src/observe.mjs'

test('着ている防具とオフハンドを報告し、空いている枠は null にする', () => {
  const slots = []
  slots[5] = { name: 'leather_helmet' }
  slots[45] = { name: 'shield' }
  assert.deepEqual(equipment({ inventory: { slots } }), {
    head: 'leather_helmet', chest: null, legs: null, feet: null, off_hand: 'shield'
  })
})
