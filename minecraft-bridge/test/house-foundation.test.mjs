import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { BuildPlan } from '../src/build.mjs'

const { Vec3 } = vec3Pkg

// 3x3 の家（壁の 8 マスと、中は空き）。原点 (10,70,10)、足元は y=69
function plan () {
  const blocks = []
  for (let x = 0; x < 3; x++) for (let z = 0; z < 3; z++) if (x !== 1 || z !== 1) blocks.push({ x, y: 0, z, block: 'planks' })
  const p = new BuildPlan({ blocks, width: 3, depth: 3, height: 2 })
  p.origin = new Vec3(10, 70, 10)
  return p
}
const SOLID = { name: 'stone', boundingBox: 'block' }
const AIR = { name: 'air', boundingBox: 'empty' }
const WALLS = {}
for (let x = 0; x < 3; x++) for (let z = 0; z < 3; z++) if (x !== 1 || z !== 1) WALLS[`${10 + x},70,${10 + z}`] = { name: 'oak_planks', boundingBox: 'block' }
// 地面: 足元から depth(x, z) 段抜けている（0 なら抜けていない）。それより下は固い
const world = (depth, extra = {}) => ({
  blockAt: (pos) => {
    const key = `${pos.x},${pos.y},${pos.z}`
    if (extra[key] !== undefined) return extra[key]
    if (WALLS[key]) return WALLS[key]
    if (pos.x < 10 || pos.x > 12 || pos.z < 10 || pos.z > 12) return AIR
    if (pos.y >= 70) return AIR
    return 69 - pos.y < depth(pos.x - 10, pos.z - 10) ? AIR : SOLID
  }
})

test('足元が固ければ、土を埋めるマスはなく、壁がそろえば完成', () => {
  const p = plan()
  const s = p.status(world(() => 0))
  assert.deepEqual([s.foundation, s.ruined, s.complete], [0, null, true])
})

test('足元が抜けた列は、固い地面に届くまで土で埋める。下から、壁より先', () => {
  const p = plan()
  const w = world((x, z) => (x === 0 && z === 0 ? 3 : 0))
  const pending = p.pending(w)
  assert.deepEqual(pending.slice(0, 3).map((b) => [b.x, b.y, b.z, b.block, b.foundation]), [
    [0, -3, 0, 'dirt', true], [0, -2, 0, 'dirt', true], [0, -1, 0, 'dirt', true]
  ])
  assert.equal(p.status(w).foundation, 3)
  assert.equal(p.status(w).complete, false)
  assert.deepEqual(p.materialsNeeded(w), { dirt: 3 })
  // 置く先の世界座標は原点からの相対
  assert.deepEqual({ ...p.worldPos(pending[0]) }, { x: 10, y: 67, z: 10 })
})

test('土のマスは、固くなれば置いたことになる。読み込まれていなければ埋まっているとみなす', () => {
  const p = plan()
  const cell = { x: 0, y: -1, z: 0, block: 'dirt', foundation: true }
  assert.equal(p.isPlaced(world((x, z) => (x === 0 && z === 0 ? 1 : 0)), cell), false)
  assert.equal(p.isPlaced(world(() => 0), cell), true)
  assert.equal(p.isPlaced({ blockAt: () => null }, cell), true)
})

test('読み込まれていない家は、足元を直さない（離れても完成のまま）', () => {
  const p = plan()
  for (const b of p.blocks) p.seen.add(`${10 + b.x},70,${10 + b.z}`)
  const s = p.status({ blockAt: () => null })
  assert.deepEqual([s.foundation, s.ruined, s.complete], [0, null, true])
})

test('水の列は、水の下の地面まで土で埋める（水を置き換える）', () => {
  const p = plan()
  const water = { name: 'water', boundingBox: 'empty' }
  const w = world(() => 0, { '12,69,12': water })
  const fills = p.foundation(w).fills
  assert.deepEqual(fills.map((b) => [b.x, b.y, b.z]), [[2, -1, 2]])
})

test('深さ 8 までに地面がない列と、溶岩の列は直せない（別の場所に建て直す）', () => {
  const p = plan()
  const deep = world((x, z) => (x === 2 && z === 2 ? 99 : 0))
  assert.match(p.ruined(deep), /cannot be filled \(1 columns: no solid ground within 8 blocks\)/)
  assert.equal(p.status(deep).complete, false)
  const lava = world(() => 0, { '10,69,10': { name: 'lava', boundingBox: 'empty' } })
  assert.match(p.ruined(lava), /lava/)
})

test('埋めるのが家の全ブロックより多いなら、直さない', () => {
  const p = plan()
  const w = world(() => 3) // 9 列 x 3 段 = 27 > 家の 8 ブロック
  assert.match(p.ruined(w), /takes 27 blocks, more than the house itself \(8\)/)
})

test('建て直し: 原点と見たブロックの記憶を捨てて、次の置く番で敷地を探し直す', () => {
  const p = plan()
  p.seen.add('10,70,10')
  p.siteSearchFailedAt = new Vec3(0, 0, 0)
  p.relocate()
  assert.deepEqual([p.origin, p.seen.size, p.siteSearchFailedAt], [null, 0, null])
  assert.equal(p.status({ blockAt: () => null }).ruined, null)
})

test('名前付きの建物は足元を見ない', () => {
  const p = new BuildPlan({ blocks: [{ x: 0, y: 0, z: 0, block: 'planks' }], width: 1, depth: 1, height: 1, kind: 'build' })
  p.origin = new Vec3(10, 70, 10)
  assert.deepEqual(p.foundation(world(() => 5)), { fills: [], unfillable: [], tooMany: false })
})

test('built の判定: 足元が抜けていれば未達で土を埋める行を出し、直せなければ敷地を選び直す', async () => {
  const { default: minecraftData } = await import('minecraft-data')
  const { Knowledge } = await import('../src/knowledge.mjs')
  const { checkConditions } = await import('../src/goals.mjs')
  const k = new Knowledge(minecraftData('1.21.4'))
  const worldView = { inventory: {}, blocks: {}, mobs: {}, table: null, unlocked: () => true, dig: () => [], hunt: () => [] }
  const botWith = (w) => ({ ...w, time: { timeOfDay: 1000 }, entity: { position: { floored: () => ({ x: 0, y: 70, z: 0 }) } }, inventory: { items: () => [] } })
  const p = plan()
  const [crater] = checkConditions([{ predicate: 'built' }], botWith(world((x, z) => (x === 0 && z === 0 ? 2 : 0))), { home: null, plan: p, builds: {}, goal: null }, k, worldView)
  assert.equal(crater.met, false)
  assert.ok(crater.lines.some((l) => /ground under the house has fallen away: 2 blocks of dirt/.test(l)))
  assert.ok(p.origin, 'a crater that can be filled keeps the site')
  const [lost] = checkConditions([{ predicate: 'built' }], botWith(world((x, z) => (x === 2 && z === 2 ? 99 : 0))), { home: null, plan: p, builds: {}, goal: null }, k, worldView)
  assert.equal(lost.met, false)
  assert.equal(p.origin, null)
})
