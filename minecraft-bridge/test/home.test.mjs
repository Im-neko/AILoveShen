import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { inHouse, houseAround } from '../src/home.mjs'

const { Vec3 } = vec3Pkg
const v = (x, y, z) => new Vec3(x, y, z)
// 原点 (100, 70, 0) の 5x5 の家: 壁は x 100..104、z 0..4。室内は x 101..103、z 1..3
const plan = { origin: v(100, 70, 0), size: { width: 5, depth: 5, height: 4 } }
const home = { min: v(101, 70, 1), max: v(103, 70, 3) }

test('計画した家とその壁は守る', () => {
  const state = { plan, home: null }
  assert.equal(inHouse(state, v(100, 71, 0)), true) // 角の柱
  assert.equal(inHouse(state, v(102, 74, 2)), true) // 屋根
  assert.equal(inHouse(state, v(105, 71, 2)), false)
  assert.equal(inHouse(state, v(105, 70, 2), 1), true) // 作業台を置くための余白
})

test('新しいプランが別の場所にあっても拠点は守る', () => {
  const state = { plan: { origin: v(200, 70, 0), size: plan.size }, home }
  assert.equal(inHouse(state, v(100, 71, 0)), true)
  assert.equal(inHouse(state, v(104, 73, 4)), true)
  assert.equal(inHouse(state, v(106, 71, 2)), false)
})

test('前の家の中にいれば、その家から出る（town4d: 前の家のベッドで生き返り、閉じたドアから出られなかった）', () => {
  const former = { min: v(-15, 66, 7), max: v(-13, 66, 9) }
  const state = { home, formerHomes: [former] }
  const at = (x, y, z) => ({ entity: { position: v(x + 0.5, y, z + 0.5) } })
  assert.equal(houseAround(at(-14, 66, 8), state), former)
  assert.equal(houseAround(at(102, 70, 2), state), home)
  assert.equal(houseAround(at(0, 70, 0), state), null)
})

test('家の中のどこかにあるベッドを数え、記録する（手で置いたベッドを「ない」とし、置く目標を立て続けていた）', async () => {
  const { hasBed } = await import('../src/home.mjs')
  const blocks = new Map([['103,70,1', 'red_bed'], ['103,70,2', 'red_bed']])
  const bot = { blockAt: (p) => ({ name: blocks.get(`${p.x},${p.y},${p.z}`) ?? 'air' }) }
  const h = { ...home, bed: null }
  assert.equal(hasBed(bot, h), true)
  assert.deepEqual([h.bed.x, h.bed.y, h.bed.z], [103, 70, 1])
  // 記録した位置のベッドが壊されたら、ほかを探し、なければ「ない」
  blocks.clear()
  assert.equal(hasBed(bot, h), false)
  assert.equal(h.bed, null)
})

test('記録したベッドの位置が読み込まれていない（ボットが遠い）ときは記録を信じる', async () => {
  const { hasBed } = await import('../src/home.mjs')
  const far = { blockAt: () => null }
  assert.equal(hasBed(far, { ...home, bed: v(102, 70, 2) }), true)
  assert.equal(hasBed(far, { ...home, bed: null }), false)
  assert.equal(hasBed(far, null), false)
})

// 室内 3x3（x 101..103、z 1..3、床 y 69）。ドアは南の壁 (102, 70, 0)、内側のセルは (102, 70, 1)
function roomBot (extra = {}) {
  return {
    blockAt: (p) => {
      if (p.y === 69) return { name: 'oak_planks', boundingBox: 'block' }
      const name = extra[`${p.x},${p.y},${p.z}`] ?? 'air'
      return { name, boundingBox: name === 'air' || name === 'torch' ? 'empty' : 'block' }
    }
  }
}
const room = { ...home, door: v(102, 70, 0), inside: v(102, 70, 1) }
const key = (p) => `${p.x},${p.z}`

test('ベッドはまずドアからまっすぐ奥に置く', async () => {
  const { bedSpot } = await import('../src/home.mjs')
  const spot = bedSpot(roomBot(), room)
  assert.equal(key(spot.foot), '102,2')
  assert.equal(key(spot.inward), '0,1')
  assert.equal(key(spot.stand), '102,1')
})

test('まっすぐ奥がふさがっていても、室内の空いた 2 マスに置く（最小の家でベッドを置く小目標が詰まった）', async () => {
  const { bedSpot } = await import('../src/home.mjs')
  const spot = bedSpot(roomBot({ '102,70,3': 'torch' }), room)
  assert.ok(spot)
  const cells = [spot.foot, spot.foot.plus(spot.inward)]
  for (const c of cells) {
    assert.ok(c.x >= 101 && c.x <= 103 && c.z >= 1 && c.z <= 3, `inside the room: ${c}`)
    assert.notEqual(key(c), '102,3') // たいまつ
    assert.notEqual(key(c), '102,1') // ドアの内側はふさがない
  }
  // 立つ場所は足側の手前（頭はボットの向いた方に伸びる）
  assert.equal(key(spot.stand), key(spot.foot.minus(spot.inward)))
})

test('空いた 2 マスがなければ置く場所はない', async () => {
  const { bedSpot } = await import('../src/home.mjs')
  const full = {}
  for (const [x, z] of [[101, 1], [101, 2], [101, 3], [102, 2], [103, 1], [103, 2], [103, 3]]) full[`${x},70,${z}`] = 'chest'
  assert.equal(bedSpot(roomBot(full), room), null)
})
