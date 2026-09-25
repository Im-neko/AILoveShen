import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { Knowledge } from '../src/knowledge.mjs'
import { stationSpot } from '../src/primitives.mjs'
import { ground } from '../src/candidates.mjs'

const { Vec3 } = vec3Pkg
const v = (x, y, z) => new Vec3(x, y, z)
const md = minecraftData('1.21.4')
const k = new Knowledge(md)
const AIR = { name: 'air', boundingBox: 'empty' }
const STONE = { name: 'stone', boundingBox: 'block' }

// ボットは 0,70,0。`floor(x, z)` より下は固体、そこから上は空気
const bot = (floor) => ({
  entity: { position: v(0.5, 70, 0.5) },
  entities: {},
  time: { timeOfDay: 1000 },
  health: 20,
  food: 20,
  heldItem: null,
  registry: md,
  inventory: { items: () => [{ name: 'crafting_table', count: 1 }], emptySlotCount: () => 20 },
  blockAt: (p) => (p.y < floor(p.x, p.z) ? STONE : AIR)
})
const state = { home: null, plan: null, unreachableDrops: new Set(), memory: { chests: [], furnaces: [], places: [] } }
const placeLeaf = { leaves: [{ kind: 'place', item: 'crafting_table' }], blocked: [] }

test('作業台などは、平地なら 2 ブロック先、坂なら 1 ブロック上か下に置く', () => {
  assert.deepEqual(stationSpot(bot(() => 70), state), v(-2, 70, 0))
  // 周り一面が 1 段高い: 同じ高さの決まった輪だけを見て何も見つからなかった（iron run）
  assert.deepEqual(stationSpot(bot((x, z) => (Math.abs(x) + Math.abs(z) > 1 ? 71 : 70)), state), v(-2, 71, 0))
})

test('置く場所がなければ候補に出さず、代わりに理由を示す', () => {
  const walled = bot((x, z) => (Math.abs(x) + Math.abs(z) > 1 ? 75 : 70)) // 穴の中
  assert.equal(stationSpot(walled, state), null)
  const { candidates, withheld } = ground(walled, state, k, {}, placeLeaf)
  assert.ok(!candidates.some((c) => c.verb === 'place_station'), candidates.map((c) => c.id).join())
  assert.match(withheld, /no room here to place the crafting_table/)

  const open = ground(bot(() => 70), state, k, {}, placeLeaf)
  assert.ok(open.candidates.some((c) => c.id === 'place crafting_table nearby'))
  assert.equal(open.withheld, null)
})
