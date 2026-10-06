import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { BuildPlan } from '../src/build.mjs'

const { Vec3 } = vec3Pkg

function plan () {
  const p = new BuildPlan({ blocks: [{ x: 0, y: 0, z: 0, block: 'planks' }, { x: 1, y: 0, z: 0, block: 'log' }], width: 2, depth: 1, height: 1 })
  p.origin = new Vec3(10, 70, 10)
  return p
}
const world = (blocks) => ({ blockAt: (pos) => (blocks === null ? null : blocks[`${pos.x},${pos.y},${pos.z}`] ?? { name: 'air', boundingBox: 'empty' }) })
const GROUND = { '10,69,10': { name: 'dirt', boundingBox: 'block' }, '11,69,10': { name: 'dirt', boundingBox: 'block' } }
const BUILT = { ...GROUND, '10,70,10': { name: 'oak_planks', boundingBox: 'block' }, '11,70,10': { name: 'oak_log', boundingBox: 'block' } }

test('離れて読み込まれていない家は、最後に見たとおり（0/N に見えて built が未達にならない）', () => {
  const p = plan()
  assert.equal(p.status(world(BUILT)).complete, true)
  assert.deepEqual(p.status(world(null)), { ...p.status(world(null)), placed: 2, complete: true })
  // 近くに戻って壊れていれば、見たとおりに未完成へ
  const { complete, placed } = p.status(world({ ...GROUND, '10,70,10': BUILT['10,70,10'] }))
  assert.deepEqual([complete, placed], [false, 1])
  assert.equal(p.status(world(null)).placed, 1)
})

test('一度も見ていないブロックは、読み込まれていなければ置いていない。最後に見た結果は保存して戻す', () => {
  const p = plan()
  assert.equal(p.status(world(null)).placed, 0)
  p.status(world(BUILT))
  const again = BuildPlan.fromJSON(JSON.parse(JSON.stringify(p.toJSON())))
  assert.equal(again.status(world(null)).complete, true)
})
