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

// ---- 立った区域のバイオームを覚え、探す方向の手がかりにする（設計書 174）
import { newMemory, remember, frontierBiome, biomesSeen, summarizeMemory } from '../src/memory.mjs'

const bearing = (a, b) => (Math.abs(b.x - a.x) >= Math.abs(b.z - a.z) ? (b.x > a.x ? 'E' : 'W') : (b.z > a.z ? 'S' : 'N'))

test('立った区域のバイオームを覚える。地下（null）は覚えない', () => {
  const m = newMemory()
  remember(m, [], { x: 8, y: 64, z: 8 }, 100, 'plains')
  remember(m, [], { x: 40, y: 64, z: 8 }, 101, 'meadow')
  remember(m, [], { x: 72, y: 20, z: 8 }, 102, null)
  assert.deepEqual(m.biomes, { '0,0': 'plains', '2,0': 'meadow' })
  remember(m, [], { x: 9, y: 64, z: 9 }, 103, 'forest') // 同じ区域の新しい記録で上書き
  assert.equal(m.biomes['0,0'], 'forest')
  assert.deepEqual(newMemory().biomes, {})
  // 古い保存（biomes のない記憶）でも落ちない
  const old = { places: {}, explored: {}, deaths: [], chests: {}, furnaces: {}, sites: {} }
  remember(old, [], { x: 8, y: 64, z: 8 }, 1, 'desert')
  assert.deepEqual(old.biomes, { '0,0': 'desert' })
})

test('見たバイオームごとの一番近い区域を、近い順に。要約にも入る', () => {
  const m = newMemory()
  remember(m, [], { x: 8, y: 64, z: 8 }, 1, 'plains') // 区域 0,0
  remember(m, [], { x: -120, y: 64, z: 8 }, 2, 'meadow') // -8,0
  remember(m, [], { x: -136, y: 64, z: 8 }, 3, 'meadow') // -9,0
  remember(m, [], { x: 8, y: 64, z: -200 }, 4, 'forest') // 0,-13
  const me = { x: 8, z: 8 }
  const seen = biomesSeen(m, me, bearing)
  assert.deepEqual(seen.map((b) => b.biome), ['plains', 'meadow', 'forest'])
  assert.deepEqual(seen[1], { biome: 'meadow', regions: 2, direction: 'W', distance_m: 128 })
  assert.deepEqual(summarizeMemory(m, { x: 8, y: 64, z: 8 }, 10, bearing).biomes, seen)
})

test('その向きの、まだ見ていない土地の手前のバイオーム', () => {
  const m = newMemory()
  remember(m, [], { x: 8, y: 64, z: 8 }, 1, 'plains')
  remember(m, [], { x: 8, y: 64, z: -40 }, 2, 'meadow') // 北へ 3 区域先
  const from = { x: 8, z: 8 }
  assert.equal(frontierBiome(m, from, 0, -1, 48), 'meadow')
  assert.equal(frontierBiome(m, from, 0, -1, 80), 'meadow') // 手前から戻って、記録のある最初の区域
  assert.equal(frontierBiome(m, from, 1, 0, 64), null) // 東には記録がない
})
