// 失敗した生の bot の技に添える現場（設計書 193）: 判断でなく世界の事実
import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { sceneOf } from '../src/scene.mjs'

const { Vec3 } = vec3Pkg

test('立っている所・向き・手の物・足元と頭とまわりのブロック・近くのものを書く', () => {
  const blocks = { '0,69,0': 'grass_block', '0,70,1': 'stone', '1,70,0': 'crafting_table' }
  const bot = {
    entity: { position: new Vec3(0.5, 70, 0.5), yaw: 1.5, pitch: 0.2, onGround: true },
    controlState: { sneak: true },
    heldItem: { name: 'crafting_table' },
    entities: { 1: { position: new Vec3(1.5, 70, 0.5), name: 'zombie' }, 2: { position: new Vec3(30, 70, 0), name: 'cow' } },
    blockAt: (p) => ({ name: blocks[`${p.x},${p.y},${p.z}`] ?? 'air', boundingBox: 'empty' })
  }
  bot.entities[0] = bot.entity
  const s = sceneOf(bot)
  assert.deepEqual(s.position, { x: 0.5, y: 70, z: 0.5 })
  assert.equal(s.below, 'grass_block')
  assert.equal(s.feet, 'air')
  assert.deepEqual(s.around_feet, { north: 'air', south: 'stone', east: 'crafting_table', west: 'air' })
  assert.equal(s.held, 'crafting_table')
  assert.equal(s.sneaking, true)
  assert.deepEqual(s.within_3m, ['zombie'])
})
