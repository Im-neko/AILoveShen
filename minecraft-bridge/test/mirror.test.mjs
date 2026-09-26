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

test('作り直して送るアイテムは数だけの部品に絞る（視聴者が「bytes extra」で切れていた）', async () => {
  const { createRequire } = await import('node:module')
  const require = createRequire(import.meta.url)
  const mc = require('minecraft-protocol')
  const Item = require('prismarine-item')('1.21.4')
  const reg = require('prismarine-registry')('1.21.4')
  const sword = new Item(reg.itemsByName.iron_sword.id, 1)
  sword.components = [
    { type: 'damage', data: 7 },
    { type: 'custom_name', data: { type: 'string', value: 'x' } }
  ]
  sword.removedComponents = ['food']
  const slots = new Array(46).fill(null)
  slots[36] = sword
  const pkt = inventoryPacket({ inventory: { slots } }, Item)

  assert.deepEqual(pkt.items[36].components, [{ type: 'damage', data: 7 }])
  assert.equal(pkt.items[36].addedComponentCount, 1)
  assert.equal(pkt.items[36].removedComponentCount, 0)
  // 1.21.4 の形で書けて、読み直すと全部読まれる
  const buf = mc.createSerializer({ state: 'play', isServer: true, version: '1.21.4' })
    .createPacketBuffer({ name: 'window_items', params: pkt })
  const back = mc.createDeserializer({ state: 'play', isServer: false, version: '1.21.4' }).parsePacketBuffer(buf)
  assert.equal(back.metadata.size, buf.length)
  assert.equal(back.data.params.items[36].components[0].data, 7)
})

test('部品つきの装備（set_equipment）は数だけの部品に絞って送る（視聴者が「74 bytes extra」で切れた）', async () => {
  const { sanitizedEquipment } = await import('../src/mirror.mjs')
  const plain = { entityId: 5, equipments: [{ slot: 0, item: { itemCount: 1, itemId: 866, addedComponentCount: 0, removedComponentCount: 0, components: [], removeComponents: [] } }] }
  assert.equal(sanitizedEquipment(plain), null) // そのまま中継
  const fancy = {
    entityId: 5,
    equipments: [{
      slot: 0,
      item: {
        itemCount: 1,
        itemId: 866,
        addedComponentCount: 2,
        removedComponentCount: 0,
        components: [{ type: 'damage', data: 3 }, { type: 'custom_name', data: { type: 'string', value: 'x' } }],
        removeComponents: []
      }
    }]
  }
  const safe = sanitizedEquipment(fancy)
  assert.deepEqual(safe.equipments[0].item.components, [{ type: 'damage', data: 3 }])
  const { createRequire } = await import('node:module')
  const mc = createRequire(import.meta.url)('minecraft-protocol')
  const buf = mc.createSerializer({ state: 'play', isServer: true, version: '1.21.4' }).createPacketBuffer({ name: 'entity_equipment', params: safe })
  const back = mc.createDeserializer({ state: 'play', isServer: false, version: '1.21.4' }).parsePacketBuffer(buf)
  assert.equal(back.metadata.size, buf.length)
})
