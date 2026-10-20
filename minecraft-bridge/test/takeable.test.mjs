import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { takeRefusal, refusalText } from '../src/takeable.mjs'
import { memoryView, mapFind } from '../src/worldmap-places.mjs'
import { WorldMap, makeClassifier, AIR } from '../src/worldmap.mjs'
import { registerFacility } from '../src/facilities.mjs'
import { newMemory } from '../src/memory.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')
const v = (x, y, z) => new Vec3(x, y, z)
const NOW = 100 * 1200
const me = v(0, 64, 0)

// 採掘場（箱 8..15 × 0..7、底 40）と、その階段の柱・採掘場と関係ない所に鉄鉱石を置いた世界
function world () {
  const cls = makeClassifier(md)
  const worldMap = new WorldMap()
  const state = { worldMap, mapCells: cls, memory: newMemory(), plan: null, placed: {}, builds: {}, formerHomes: [], home: { inside: v(0, 64, 0), min: v(-1, 64, -1), max: v(1, 64, 1), door: v(0, 64, 2), outside: v(0, 64, 3) } }
  const flat = { registry: md, entity: { position: v(10, 64, 0) }, inventory: { items: () => [], emptySlotCount: () => 30 }, entities: {}, findBlock: () => null, time: { timeOfDay: 1000, age: 0 }, findBlocks: () => [], blockAt: (p) => { const name = p.y > 63 ? 'air' : p.y === 63 ? 'grass_block' : 'stone'; return { name, boundingBox: p.y > 63 ? 'empty' : 'block', position: p } } }
  const fac = registerFacility(flat, state, 'mine_1', { kind: 'mine', boxes: [{ min_x: 8, min_z: 0, max_x: 15, max_z: 7 }], design: { bottom_y: 40, tunnels: 2, tunnel_length: 8 } })
  state.facilities = { mine_1: fac }
  const put = (x, y, z) => { worldMap.set(x, y, z, cls.cellOf('iron_ore'), 90); worldMap.set(x, y + 1, z, AIR, 90) }
  return { state, put, pillar: { x: fac.box.min.x + 1, y: 50, z: fac.box.min.z + 1 } }
}

test('階段の柱の鉄鉱石は、掘ってよくない理由が mine_body で、dig の断りの文になる', () => {
  const { state, pillar } = world()
  const r = takeRefusal(state, pillar, 'iron_ore')
  assert.equal(r.kind, 'mine_body')
  assert.match(refusalText(r, 'iron_ore', '1,2,3'), /^will not dig iron_ore at 1,2,3: it is the pillar or a step of a mine staircase.*Dig the rock beside the mine instead$/)
  assert.equal(takeRefusal(state, pillar, 'iron_ore', { demolish: true }), null)
  assert.equal(takeRefusal(state, { x: -30, y: 66, z: 0 }, 'iron_ore'), null)
})

test('覚えている場所: 柱の鉄鉱石だけの区域は出さず、同じ区域に掘れる鉄鉱石があればそちらを出す', () => {
  const only = world()
  only.put(only.pillar.x, only.pillar.y, only.pillar.z)
  assert.equal(memoryView(only.state, me, NOW, ['iron_ore']).places.iron_ore, undefined)
  const both = world() // 引いた結果は数秒のあいだ覚えられる: 別の世界で
  both.put(both.pillar.x, both.pillar.y, both.pillar.z)
  both.put(4, 66, 4) // 同じ 16x16 の区域の、採掘場と関係ない鉄鉱石
  const rep = memoryView(both.state, me, NOW, ['iron_ore']).places.iron_ore
  assert.deepEqual(rep.map((p) => [p.x, p.z]), [[4, 4]])
  assert.deepEqual(mapFind(both.state, 'iron_ore', me, 96, { ticksNow: NOW }).map((o) => o.x), [4])
})
