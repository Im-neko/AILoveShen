import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { PRIMITIVES } from '../src/primitives.mjs'

const { Vec3 } = vec3Pkg

// 壁（0,65,-1 の石）に松明を付けるボット。placed: 置いた後にそのセルが何になるか
function placer (placed) {
  const cells = new Map()
  const key = (p) => `${p.x},${p.y},${p.z}`
  const blockAt = (p) => {
    if (p.z === -1) return { name: 'stone', boundingBox: 'block', position: p, shapes: [[0, 0, 0, 1, 1, 1]] }
    const name = cells.get(key(p)) ?? 'air'
    return { name, boundingBox: name === 'air' || name.endsWith('torch') ? 'empty' : 'block', position: p, shapes: [] }
  }
  return {
    entity: { position: new Vec3(0, 64, 2), onGround: true },
    world: { getBlock: blockAt },
    blockAt,
    on: () => {},
    off: () => {},
    waitForTicks: async () => {},
    equip: async () => {},
    placeBlock: async (against, face) => { if (placed) cells.set(key(against.position.plus(face)), placed) },
    inventory: { items: () => [{ name: 'torch', count: 4 }] },
    pathfinder: { goto: async () => {}, setGoal: () => {} }
  }
}
const c = { item: 'torch', pos: new Vec3(0, 65, 0), against: new Vec3(0, 65, -1) }

test('壁に付いた松明（当たり判定なし、wall_torch）は置けたことになる。何も変わらなければ失敗', async () => {
  const signal = new AbortController().signal
  assert.equal(await PRIMITIVES.place_at(placer('wall_torch'), {}, c, signal), 'placed wall_torch at 0,65,0')
  await assert.rejects(PRIMITIVES.place_at(placer(null), {}, c, signal), /the torch was not placed/)
})
