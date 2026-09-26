import { test } from 'node:test'
import assert from 'node:assert/strict'
import { relayCloses, relayOwnState, inventoryPacket } from '../src/mirror.mjs'

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

// 持ち物の Item（prismarine-item の代わり）: toNotch は名前だけを写す
const FakeItem = { toNotch: (i) => (i ? { itemCount: i.count, name: i.name } : { itemCount: 0 }) }

test('ボット自身の持ち替えと、クリックでの持ち物の移動を視聴側に送る（手に持つ物がずれていた）', async () => {
  const sent = []
  const slots = new Array(46).fill(null)
  const bot = {
    _client: { write: (name, params) => sent.push(['server', name, params]) },
    inventory: { slots }
  }
  const viewer = { write: (name, params) => sent.push(['viewer', name, params]) }
  relayOwnState(bot, new Set([viewer]), () => FakeItem, { delayMs: 5 })

  bot._client.write('held_item_slot', { slotId: 3 })
  bot._client.write('window_click', { windowId: 0, slot: 36 })
  bot._client.write('window_click', { windowId: 0, slot: 40 }) // まとめて 1 回
  slots[39] = { name: 'stone_pickaxe', count: 1 } // Mineflayer がクリックの結果を反映した
  await new Promise((resolve) => setTimeout(resolve, 20))

  const toViewer = sent.filter(([who]) => who === 'viewer')
  assert.deepEqual(toViewer[0], ['viewer', 'held_item_slot', { slot: 3 }])
  assert.equal(toViewer.length, 2)
  const [, name, pkt] = toViewer[1]
  assert.equal(name, 'window_items')
  assert.equal(pkt.windowId, 0)
  assert.deepEqual(pkt.items[39], { itemCount: 1, name: 'stone_pickaxe' })
  assert.equal(sent.filter(([who]) => who === 'server').length, 3) // サーバーへはそのまま
})

test('持ち物の window_items は 46 マスのプレイヤーの持ち物', () => {
  const bot = { inventory: { slots: new Array(46).fill(null) } }
  const pkt = inventoryPacket(bot, FakeItem)
  assert.equal(pkt.items.length, 46)
  assert.deepEqual(pkt.carriedItem, { itemCount: 0 })
})
