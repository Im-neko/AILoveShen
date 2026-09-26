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

// 室内 3x3（x 101..103、z 1..3、床 y 69）、ドアは北 (102, 70, 0)、ベッドはドアからまっすぐ奥
function room (timeOfDay, extra = {}) {
  const blocks = { '102,70,2': 'red_bed', '102,70,3': 'red_bed', ...extra }
  const wall = (x, z) => x === 100 || x === 104 || z === 0 || z === 4
  return {
    time: { timeOfDay },
    entity: { position: v(102.5, 70, 1.5) },
    entities: {},
    registry: { blocksByName: { crafting_table: { id: 7 } } },
    blockAt: (p) => {
      let name = blocks[`${p.x},${p.y},${p.z}`] ?? 'air'
      if (p.y === 69 || p.y === 73) name = 'oak_planks'
      else if (p.y >= 70 && p.y <= 72 && p.x >= 100 && p.x <= 104 && p.z >= 0 && p.z <= 4 && wall(p.x, p.z) && name === 'air') name = 'oak_planks'
      return { name, position: p, boundingBox: name === 'air' || name.endsWith('_bed') ? 'empty' : 'block' }
    },
    findBlocks: () => Object.entries(blocks).filter(([, n]) => n === 'crafting_table').map(([k]) => v(...k.split(',').map(Number))),
    findBlock: () => null
  }
}
const homeState = () => ({ home: { min: v(101, 70, 1), max: v(103, 70, 3), door: v(102, 70, 0), inside: v(102, 70, 1), outside: v(102, 70, -1), breach: [] } })

test('夜に家の中にいれば、作業台は家の中に置く（夜の待ち時間にクラフトできるように）', async () => {
  const { isInside } = await import('../src/home.mjs')
  const state = homeState()
  const night = stationSpot(room(18000), state, 'crafting_table')
  assert.ok(night && isInside({ entity: { position: night } }, state.home), `inside: ${night}`)
  assert.notEqual(`${night.x},${night.z}`, '102,1') // ドアの内側はふさがない
  assert.ok(!['102,2', '102,3'].includes(`${night.x},${night.z}`)) // ベッドの上ではない
  // 昼は今までどおり家の外（家の中には置かない）
  const day = stationSpot(room(1000), state, 'crafting_table')
  assert.ok(!day || !isInside({ entity: { position: day } }, state.home))
  // かまどは夜でも家の中に置かない
  const furnace = stationSpot(room(18000), state, 'furnace')
  assert.ok(!furnace || !isInside({ entity: { position: furnace } }, state.home))
})

test('夜に家の中にいれば、使える作業台は家の中のものだけ', async () => {
  const { findTable } = await import('../src/primitives.mjs')
  const state = homeState()
  assert.equal(findTable(room(18000, { '98,70,2': 'crafting_table' }), state), null) // 外の作業台
  const indoor = findTable(room(18000, { '98,70,2': 'crafting_table', '101,70,3': 'crafting_table' }), state)
  assert.deepEqual(indoor.position, v(101, 70, 3))
})
