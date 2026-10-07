import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { EventEmitter } from 'node:events'
import { mkdtempSync, readFileSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { waterCrossing, surfaceAt, drive, driveToShore, boxFree, placeBoat, nearestShore, BoatUnusable, BOAT, BOAT_MIN_WATER, EXTEND_M } from '../src/boat.mjs'
import { createTools } from '../src/tools.mjs'
import { relayOwnState } from '../src/mirror.mjs'
import { Knowledge } from '../src/knowledge.mjs'
import { TRAVELS, crossWater } from '../src/primitives.mjs'

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

// 海: x < west は陸（y 62 まで地面）、west 以上 east 未満は水（水面 y 62）、east 以上は陸。水中の自分が立つ
function seaBot ({ west = 0, east = 1000, at = v(40.5, 62.2, 0.5), spawnsBoat = false, boats = 1 } = {}) {
  const block = (p) => {
    if (p.x < west || p.x >= east) return p.y <= 62 ? { name: 'grass_block', boundingBox: 'block', position: p } : { name: 'air', boundingBox: 'empty', position: p }
    if (p.y <= 61) return { name: 'sand', boundingBox: 'block', position: p }
    if (p.y === 62) return { name: 'water', boundingBox: 'empty', position: p }
    return { name: 'air', boundingBox: 'empty', position: p }
  }
  const bot = new EventEmitter()
  const looks = []
  const controls = []
  const yaws = []
  Object.assign(bot, {
    entity: { position: at.clone(), isInWater: true, onGround: false, yaw: 0, pitch: 0 },
    entities: {},
    heldItem: null,
    blockAt: block,
    inventory: { items: () => (boats ? [{ name: 'oak_boat', count: boats }] : []) },
    equip: async () => { bot.heldItem = { name: 'oak_boat' } },
    lookAt: async (p) => { looks.push(p) },
    activateItem: () => {
      if (spawnsBoat) bot.emit('entitySpawn', { id: 9, name: 'oak_boat', position: bot.entity.position.offset(2, 0, 0) })
    },
    setControlState: (c, on) => controls.push([c, on]),
    look: async (yaw) => { yaws.push(yaw) },
    waitForTicks: async () => {}
  })
  return { bot, looks, controls, yaws }
}

test('ボートを置けなかったら、BoatUnusable を投げて、向きと目の高さと近くのボートを記録する', async () => {
  const dir = mkdtempSync(path.join(tmpdir(), 'boat-'))
  const file = path.join(dir, 'boat-refusals.jsonl')
  process.env.BOAT_REFUSALS_FILE = file
  try {
    const { bot } = seaBot()
    bot.entity.yaw = 1.5
    bot.entity.pitch = 0.9
    await assert.rejects(placeBoat(bot, v(42, 62, 0), new AbortController().signal, 30), (e) => e instanceof BoatUnusable && /was not placed on the water at 42,62,0/.test(e.message))
    assert.ok(existsSync(file))
    const row = JSON.parse(readFileSync(file, 'utf8').trim())
    assert.equal(row.bot.in_water, true)
    assert.equal(row.bot.yaw, 1.5)
    assert.equal(row.bot.pitch, 0.9)
    assert.equal(row.water_block, 'water')
    assert.equal(row.boats_in_inventory, 1)
    assert.deepEqual(row.boats_near, [])
    assert.ok(row.horizontal_to_water_m > 1)
  } finally {
    delete process.env.BOAT_REFUSALS_FILE
  }
})

test('置いたボートは、置く水のセルから離れて出ても、自分の近くなら受け取る', async () => {
  const { bot } = seaBot({ spawnsBoat: true })
  const boat = await placeBoat(bot, v(60, 62, 0), new AbortController().signal, 200)
  assert.equal(boat.id, 9)
})

test('沖の水の中でボートを置けないなら、置く水のセルは自分から 1.8m 以上離す（毎回すぐ隣のセルを選んで断られた）', async () => {
  process.env.BOAT_REFUSALS_FILE = path.join(mkdtempSync(path.join(tmpdir(), 'boat-')), 'r.jsonl')
  try {
    const { bot, looks } = seaBot({ west: 0, east: 90 })
    const ctl = new AbortController()
    bot.setControlState = () => ctl.abort(new Error('swimming started'))
    // 向こう岸（x 90 から陸）が見える沖: ボートを置けなければ目標へ泳ぐ
    await assert.rejects(crossWater(bot, { home: null }, v(120.5, 62, 0.5), 'the shore', ctl.signal), /swimming started/)
    assert.ok(looks.length >= 1)
    for (const p of looks) assert.ok(Math.hypot(p.x - bot.entity.position.x, p.z - bot.entity.position.z) >= 1.8, `placed too near: ${p}`)
  } finally {
    delete process.env.BOAT_REFUSALS_FILE
  }
})

test('ボートを置けず、目標の向こう岸が見えない沖なら、目標へ泳がず、いちばん近い岸へ泳いで戻る', async () => {
  process.env.BOAT_REFUSALS_FILE = path.join(mkdtempSync(path.join(tmpdir(), 'boat-')), 'r.jsonl')
  try {
    const { bot, yaws } = seaBot({ west: 0, at: v(40.5, 62.2, 0.5) })
    const ctl = new AbortController()
    bot.setControlState = (c, on) => { if (c === 'forward' && on) ctl.abort(new Error('swimming started')) }
    await assert.rejects(crossWater(bot, { home: null }, v(300.5, 62, 0.5), 'the unexplored land east', ctl.signal), /swimming started/)
    // 西（x が小さいほう）の岸へ向く。東（目標）へは向かない
    assert.ok(Math.sin(yaws[0]) > 0.5, `yaw ${yaws[0]}`)
  } finally {
    delete process.env.BOAT_REFUSALS_FILE
  }
})

test('岸も見えない外洋で、ボートも置けないなら、理由を付けて失敗にする', async () => {
  process.env.BOAT_REFUSALS_FILE = path.join(mkdtempSync(path.join(tmpdir(), 'boat-')), 'r.jsonl')
  try {
    const { bot } = seaBot({ west: -1000 })
    await assert.rejects(crossWater(bot, { home: null }, v(300.5, 62, 0.5), 'the unexplored land east', new AbortController().signal), /no shore is loaded within 96m to swim to/)
  } finally {
    delete process.env.BOAT_REFUSALS_FILE
  }
})

test('nearestShore: 近い輪から岸を探す。なければ null', () => {
  assert.ok(nearestShore(seaBot({ west: 0 }).bot).x < 0)
  assert.equal(nearestShore(seaBot({ west: -1000 }).bot), null)
})

test('目標が水の上なら、「着いた」でも降りずに、岸に着くまで同じ向きに漕ぐ', async () => {
  // 目標は 5m 先の沖。岸は 30m 先（x 30 から陸）: 目標で止まらず岸まで行く
  const { bot, boat, sent } = rider(v(12.5, 62.52, 0.5), { shore: 30 })
  const r = await driveToShore(bot, boat, v(17.5, 62, 0.5), new AbortController().signal)
  assert.equal(r.why, 'reached the shore')
  assert.ok(r.extended >= 1)
  assert.equal(r.openWater, false)
  const last = sent.filter((s) => s.name === 'vehicle_move').at(-1).params
  assert.ok(last.x > 24 && last.x < 30, `stopped at ${last.x}`)
})

test('漕ぎ続けても岸がなければ、openWater で返して降りない（回数の上限まで）', async () => {
  const { bot, boat } = rider(v(12.5, 62.52, 0.5), { shore: 1000 })
  const r = await driveToShore(bot, boat, v(15.5, 62, 0.5), new AbortController().signal, { maxExtensions: 1 })
  assert.equal(r.extended, 1)
  assert.equal(r.openWater, true)
  assert.ok(r.moved > EXTEND_M * 0.9)
})

test('岸の手前で止まった（前が陸）ときは、水の上でも延ばさない', async () => {
  const { bot, boat } = rider(v(12.5, 62.52, 0.5))
  const r = await driveToShore(bot, boat, v(60, 64, 0.5), new AbortController().signal)
  assert.equal(r.why, 'reached the shore')
  assert.equal(r.extended, 0)
  assert.equal(r.openWater, false)
})
