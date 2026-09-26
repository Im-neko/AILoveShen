import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { placedBedToTake } from '../src/furniture.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')
const home = { door: new Vec3(2, 70, 4), inside: new Vec3(2, 70, 3), min: new Vec3(1, 70, 1), max: new Vec3(3, 70, 3) }

function bot (beds) {
  return {
    registry: md,
    entity: { position: new Vec3(10, 70, 10) },
    findBlocks: () => beds,
    blockAt: () => ({ name: 'red_bed' })
  }
}

test('家に運べるベッドは、今の家の外に置いてある一番近いもの', () => {
  const inHome = new Vec3(1, 70, 1)
  const far = new Vec3(30, 70, 30)
  const near = new Vec3(12, 70, 10)
  assert.deepEqual(placedBedToTake(bot([inHome, far, near]), { home }).pos, near)
  assert.equal(placedBedToTake(bot([inHome]), { home }), null)
  assert.deepEqual(placedBedToTake(bot([inHome]), { home: null }).pos, inHome) // 家がなければどれでも
})
