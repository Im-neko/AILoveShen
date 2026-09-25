import { test } from 'node:test'
import assert from 'node:assert/strict'
import { relayCloses } from '../src/mirror.mjs'

test('ボットが閉じた画面は視聴側でも閉じる（配信で開いたままになっていた）', () => {
  const sent = []
  const client = { write: (name, params) => sent.push(['server', name, params]) }
  const viewer = { write: (name, params) => sent.push(['viewer', name, params]) }
  relayCloses(client, new Set([viewer]))

  client.write('close_window', { windowId: 3 })
  client.write('arm_animation', { hand: 0 })

  assert.deepEqual(sent, [
    ['server', 'close_window', { windowId: 3 }],
    ['viewer', 'close_window', { windowId: 3 }],
    ['server', 'arm_animation', { hand: 0 }]
  ])
})
