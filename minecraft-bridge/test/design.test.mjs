import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { BuildPlan } from '../src/build.mjs'
import { homeFromPlan } from '../src/home.mjs'

const { Vec3 } = vec3Pkg
const design = { name: 'ぽかぽか', concept: '木の家', width: 3, depth: 3, wall_height: 1, door_side: 'south', door_offset: 1, corner_pillars: false }
const blocks = [{ x: 1, y: 0, z: 2, block: 'door' }, { x: 0, y: 0, z: 0, block: 'planks' }]

test('設計はプランと一緒に保存と読み込みを通り、拠点に渡る', () => {
  const plan = BuildPlan.fromJSON(JSON.parse(JSON.stringify(new BuildPlan({ blocks, width: 3, depth: 3, height: 2, design }).toJSON())))
  assert.deepEqual(plan.design, design)

  plan.origin = new Vec3(10, 70, 10)
  const home = homeFromPlan(plan)
  assert.equal(home.name, 'ぽかぽか')
  assert.deepEqual(home.design, design)
})

test('設計なしで送ったプランは設計を持たない', () => {
  assert.equal(new BuildPlan({ blocks, width: 3, depth: 3, height: 2 }).design, null)
})
