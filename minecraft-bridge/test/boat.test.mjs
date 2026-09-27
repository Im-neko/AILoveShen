import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { waterCrossing, surfaceAt, drive, boxFree, BOAT, BOAT_MIN_WATER } from '../src/boat.mjs'
import { createTools } from '../src/tools.mjs'
import { relayOwnState } from '../src/mirror.mjs'
import { Knowledge } from '../src/knowledge.mjs'
import { TRAVELS } from '../src/primitives.mjs'

const { Vec3 } = vec3Pkg
const v = (x, y, z) => new Vec3(x, y, z)
const md = minecraftData('1.21.4')

// 水面 y 62。x < 10 と x >= shore は y 64 までの陸（草が生えている）、その間は水。island があれば x 24..26 の
// |z| <= 1 が陸
function world ({ island = false, shore = 40 } = {}) {
  return (p) => {
    const land = p.x < 10 || p.x >= shore || (island && p.x >= 24 && p.x <= 26 && Math.abs(p.z) <= 1)
    if (land) {
      if (p.y <= 63) return { name: 'dirt', boundingBox: 'block', position: p }
      if (p.y === 64) return { name: 'short_grass', boundingBox: 'empty', position: p }
      return { name: 'air', boundingBox: 'empty', position: p }
    }
    if (p.y <= 62) return { name: p.y === 62 ? 'water' : 'sand', boundingBox: p.y === 62 ? 'empty' : 'block', position: p }
    return { name: 'air', boundingBox: 'empty', position: p }
  }
}

const fakeBot = (at, opts) => ({ entity: { position: at.clone(), yaw: 0 }, blockAt: world(opts) })

test('まっすぐな道のりに長い水があれば、その始まりと長さと向こう岸がわかる', () => {
  const bot = fakeBot(v(5.5, 64, 0.5))
  const c = waterCrossing(bot, v(60, 64, 0))
  assert.equal(c.from, 5)
  assert.equal(c.length, 30)
  assert.equal(c.landAfter, true)
  assert.deepEqual([c.entry.x, c.entry.y, c.entry.z], [10, 62, 0])
  // 草は地面に数えない（その下の土がいちばん上）
  assert.deepEqual(surfaceAt(bot, 5, 0, 64), { y: 63, name: 'dirt', water: false })
})

test('短い水は自動では渡らない（道具の ride_boat は短い水でも渡る）', () => {
  const bot = fakeBot(v(5.5, 64, 0.5), { island: true })
  // 島で水が 14m と 13m に分かれる: 12m 以上なので最初の水から
  assert.equal(waterCrossing(bot, v(60, 64, 0)).length, 14)
  const narrow = fakeBot(v(5.5, 64, 0.5), { shore: 14 })
  assert.equal(waterCrossing(narrow, v(30, 64, 0)), null)
  assert.ok(BOAT_MIN_WATER > 4)
  assert.equal(waterCrossing(narrow, v(30, 64, 0), undefined, undefined, 3).length, 4)
})

function rider (at, opts) {
  const sent = []
  const listeners = {}
  const boat = { id: 7, name: 'oak_boat', position: at.clone() }
  const bot = {
    ...fakeBot(at, opts),
    vehicle: boat,
    _client: {
      write: (name, params) => sent.push({ name, params }),
      on: (e, f) => { listeners[e] = f },
      off: (e) => { delete listeners[e] }
    }
  }
  return { bot, boat, sent }
}

test('ボートは自分で vehicle_move を送って進み、向こう岸に着いたら止まる', async () => {
  const { bot, boat, sent } = rider(v(12.5, 62.52, 0.5))
  const r = await drive(bot, boat, v(60, 64, 0.5), new AbortController().signal)
  assert.equal(r.landed, true)
  assert.equal(r.why, 'reached the shore')
  const moves = sent.filter((s) => s.name === 'vehicle_move').map((s) => s.params)
  assert.ok(moves.length > 50)
  // 1 ティックに 0.35 ブロックまで（サーバーが受け入れる速さ）、高さは水面のまま
  for (let i = 1; i < moves.length; i++) assert.ok(Math.hypot(moves[i].x - moves[i - 1].x, moves[i].z - moves[i - 1].z) <= 0.351)
  assert.ok(moves.every((m) => Math.abs(m.y - 62.52) < 1e-9))
  const last = moves.at(-1)
  assert.ok(last.x > 36 && last.x < 40, `stopped at ${last.x}`)
  // 自分の位置もボートと一緒に動く（Mineflayer は乗っている間に動かさない）
  assert.ok(Math.abs(bot.entity.position.x - last.x) < 1e-9)
})

test('前の陸の向こうにまた水があれば島: よけて進み、向こう岸まで行く', async () => {
  const { bot, boat, sent } = rider(v(12.5, 62.52, 0.5), { island: true })
  const r = await drive(bot, boat, v(60, 64, 0.5), new AbortController().signal)
  assert.equal(r.why, 'reached the shore')
  const moves = sent.filter((s) => s.name === 'vehicle_move').map((s) => s.params)
  assert.ok(moves.some((m) => Math.abs(m.z - 0.5) > 1.5), 'turned aside')
  assert.ok(moves.at(-1).x > 36)
})

test('斜めに進むときも、ボートの箱（1.375 の正方形）の角を岸のブロックに当てない', async () => {
  // 実測（2026-09-27）: 西南西へ進むボートの箱の北西の角が、水面の高さの草ブロック (20, 62, -179) に
  // かかり、サーバーが毎ティック位置を戻した
  const water = (p) => p.y === 62 ? { name: 'water', boundingBox: 'empty' } : p.y < 62 ? { name: 'sand', boundingBox: 'block' } : { name: 'air', boundingBox: 'empty' }
  const blockAt = (p) => (p.x === 20 && p.y === 62 && p.z === -179 ? { name: 'grass_block', boundingBox: 'block' } : water(p))
  const { bot, boat, sent } = rider(v(21.8, 62.889, -177.51))
  bot.blockAt = blockAt
  const controller = new AbortController()
  setTimeout(() => controller.abort(new Error('enough')), 300)
  await drive(bot, boat, v(-76, 65, -151), controller.signal)
  const moves = sent.filter((s) => s.name === 'vehicle_move').map((s) => s.params)
  assert.ok(moves.length > 3)
  for (const m of moves) assert.equal(boxFree(bot, m.x, m.y, m.z), true, `the box hits the shore at ${m.x},${m.z}`)
  // 前のやり方（進む向きに垂直な線の上だけ見る）では当たっていた一歩目
  assert.equal(boxFree(bot, 21.8 - 0.338, 62.889, -177.51 + 0.092), false)
})

test('サーバーに戻され続けたら、漕ぎ続けずに理由を付けて止まる', async () => {
  const { bot, boat } = rider(v(12.5, 62.52, 0.5))
  const listeners = {}
  bot._client.on = (e, f) => { listeners[e] = f }
  bot._client.off = (e) => { delete listeners[e] }
  bot._client.write = (name) => { if (name === 'vehicle_move') listeners.vehicle_move?.({ x: 12.5, y: 62.52, z: 0.5 }) }
  const r = await drive(bot, boat, v(60, 64, 0.5), new AbortController().signal)
  assert.equal(r.landed, false)
  assert.match(r.why, /^the server kept pushing the boat back at 12.5,0.5/)
})

test('止める合図が来たら、乗ったまま止まる（続きは次の行動が漕ぐ）', async () => {
  const { bot, boat } = rider(v(12.5, 62.52, 0.5))
  const controller = new AbortController()
  setTimeout(() => controller.abort(new Error('interrupted')), 120)
  const r = await drive(bot, boat, v(60, 64, 0.5), controller.signal)
  assert.equal(r.landed, false)
  assert.match(r.why, /^stopped: interrupted/)
})

function toolsWith (items, extra = {}) {
  const bot = {
    entity: { position: v(5.5, 64, 0.5) },
    entities: { 3: { id: 3, name: 'cow', type: 'passive', position: v(7, 64, 0), height: 1.4 } },
    registry: md,
    time: { timeOfDay: 1000 },
    blockAt: (p) => extra[`${p.x},${p.y},${p.z}`] ?? world()(p),
    inventory: { items: () => items.map(([name, count]) => ({ name, count })) }
  }
  const runs = []
  const callTool = createTools({
    bot,
    state: { home: null, formerHomes: [], builds: {}, memory: {}, busy: false, reflex: false },
    knowledge: new Knowledge(md),
    run: async (c) => { runs.push(c); return { ok: true, result: 'done', seconds: 0 } },
    candidates: () => [],
    check: () => []
  })
  return { callTool, runs }
}

test('use_item: 火を起こす物・爆発する物は使わず、ボート・食べ物・チェストは専用の道具へ', async () => {
  const { callTool, runs } = toolsWith([['flint_and_steel', 1], ['oak_boat', 1], ['bread', 2], ['wheat', 3], ['bone_meal', 1]],
    { '6,64,0': { name: 'chest', boundingBox: 'block' } })
  assert.match((await callTool('use_item', { item: 'flint_and_steel', x: 6, y: 63, z: 0 })).result, /starts fires/)
  assert.match((await callTool('use_item', { item: 'oak_boat' })).result, /ride_boat/)
  assert.match((await callTool('use_item', { item: 'bread' })).result, /use eat/)
  assert.match((await callTool('use_item', { item: 'bone_meal', x: 6, y: 64, z: 0 })).result, /chest has its own tools/)
  assert.match((await callTool('use_item', { item: 'lava_bucket' })).result, /you have no lava_bucket/)
  assert.equal(runs.length, 0)
  // モブに（餌やり）、ブロックに、向いている方へ
  assert.equal((await callTool('use_item', { item: 'wheat', entity: 3 })).ok, true)
  assert.equal((await callTool('use_item', { item: 'bone_meal', x: 6, y: 63, z: 0, hold: 9 })).ok, true)
  assert.deepEqual(runs.map((c) => [c.verb, c.entityId ?? null, c.pos ? `${c.pos.x},${c.pos.y},${c.pos.z}` : null, c.hold]),
    [['use_item', 3, '7,64,0', 0], ['use_item', null, '6,63,0', 3]])
})

test('ride_boat: ボートがなければ断り、あれば行き先へ向かう', async () => {
  assert.match((await toolsWith([]).callTool('ride_boat', { x: 60, z: 0 })).result, /you have no boat/)
  const { callTool, runs } = toolsWith([['spruce_boat', 1]])
  assert.equal((await callTool('ride_boat', { x: 60, z: 0 })).ok, true)
  assert.equal(runs[0].verb, 'ride_boat')
  assert.deepEqual([runs[0].pos.x, runs[0].pos.z], [60, 0])
  // 乗るボートにチェスト付きは入れない（持ち物用）
  assert.ok(BOAT.test('bamboo_raft') && !BOAT.test('oak_chest_boat'))
})

test('水の上で続きを漕げるのは、遠くへ移動する行動だけ（ほかの行動の前には降りる）', () => {
  for (const verb of ['goto_pos', 'explore', 'go_home', 'goto_memory', 'ride_boat']) assert.ok(TRAVELS.has(verb), verb)
  for (const verb of ['dig', 'craft', 'attack', 'use_item']) assert.ok(!TRAVELS.has(verb), verb)
})

test('ミラー: ボットが送ったボートの位置は、視聴者のボートにも送る', () => {
  const written = []
  const viewer = { write: (name, params) => written.push({ name, params }) }
  const bot = { _client: { write: () => {} }, inventory: null }
  relayOwnState(bot, new Set([viewer]), () => null)
  bot._client.write('vehicle_move', { x: 1, y: 62.5, z: 3, yaw: 90, pitch: 0, onGround: false })
  assert.deepEqual(written, [{ name: 'vehicle_move', params: { x: 1, y: 62.5, z: 3, yaw: 90, pitch: 0 } }])
})
