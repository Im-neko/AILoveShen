import test from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { jumpAndPlace } from '../src/home.mjs'

const { Vec3 } = vec3Pkg

// 足元のセルに置き換えられない物（松明）があるとき、足場を置く前に壊す
function bot (cellBlock) {
  const log = []
  const cell = new Vec3(5, 63, 5)
  const b = {
    entity: { position: new Vec3(5.5, 63, 5.5), yaw: 0 },
    blockAt: (p) => p.equals(cell) ? cellBlock : p.equals(cell.offset(0, -1, 0)) ? { name: 'cobblestone', boundingBox: 'block', position: p } : { name: 'stone', boundingBox: 'block', position: p },
    equip: async (it) => log.push(`equip ${it.name}`),
    look: async () => {},
    setControlState: () => {},
    waitForTicks: async () => { b.entity.position = new Vec3(5.5, 64.2, 5.5) },
    dig: async (blk) => log.push(`dig ${blk.name}`),
    placeBlock: async () => log.push('place')
  }
  return { b, log, cell }
}

test('jumpAndPlace: 立っているセルの松明は、足場を置く前に壊す（置き換えられず、積めなかった）', async () => {
  const { b, log, cell } = bot({ name: 'torch', boundingBox: 'empty', position: new Vec3(5, 63, 5) })
  await jumpAndPlace(b, cell, { name: 'cobblestone' })
  assert.deepEqual(log, ['dig torch', 'equip cobblestone', 'place'])
})

test('jumpAndPlace: 空気のセルは壊さない', async () => {
  const { b, log, cell } = bot({ name: 'air', boundingBox: 'empty', position: new Vec3(5, 63, 5) })
  await jumpAndPlace(b, cell, { name: 'cobblestone' })
  assert.deepEqual(log, ['equip cobblestone', 'place'])
})
