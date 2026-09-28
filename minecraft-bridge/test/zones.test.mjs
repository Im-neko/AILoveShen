import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { zoneBoxes, zonesAround } from '../src/zones.mjs'

const { Vec3 } = vec3Pkg

test('区画: 中心から 12 x 12、A1 が北西、列が x、行が z', () => {
  const boxes = zoneBoxes({ x: 0, z: 0 })
  assert.equal(boxes.length, 144)
  assert.deepEqual(boxes[0], { id: 'A1', min_x: -48, min_z: -48, max_x: -41, max_z: -41 })
  assert.equal(boxes[1].id, 'B1')
  assert.equal(boxes[12].id, 'A2')
  assert.deepEqual(boxes.at(-1), { id: 'L12', min_x: 40, min_z: 40, max_x: 47, max_z: 47 })
})

test('区画の地形: 水、平らさ、見えていない所、重なる施設', () => {
  // 地面は y 69 の草。x < -40 は水、z >= 40 は読み込まれていない、x 0..7 z 0..7 は段々
  const bot = {
    entity: { position: new Vec3(0, 70, 0) },
    blockAt (p) {
      if (p.z >= 40) return null
      const ground = p.x >= 0 && p.x <= 7 && p.z >= 0 && p.z <= 7 ? 69 + (p.x % 4) : 69
      if (p.y > ground) return { name: 'air', boundingBox: 'empty' }
      if (p.x < -40) return { name: 'water', boundingBox: 'empty' }
      return { name: 'grass_block', boundingBox: 'block' }
    }
  }
  const state = { facilities: { home_farm: { name: 'home_farm', kind: 'farm', box: { min: { x: 1, y: 69, z: 1 }, max: { x: 3, y: 69, z: 2 } } } } }
  const zones = Object.fromEntries(zonesAround(bot, state, { x: 0, z: 0 }).map((z) => [z.id, z]))
  assert.equal(zones.A1.water, 1)
  assert.equal(zones.B1.water, 0)
  assert.equal(zones.B1.flat, 1)
  assert.equal(zones.B1.seen, 1)
  assert.equal(zones.A12.seen, 0)
  assert.equal(zones.G7.flat, 0.75) // 高さ 69..72 が同じ数: 真ん中（71）の ±1 は 3/4
  assert.deepEqual(zones.G7.facilities, [{ name: 'home_farm', kind: 'farm' }])
})
