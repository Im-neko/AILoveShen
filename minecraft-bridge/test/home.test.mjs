import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { inHouse } from '../src/home.mjs'

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
