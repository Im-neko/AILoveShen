// 採掘場の出入りは階段を通る（設計書 145）: 洞窟の近道を通らず、階段のセルを順に目標にする。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { mineAround, stairWaypoints, enterMine, leaveMine, routeThroughMine, climbsDirect } from '../src/mine-route.mjs'

const { Vec3 } = vec3Pkg

// 入口 (63,77,-453) から y=17 まで 1 段ずつ下りる 61 セルの階段、底の部屋、西へ伸びる坑道
const stairs = Array.from({ length: 61 }, (_, i) => ({ x: 63 + (i % 4), y: 77 - i, z: -453 + Math.floor((i % 8) / 4) * 3, h: 3, part: 'stairs' }))
const room = [{ x: 65, y: 16, z: -453, h: 2, part: 'room' }, { x: 64, y: 16, z: -452, h: 2, part: 'room' }]
const tunnel = Array.from({ length: 10 }, (_, i) => ({ x: 62 - i, y: 16, z: -451, h: 2, part: 'tunnel' }))
const fac = { name: 'mine_1', kind: 'mine', layout: { cells: [...stairs, ...room, ...tunnel] } }
const state = { facilities: { mine_1: fac } }
const botAt = (x, y, z) => ({ entity: { position: new Vec3(x, y, z) } })
const signal = new AbortController().signal
const record = () => {
  const goals = []
  return { goals, go: async (g) => { goals.push([g.x, g.y, g.z]) } }
}

test('採掘場の中にいるか: セルの上下の範囲にいれば中、離れていれば外', () => {
  assert.equal(mineAround(state, new Vec3(62.5, 16, -450.5)), fac) // 坑道
  assert.equal(mineAround(state, new Vec3(63.5, 76.5, -452.5)), fac) // 入口
  assert.equal(mineAround(state, new Vec3(85.5, 12, -450.5)), null) // 東の洞窟
  assert.equal(mineAround(state, new Vec3(158.5, 79, -455.5)), null)
})

test('階段の途中のセルは、入口から底まで 5 個おき。底を含む', () => {
  const w = stairWaypoints(fac)
  assert.equal(w[0].y, 72)
  assert.equal(w.at(-1).y, 17)
  assert.ok(w.every((c, i) => i === 0 || c.y < w[i - 1].y))
})

test('外から深い所へ: 入口まで歩いてから、階段を順に下りる', async () => {
  const r = record()
  const walked = []
  const through = await routeThroughMine(botAt(158.5, 79, -455.5), state, new Vec3(55.5, 16, -450.5), async (p) => { walked.push([p.x, p.y, p.z]); r.goals.push('walked') }, r.go, signal)
  assert.equal(through, true)
  assert.deepEqual(walked, [[63.5, 77, -452.5]])
  assert.deepEqual(r.goals[0], 'walked')
  assert.deepEqual(r.goals[1], [63, 77, -453]) // 入口
  assert.equal(r.goals.at(-1)[1], 17) // 底
  assert.ok(r.goals.slice(2).every((g, i, all) => i === 0 || g[1] < all[i - 1][1]), '下りる向きに並ぶ')
})

test('坑道から地上へ: 底の階段へ出て、入口まで階段を上る（東の洞窟へは行かない）', async () => {
  const r = record()
  const through = await routeThroughMine(botAt(55.5, 16, -450.5), state, new Vec3(158.5, 79, -455.5), async () => {}, r.go, signal)
  assert.equal(through, true)
  assert.deepEqual(r.goals[0], [stairs.at(-1).x, stairs.at(-1).y, stairs.at(-1).z]) // 階段の底
  assert.deepEqual(r.goals.at(-1), [63, 77, -453]) // 入口
  assert.ok(r.goals.every((g, i, all) => i === 0 || g[1] > all[i - 1][1]), '上る向きに並ぶ')
  assert.ok(r.goals.every((g) => g[0] >= 62 && g[0] <= 66), '目標は階段の上だけ')
})

test('階段の途中から地上へ: その高さの次のセルから上る', async () => {
  const r = record()
  await leaveMine(fac, new Vec3(stairs[30].x + 0.5, stairs[30].y, stairs[30].z + 0.5), r.go, signal)
  assert.equal(r.goals[0][1], stairs[25].y)
  assert.deepEqual(r.goals.at(-1), [63, 77, -453])
})

test('何もしない場合: 同じ採掘場の中、地上どうし、入口の近くの浅い所', async () => {
  const none = async () => { throw new Error('歩かないはず') }
  assert.equal(await routeThroughMine(botAt(55.5, 16, -450.5), state, new Vec3(55.5, 16, -450.5), none, none, signal), false)
  assert.equal(await routeThroughMine(botAt(158.5, 79, -455.5), state, new Vec3(100.5, 74, -400.5), none, none, signal), false)
  assert.equal(await routeThroughMine(botAt(158.5, 79, -455.5), state, new Vec3(63.5, 77, -452.5), none, none, signal), false) // 入口そのもの
  assert.equal(await routeThroughMine(botAt(64.5, 74, -452.5), state, new Vec3(158.5, 79, -455.5), none, none, signal), false) // 入口のすぐ下
})

test('採掘場がなければ何もしない', async () => {
  const none = async () => { throw new Error('歩かないはず') }
  assert.equal(await routeThroughMine(botAt(0, 70, 0), { facilities: {} }, new Vec3(10, 16, 10), none, none, signal), false)
})

test('地下から地上へ向かうときだけ、区間に分けず高さも含めて歩く', () => {
  assert.equal(climbsDirect(new Vec3(95, -16, -451), new Vec3(158, 79, -455)), true) // 洞窟の底から家
  assert.equal(climbsDirect(new Vec3(48, 16, -448), new Vec3(158, 79, -455)), true) // 採掘場から家
  assert.equal(climbsDirect(new Vec3(100, 74, -400), new Vec3(158, 79, -455)), false) // 地上どうし
  assert.equal(climbsDirect(new Vec3(100, 74, -400), new Vec3(158, 60, -455)), false) // 下りる向きは今までどおり
  assert.equal(climbsDirect(new Vec3(100, 74, -400), { x: 158, z: -455 }), false) // 高さのない目標
})
