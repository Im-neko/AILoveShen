// 今いるバイオームの名前（羊は草原、など探す場所の手がかり。2026-10-12）
import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { biomeName } from '../src/observe.mjs'

const { Vec3 } = vec3Pkg

test('足元のブロックのバイオーム名を返す。読めなければ null', () => {
  const at = []
  const bot = { entity: { position: new Vec3(10.5, 64, -3.2) }, blockAt: (p) => { at.push(`${p.x},${p.y},${p.z}`); return { biome: { name: 'meadow' } } } }
  assert.equal(biomeName(bot), 'meadow')
  assert.deepEqual(at, ['10,64,-4'])
  assert.equal(biomeName({ ...bot, blockAt: () => null }), null)
  assert.equal(biomeName({ ...bot, blockAt: () => ({}) }), null)
  assert.equal(biomeName({ ...bot, blockAt: () => { throw new Error('not loaded') } }), null)
})

test('ブロックの biome.name が空でも、ボットの群系の表から id で名前を引く（サーバーが群系の表を送ったあと、実機で空になった）', () => {
  const bot = { entity: { position: new Vec3(0, 64, 0) }, registry: { biomes: { 21: { name: 'forest' } } }, blockAt: () => ({ biome: { id: 21, name: '' } }) }
  assert.equal(biomeName(bot), 'forest')
  assert.equal(biomeName({ ...bot, registry: { biomes: {}, biomesArray: [] } }), null)
})
