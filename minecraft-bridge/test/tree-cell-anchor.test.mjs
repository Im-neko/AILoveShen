// 植林場のマスが、設計のあとに固い地面になっていたら、苗木を置ける1つ上に直す（2026-10-08）
import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { layoutStatus, ensureTreeCells, workCell } from '../src/facilities.mjs'

const { Vec3 } = vec3Pkg

function botWith (blocks = {}) {
  return {
    entity: { position: new Vec3(50.5, 68, -192.5) },
    blockAt: (p) => {
      const q = new Vec3(Math.floor(p.x), Math.floor(p.y), Math.floor(p.z))
      const name = blocks[`${q.x},${q.y},${q.z}`] ?? (q.y === 67 ? 'grass_block' : q.y < 67 ? 'dirt' : 'air')
      return { name, boundingBox: /air|tulip|birch_sapling|short_grass/.test(name) ? 'empty' : 'block', position: q }
    }
  }
}
const grove = () => ({
  name: 'grove',
  kind: 'tree_farm',
  box: { min: { x: 45, y: 67, z: -199 }, max: { x: 50, y: 68, z: -194 } },
  layout: { sapling: 'birch_sapling', spacing: 5, cells: [{ x: 45, y: 68, z: -194 }, { x: 50, y: 67, z: -194 }, { x: 50, y: 68, z: -199 }] }
})

test('設計のマスが地面になっていたら、1つ上に直して、植える所として数える。空気のマスは動かさない', () => {
  const fac = grove()
  const bot = botWith()
  const s = layoutStatus(bot, fac)
  assert.deepEqual(fac.layout.cells.map((c) => c.y), [68, 68, 68])
  assert.deepEqual(s.plant.map((p) => `${p.x},${p.y},${p.z}`).sort(), ['45,68,-194', '50,68,-194', '50,68,-199'])
  // 何度呼んでも同じ
  assert.equal(ensureTreeCells(bot, fac), false)
  assert.deepEqual(fac.layout.cells.map((c) => c.y), [68, 68, 68])
  // 次に作業するマスとして出る
  assert.ok(workCell(bot, { facilities: { grove: fac } }, 'plant', { sapling: 'birch_sapling' }))
})

test('上に木・苗木があるなら、そのマスに植わった木なので1つ上に直し、済みと数える', () => {
  const fac = grove()
  const bot = botWith({ '50,68,-194': 'birch_log', '50,69,-194': 'birch_log' })
  const s = layoutStatus(bot, fac)
  assert.deepEqual(fac.layout.cells.map((c) => c.y), [68, 68, 68])
  assert.equal(s.done, 1)
  assert.ok(!s.plant.some((p) => p.x === 50 && p.z === -194))
  const sapling = grove()
  ensureTreeCells(botWith({ '50,68,-194': 'birch_sapling' }), sapling)
  assert.equal(sapling.layout.cells[1].y, 68)
})

test('上が地面・石などの固いブロックなら動かさない。植えた苗木・畑も動かさない。草花の上は空いているとみなす', () => {
  const fac = grove()
  // 上が石: 動かさない。苗木が植わったマスは地面でないので動かさない
  const blocked = botWith({ '50,68,-194': 'stone', '45,68,-194': 'birch_sapling' })
  ensureTreeCells(blocked, fac)
  assert.deepEqual(fac.layout.cells.map((c) => c.y), [68, 67, 68])
  const flower = grove()
  ensureTreeCells(botWith({ '50,68,-194': 'white_tulip' }), flower)
  assert.equal(flower.layout.cells[1].y, 68)
  // 畑は対象外
  const farm = { name: 'f', kind: 'farm', layout: { crop: 'wheat', cells: [{ x: 1, y: 67, z: 1 }] } }
  assert.equal(ensureTreeCells(botWith(), farm), false)
  assert.equal(farm.layout.cells[0].y, 67)
})

test('読み込まれていないマスは動かさない', () => {
  const fac = grove()
  const bot = botWith()
  const original = bot.blockAt
  bot.blockAt = (p) => (Math.floor(p.y) === 68 && Math.floor(p.x) === 50 && Math.floor(p.z) === -194 ? null : original(p))
  assert.equal(ensureTreeCells(bot, fac), false)
  assert.equal(fac.layout.cells[1].y, 67)
})
