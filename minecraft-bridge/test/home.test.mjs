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
