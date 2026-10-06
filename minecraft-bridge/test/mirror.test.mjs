import { test } from 'node:test'
import assert from 'node:assert/strict'
import { relayWindows, relayOwnState, inventoryPacket } from '../src/mirror.mjs'

// 開いた画面の視聴者側（設計書 53）: ボットの操作は数十ミリ秒で終わるので、閉じるのを遅らせ、クリックの結果を送る
const FakeItem = { toNotch: (i) => (i ? { itemCount: i.count, name: i.name } : { itemCount: 0 }) }
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

function windowRig (options = {}) {
  const sent = []
  const bot = {
    _client: { write: (name, params) => sent.push(['server', name, params]) },
    currentWindow: null
  }
  const viewer = {
    write: (name, params) => sent.push(['viewer', name, params]),
    writeRaw: (buf) => sent.push(['viewer', 'raw', buf])
  }
  const viewers = new Set([viewer])
  const windows = relayWindows(bot, viewers, () => FakeItem, { holdMs: 60, delayMs: 5, ...options })
  const toViewer = () => sent.filter(([who]) => who === 'viewer').map(([, name, params]) => [name, params])
  return { sent, bot, viewer, viewers, windows, toViewer }
}
const chest = (id, slots) => ({ id, slots })

test('ボットが画面を閉じても、視聴側ではしばらく開けておく（一瞬で消えていた）', async () => {
  const { bot, windows, toViewer } = windowRig()
  windows.fromServer('open_window', { windowId: 2 }, Buffer.from('open'))
  bot._client.write('close_window', { windowId: 2 })

  assert.deepEqual(toViewer().filter(([n]) => n === 'close_window'), [])
  await sleep(120)
  assert.deepEqual(toViewer().filter(([n]) => n === 'close_window'), [['close_window', { windowId: 2 }]])
  assert.deepEqual(windows.open(), [])
})

test('ボットのクリックの後、視聴側の画面の今の中身を送る（取り出しても中身が変わらなかった）', async () => {
  const { bot, sent, windows, toViewer } = windowRig({ holdMs: 500 })
  windows.fromServer('open_window', { windowId: 2 }, Buffer.from('open'))
  bot.currentWindow = chest(2, [{ name: 'oak_log', count: 3 }, null])
  bot._client.write('window_click', { windowId: 2, slot: 0 })
  bot._client.write('window_click', { windowId: 2, slot: 1 }) // まとめて 1 回
  bot.currentWindow.slots[0] = null // Mineflayer がクリックの結果を反映した
  await sleep(30)

  const items = toViewer().filter(([n]) => n === 'window_items')
  assert.equal(items.length, 1)
  assert.equal(items[0][1].windowId, 2)
  assert.deepEqual(items[0][1].items, [{ itemCount: 0 }, { itemCount: 0 }])
  assert.equal(sent.filter(([who]) => who === 'server').length, 2) // サーバーへはそのまま
  windows.dispose()
})

test('閉じる前の最後の中身を送り、閉じるのは最後の変化から保持時間のあと', async () => {
  const { bot, windows, toViewer } = windowRig({ holdMs: 80 })
  windows.fromServer('open_window', { windowId: 2 }, Buffer.from('open'))
  bot.currentWindow = chest(2, [{ name: 'coal', count: 1 }])
  bot._client.write('window_click', { windowId: 2, slot: 0 })
  bot._client.write('close_window', { windowId: 2 }) // クリックの同期を待たずに閉じる
  const names = () => toViewer().map(([n]) => n)
  assert.deepEqual(names(), ['window_items']) // 閉じる前に中身を送った
  await sleep(40)
  assert.ok(!names().includes('close_window'))
  await sleep(80)
  assert.equal(names().at(-1), 'close_window')
})

test('保持時間を過ぎた画面は、閉じたらすぐ視聴側でも閉じる', async () => {
  let t = 0
  const { bot, windows, toViewer } = windowRig({ holdMs: 100, now: () => t })
  windows.fromServer('open_window', { windowId: 1 }, Buffer.from('open'))
  t = 500
  bot._client.write('close_window', { windowId: 1 })
  assert.deepEqual(toViewer().filter(([n]) => n === 'close_window'), [['close_window', { windowId: 1 }]])
})

test('次の画面が開いたら、閉じるのを待っていた画面は捨てる', async () => {
  const { bot, windows, toViewer } = windowRig({ holdMs: 50 })
  windows.fromServer('open_window', { windowId: 1 }, Buffer.from('a'))
  bot._client.write('close_window', { windowId: 1 })
  windows.fromServer('open_window', { windowId: 2 }, Buffer.from('b'))
  await sleep(100)
  assert.deepEqual(toViewer().filter(([n]) => n === 'close_window'), [])
  assert.deepEqual(windows.open(), [2])
  windows.dispose()
})

test('サーバーが閉じた画面も同じに待つ（そのまま中継しない）。持ち物（0）の閉じるは即', async () => {
  const { bot, windows, toViewer } = windowRig({ holdMs: 30 })
  windows.fromServer('open_window', { windowId: 4 }, Buffer.from('a'))
  assert.equal(windows.fromServer('close_window', { windowId: 4 }, Buffer.from('c')), true)
  assert.equal(windows.fromServer('block_change', {}, Buffer.from('x')), false)
  assert.deepEqual(toViewer().filter(([n]) => n === 'close_window'), [])
  await sleep(60)
  assert.deepEqual(toViewer().filter(([n]) => n === 'close_window'), [['close_window', { windowId: 4 }]])

  bot._client.write('close_window', { windowId: 0 })
  assert.deepEqual(toViewer().at(-1), ['close_window', { windowId: 0 }])
})

test('あとから参加した視聴者にも、開いている画面を見せる', () => {
  const { bot, windows } = windowRig({ holdMs: 500 })
  windows.fromServer('open_window', { windowId: 2 }, Buffer.from('open'))
  bot.currentWindow = chest(2, [{ name: 'coal', count: 1 }])
  bot._client.write('window_click', { windowId: 2, slot: 0 })
  const late = []
  windows.replay({ writeRaw: (b) => late.push(['raw', b.toString()]), write: (n, p) => late.push([n, p]) })
  assert.deepEqual(late[0], ['raw', 'open'])
  assert.equal(late[1][0], 'window_items')
  windows.dispose()
})


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

test('装備（set_equipment）は、サーバーから届いたのと同じバイト列で視聴者へ送る（nmp の読み取りが先頭ビットを消す）', async () => {
  const { encodeEquipment } = await import('../src/mirror.mjs')
  const { createRequire } = await import('node:module')
  const mc = createRequire(import.meta.url)('minecraft-protocol')
  // 実際に切れたパケット（logs/bridge.log 2026-10-03 07:56）の、サーバーが送った形: 手 2 つ、1 つ目の slot は 0x80（続きがある）
  const wire = Buffer.from('60a3a901' + '8001d8090000' + '0101d9090000', 'hex')
  const deserializer = mc.createDeserializer({ state: 'play', isServer: false, version: '1.21.4' })
  const received = Buffer.from(wire)
  const parsed = deserializer.parsePacketBuffer(received) // nmp は received の先頭ビットを消す
  assert.equal(received[4], 0x00, '前提: nmp が読んだバッファの先頭ビットを消す')
  const sent = encodeEquipment(parsed.data.params)
  assert.equal(sent.toString('hex'), wire.toString('hex'))
  const back = deserializer.parsePacketBuffer(Buffer.from(sent))
  assert.equal(back.metadata.size, sent.length)
  assert.deepEqual(back.data.params.equipments.map((e) => [e.slot, e.item.itemId]), [[0, 1240], [1, 1241]])
})
