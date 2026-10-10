// 建物の設計の点検: 外からつながらない閉じた空洞（2026-10-10: 塔の 3x3x1 の空洞が、全面の丸石の床と天井に挟まれ、入る道も
// 出る道もなかった）。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { sealedPockets, MIN_SEALED_POCKET } from '../src/builds.mjs'
import { BuildPlan } from '../src/build.mjs'

// 箱 w x d x h。solid(x, y, z) が真のマスを cobblestone にする
function design (w, d, h, solid, doors = []) {
  const blocks = []
  for (let x = 0; x < w; x++) for (let y = 0; y < h; y++) for (let z = 0; z < d; z++) {
    if (solid(x, y, z)) blocks.push({ x, y, z, block: 'cobblestone' })
  }
  for (const [x, y, z] of doors) blocks.push({ x, y, z, block: 'door' })
  return new BuildPlan({ blocks, width: w, depth: d, height: h, kind: 'build' })
}

test('全面の床と天井に挟まれた空洞は、閉じた空洞と数える（塔の 3x3x1）', () => {
  // 5x5x5 の塊で、中の 3x3 の層 y=2 だけが空洞
  const tower = design(5, 5, 5, (x, y, z) => !(y === 2 && x >= 1 && x <= 3 && z >= 1 && z <= 3))
  const r = sealedPockets(tower)
  assert.equal(r.count, 9)
  assert.deepEqual(r.first, { x: 1, y: 2, z: 1 })
  assert.ok(r.count >= MIN_SEALED_POCKET)
})

test('ドアや窓の隙間で外とつながる部屋は閉じていない。屋根のない箱もそう', () => {
  const room = (x, y, z) => x === 0 || x === 4 || z === 0 || z === 4 || y === 0 || y === 4 // 5x5x5 の殻
  assert.equal(sealedPockets(design(5, 5, 5, room)).count, 27, '殻だけは閉じている')
  const withDoor = design(5, 5, 5, (x, y, z) => room(x, y, z) && !(x === 2 && z === 0 && y >= 1 && y <= 2), [[2, 1, 0], [2, 2, 0]])
  assert.equal(sealedPockets(withDoor).count, 0)
  const window = design(5, 5, 5, (x, y, z) => room(x, y, z) && !(x === 0 && z === 2 && y === 2))
  assert.equal(sealedPockets(window).count, 0, '窓の隙間')
  const open = design(5, 5, 5, (x, y, z) => room(x, y, z) && y < 4)
  assert.equal(sealedPockets(open).count, 0, '屋根がない')
})

test('1〜2 マスの小さな隙間は数えない（壁の中の1マスなど）', () => {
  const solid = design(5, 5, 5, (x, y, z) => !(x === 2 && y === 2 && z === 2))
  assert.equal(sealedPockets(solid).count, 1)
  assert.ok(sealedPockets(solid).count < MIN_SEALED_POCKET)
})

test('registerBuild は、閉じた空洞のある設計を、理由つきで差し戻す', async () => {
  const { registerBuild, BuildError } = await import('../src/builds.mjs')
  const blocks = []
  for (let x = 0; x < 5; x++) for (let y = 0; y < 5; y++) for (let z = 0; z < 5; z++) {
    if (!(y === 2 && x >= 1 && x <= 3 && z >= 1 && z <= 3)) blocks.push({ x, y, z, block: 'cobblestone' })
  }
  const state = { home: { min: { x: 0, y: 70, z: 0 }, max: { x: 2, y: 70, z: 2 } }, builds: {} }
  assert.throws(
    () => registerBuild({}, state, { name: 'tower', blocks, size: { width: 5, depth: 5, height: 5 }, anchor: 'near_home' }),
    (e) => e instanceof BuildError && /closes in 9 empty cells.*every room an opening/.test(e.message)
  )
})
