import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { inHouse } from '../src/home.mjs'

const { Vec3 } = vec3Pkg
const v = (x, y, z) => new Vec3(x, y, z)
// A 5x5 house at origin (100, 70, 0): walls x 100..104, z 0..4; interior 101..103, 1..3
const plan = { origin: v(100, 70, 0), size: { width: 5, depth: 5, height: 4 } }
const home = { min: v(101, 70, 1), max: v(103, 70, 3) }

test('the planned house and its walls are protected', () => {
  const state = { plan, home: null }
  assert.equal(inHouse(state, v(100, 71, 0)), true) // corner pillar
  assert.equal(inHouse(state, v(102, 74, 2)), true) // roof
  assert.equal(inHouse(state, v(105, 71, 2)), false)
  assert.equal(inHouse(state, v(105, 70, 2), 1), true) // margin for placing a table
})

test('the home stays protected when a new plan is elsewhere', () => {
  const state = { plan: { origin: v(200, 70, 0), size: plan.size }, home }
  assert.equal(inHouse(state, v(100, 71, 0)), true)
  assert.equal(inHouse(state, v(104, 73, 4)), true)
  assert.equal(inHouse(state, v(106, 71, 2)), false)
})
