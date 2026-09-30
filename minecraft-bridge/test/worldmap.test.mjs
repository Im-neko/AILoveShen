import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import minecraftData from 'minecraft-data'
import {
  WorldMap, makeClassifier, rayGrid, cameraBasis, sampleOnce, reflectLive, createSampler, minuteOf, minutesSince,
  AIR, SET_NEW, SET_SAME, SET_CHANGED, FORMAT
} from '../src/worldmap.mjs'

const registry = minecraftData('1.21.4')
const cls = makeClassifier(registry)
const state = (name) => registry.blocksByName[name].defaultState
const cellOf = (name) => cls.cellOf(name)

// 空気だけの世界に、blocks（"x,y,z" -> 名前）を置く。unloaded(x, y, z) が真の所は未読み込み
function world (blocks = {}, unloaded = () => false) {
  const read = (x, y, z) => {
    if (unloaded(x, y, z)) return -1
    const name = blocks[`${x},${y},${z}`]
    return name ? state(name) : state('air')
  }
  return { blocks, read }
}

const EYE = { x: 0.5, y: 70.5, z: 0.5 } // セル (0, 70, 0)
const rays = rayGrid()
const look = (w, map = new WorldMap(), yaw = 0, pitch = 0, opts = {}) => {
  const r = sampleOnce(map, w.read, cls, EYE, yaw, pitch, rays, { minute: 1, ...opts })
  return { map, r }
}
const cellAtEye = (dx, dy, dz) => [Math.floor(EYE.x + dx), Math.floor(EYE.y + dy), Math.floor(EYE.z + dz)]
const key = (p) => p.join(',')
const known = (map, p) => map.get(...p) !== null

test('視野: 標準の垂直 70 度、16:9 で水平は約 102 度。角の外にはレイを出さない', () => {
  const n = rays.length / 3
  assert.ok(n > 1400 && n < 1900, `${n} 本`)
  let maxAz = 0
  let maxEl = 0
  for (let i = 0; i < rays.length; i += 3) {
    maxAz = Math.max(maxAz, Math.abs(Math.atan2(rays[i], rays[i + 2])))
    maxEl = Math.max(maxEl, Math.abs(Math.asin(rays[i + 1])))
  }
  assert.ok(maxAz * 180 / Math.PI <= 51.3 && maxAz * 180 / Math.PI >= 49, `水平 ${maxAz * 180 / Math.PI}`)
  assert.ok(maxEl * 180 / Math.PI <= 35.1 && maxEl * 180 / Math.PI >= 33, `垂直 ${maxEl * 180 / Math.PI}`)
  // 真ん中のレイがある
  let center = false
  for (let i = 0; i < rays.length; i += 3) center ||= rays[i] === 0 && rays[i + 1] === 0 && rays[i + 2] === 1
  assert.ok(center)
})

const rounded = (v) => v.map((n) => Math.round(n) + 0)

test('向き: yaw 0 は北（-z）、pitch が正で上向き、yaw が増えると左（-x）を向く', () => {
  const a = cameraBasis(0, 0)
  assert.deepEqual(rounded(a.forward), [0, 0, -1])
  assert.deepEqual(rounded(a.right), [1, 0, 0])
  assert.deepEqual(rounded(a.up), [0, 1, 0])
  assert.ok(cameraBasis(0, 0.5).forward[1] > 0)
  assert.deepEqual(rounded(cameraBasis(Math.PI / 2, 0).forward), [-1, 0, 0])
})

test('視野の内側の物は記録され、外側は記録されない（水平・垂直・距離）', () => {
  const at = (dist, az, el) => {
    const a = (az * Math.PI) / 180
    const e = (el * Math.PI) / 180
    return cellAtEye(dist * Math.sin(a) * Math.cos(e), dist * Math.sin(e), -dist * Math.cos(a) * Math.cos(e))
  }
  const cases = [
    ['正面 10 m', at(10, 0, 0), true],
    ['右 45 度', at(10, 45, 0), true],
    ['右 60 度', at(10, 60, 0), false],
    ['左 45 度', at(10, -45, 0), true],
    ['真横', at(10, 90, 0), false],
    ['後ろ', at(10, 180, 0), false],
    ['上 25 度', at(10, 0, 25), true],
    ['上 45 度', at(10, 0, 45), false],
    ['下 25 度', at(10, 0, -25), true],
    ['下 45 度', at(10, 0, -45), false],
    ['15 m', at(15, 0, 0), true],
    ['25 m', at(25, 0, 0), false]
  ]
  for (const [label, p, expected] of cases) {
    const w = world({ [key(p)]: 'stone' })
    const { map } = look(w)
    assert.equal(known(map, p), expected, label)
  }
})

test('遮蔽: 壁の向こうは記録されない。壁の手前の面は記録される', () => {
  const blocks = {}
  for (let x = -3; x <= 3; x++) for (let y = 66; y <= 74; y++) blocks[`${x},${y},-5`] = 'stone'
  blocks['0,70,-9'] = 'oak_log'
  const { map } = look(world(blocks))
  assert.ok(known(map, [0, 70, -5]), '壁')
  assert.equal(map.get(0, 70, -5).cell, cellOf('stone'))
  assert.equal(known(map, [0, 70, -9]), false, '壁の向こう')
  assert.equal(known(map, [0, 70, -4]), true)
  assert.equal(map.get(0, 70, -4).cell, AIR, '手前の空気')
})

test('葉は視線を遮り、ガラスは通す', () => {
  const behindLeaves = world({ '0,70,-3': 'oak_leaves', '0,70,-6': 'oak_log' })
  assert.equal(known(look(behindLeaves).map, [0, 70, -6]), false)
  assert.ok(known(look(behindLeaves).map, [0, 70, -3]))
  const behindGlass = world({ '0,70,-3': 'glass', '0,70,-6': 'oak_log' })
  const map = look(behindGlass).map
  assert.ok(known(map, [0, 70, -6]))
  assert.equal(map.get(0, 70, -3).cell, cellOf('glass'))
})

test('柵は視線を遮らず、水は通り抜ける', () => {
  assert.ok(known(look(world({ '0,70,-3': 'oak_fence', '0,70,-6': 'oak_log' })).map, [0, 70, -6]))
  assert.ok(known(look(world({ '0,70,-3': 'water', '0,70,-6': 'oak_log' })).map, [0, 70, -6]))
})

test('未読み込みのセルで止まり、そのセルは記録しない', () => {
  const w = world({ '0,70,-8': 'oak_log' }, (x, y, z) => z <= -5)
  const { map } = look(w)
  assert.equal(known(map, [0, 70, -5]), false)
  assert.equal(known(map, [0, 70, -8]), false)
  assert.ok(known(map, [0, 70, -4]))
})

test('空気が「空だった」と記録される。時刻も入る', () => {
  const { map } = look(world())
  const c = map.get(0, 70, -4)
  assert.equal(c.cell, AIR)
  assert.equal(c.minute, 1)
})

test('見直したとき、前と違えば上書きして数える（壊された、置かれた）', () => {
  const p = [0, 70, -6]
  const w = world({ [key(p)]: 'oak_log' })
  const map = new WorldMap()
  const first = look(w, map).r
  assert.ok(first.added > 0)
  assert.equal(first.changed, 0)
  assert.equal(map.get(...p).cell, cellOf('oak_log'))
  const same = look(w, map, 0, 0, { minute: 5 }).r
  assert.equal(same.changed, 0)
  assert.equal(same.added, 0)
  assert.equal(map.get(...p).minute, 5, '時刻は更新される')
  delete w.blocks[key(p)]
  const after = look(w, map, 0, 0, { minute: 9 }).r
  assert.ok(after.changed >= 1)
  assert.equal(map.get(...p).cell, AIR)
})

test('set: 初めて / 同じ / 違う を返す', () => {
  const map = new WorldMap()
  assert.equal(map.set(1, 2, 3, 5, 0), SET_NEW)
  assert.equal(map.set(1, 2, 3, 5, 1), SET_SAME)
  assert.equal(map.set(1, 2, 3, 6, 1), SET_CHANGED)
  assert.equal(map.total.added, 1)
  assert.equal(map.total.changed, 1)
})

test('自分で掘った・置いたセルは、見ていなくてもその場で反映される', () => {
  const w = world({ '5,60,5': 'oak_log' })
  const map = new WorldMap()
  reflectLive(map, w.read, cls, { x: 5, y: 60, z: 5 }, 3)
  assert.equal(map.get(5, 60, 5).cell, cellOf('oak_log'))
  delete w.blocks['5,60,5']
  reflectLive(map, w.read, cls, { x: 5, y: 60, z: 5 }, 4)
  assert.equal(map.get(5, 60, 5).cell, AIR)
  reflectLive(map, () => -1, cls, { x: 9, y: 60, z: 9 }, 4)
  assert.equal(map.get(9, 60, 9), null, '未読み込みは書かない')
})

test('負の座標でも同じセルに当たる', () => {
  const map = new WorldMap()
  map.set(-1, -1, -1, 7, 0)
  map.set(-16, 64, -17, 8, 0)
  assert.equal(map.get(-1, -1, -1).cell, 7)
  assert.equal(map.get(-16, 64, -17).cell, 8)
  assert.equal(map.get(-2, -1, -1), null)
})

test('find: 種類で探して近い順。空気に接するものだけにもできる', () => {
  const map = new WorldMap()
  const log = cellOf('oak_log')
  for (const [x, z] of [[10, 0], [3, 0], [30, 0]]) map.set(x, 64, z, log, 1)
  map.set(3, 65, 0, AIR, 1)
  const from = { x: 0, y: 64, z: 0 }
  assert.deepEqual(map.find(new Set([log]), from, 50).map((r) => r.x), [3, 10, 30])
  assert.deepEqual(map.find(new Set([log]), from, 20).map((r) => r.x), [3, 10])
  assert.deepEqual(map.find(new Set([log]), from, 50, { exposed: true }).map((r) => r.x), [3])
  assert.equal(map.find(new Set([log]), from, 50, { limit: 1 }).length, 1)
  assert.deepEqual(map.find(new Set([cellOf('stone')]), from, 50), [])
})

test('regionReps: 種類ごと・16x16 の区域ごとに一番近い空気に接するセルと数', () => {
  const map = new WorldMap()
  const log = cellOf('oak_log')
  for (const [x, z] of [[2, 2], [4, 2], [40, 2]]) {
    map.set(x, 64, z, log, 1)
    map.set(x, 65, z, AIR, 1)
  }
  const reps = map.regionReps(new Set([log]), { x: 0, y: 64, z: 0 }, 100)
  const regions = [...reps.get(log).values()].sort((a, b) => a.x - b.x)
  assert.deepEqual(regions.map((r) => [r.x, r.count]), [[2, 2], [40, 1]])
})

test('保存して読み直すと同じ。バージョンが違えば読まない', async () => {
  const dir = mkdtempSync(path.join(tmpdir(), 'worldmap-'))
  try {
    const map = new WorldMap()
    map.set(3, 64, -5, 9, 12)
    map.set(-40, 70, 200, 11, 13)
    assert.equal(await map.save(dir, '1.21.4'), 2, '領域が 2 つ')
    assert.equal(await map.save(dir, '1.21.4'), 0, '変わっていなければ書かない')
    const back = await WorldMap.load(dir, '1.21.4')
    assert.deepEqual(back.get(3, 64, -5), { cell: 9, minute: 12 })
    assert.deepEqual(back.get(-40, 70, 200), { cell: 11, minute: 13 })
    assert.equal(back.sectionCount, 2)
    assert.equal(back.find(new Set([9]), { x: 3, y: 64, z: -5 }, 5).length, 1, '数えも復元される')
    assert.equal((await WorldMap.load(dir, '1.20.4')).sectionCount, 0)
    assert.equal((await WorldMap.load(path.join(dir, 'none'), '1.21.4')).sectionCount, 0)
    assert.equal(FORMAT, 1)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('時刻は分で、16 ビットで一周しても差が出る', () => {
  assert.equal(minuteOf(1200 * 5 + 3), 5)
  assert.equal(minutesSince(5, 3), 2)
  assert.equal(minutesSince(2, 65535), 3)
})

function fakeBot (w, at = { x: 0.5, y: 70, z: 0.5 }) {
  return { entity: { position: { ...at }, yaw: 0, pitch: 0, eyeHeight: 0.5 }, time: { age: 1200 * 7 }, world: null, w }
}

test('サンプラー: 見て、動いていなければもう見ない。動くか向くと見る', async () => {
  const w = world({ '0,70,-6': 'oak_log' })
  const bot = fakeBot(w)
  const sampler = createSampler(bot, new WorldMap(), cls, { reader: () => w.read })
  await sampler.tick()
  assert.equal(sampler.stats.samples, 1)
  await sampler.tick()
  assert.equal(sampler.stats.samples, 1, '動いていない')
  bot.entity.yaw += 0.05
  await sampler.tick()
  assert.equal(sampler.stats.samples, 1, '少し向いただけ')
  bot.entity.yaw += 0.3
  await sampler.tick()
  assert.equal(sampler.stats.samples, 2, '向きが変わった')
  bot.entity.position.x += 3
  await sampler.tick()
  assert.equal(sampler.stats.samples, 3, '動いた')
})

test('サンプラー: 前の回が終わっていなければ飛ばす。時刻はワールドの分', async () => {
  const w = world({ '0,70,-6': 'oak_log' })
  const bot = fakeBot(w)
  const map = new WorldMap()
  let release
  const gate = new Promise((resolve) => { release = resolve })
  let first = true
  const sampler = createSampler(bot, map, cls, {
    reader: () => w.read,
    clock: (() => { let t = 0; return () => (t += 10) })(), // 1 呼び出しで 10 ms 進む: すぐスライスを譲る
    yieldTurn: async () => { if (first) { first = false; await gate } }
  })
  const running = sampler.sampleNow()
  assert.equal(await sampler.sampleNow(), false, '実行中は飛ばす')
  release()
  assert.equal(await running, true)
  assert.equal(map.get(0, 70, -6).minute, 7)
  assert.ok(sampler.stats.ms > 0)
})

test('保存が重なっても、書き合わず順に走る', async () => {
  const dir = mkdtempSync(path.join(tmpdir(), 'worldmap-'))
  try {
    const map = new WorldMap()
    map.set(1, 64, 1, 9, 1)
    const [a, b] = await Promise.all([map.save(dir, '1.21.4'), map.save(dir, '1.21.4')])
    assert.equal(a + b, 1, '後の回は書くものがない')
    assert.deepEqual((await WorldMap.load(dir, '1.21.4')).get(1, 64, 1), { cell: 9, minute: 1 })
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})
