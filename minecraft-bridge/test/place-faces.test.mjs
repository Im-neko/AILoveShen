// 置くときに、ドアなど使うブロックに寄りかからない（2026-10-10 21:2x: 戸口の真上のマスに板を置くとき、下のドアを基準にして
// ドアを開け閉めし続けた。サーバーは「置けない」と断り、同じ手が 55 回続いた）。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import prismarineBlock from 'prismarine-block'
import pathfinderPkg from 'mineflayer-pathfinder'
import { placeFaces, INTERACTIVE } from '../src/place-faces.mjs'

const { Vec3 } = vec3Pkg
const { goals } = pathfinderPkg
const Block = prismarineBlock('1.21.4')

// 戸口 (0, 79..80, 0) の真上 (0, 81, 0) に板を置く。西 (-1,81,0) と南 (0,81,1) は板（壁）、下はドアの上半分
function world (extra = {}) {
  const set = new Map(Object.entries({
    '0,79,0': 'oak_door', '0,80,0': 'oak_door', '-1,81,0': 'oak_planks', '0,81,1': 'oak_planks', ...extra
  }))
  const blockAt = (p) => {
    const q = new Vec3(Math.floor(p.x), Math.floor(p.y), Math.floor(p.z))
    const b = Block.fromProperties(set.get(`${q.x},${q.y},${q.z}`) ?? (q.y < 79 ? 'stone' : 'air'), {}, 0)
    b.position = q
    return b
  }
  return { blockAt, getBlock: blockAt }
}
const target = new Vec3(0, 81, 0)

test('placeFaces: 使うブロック（ドア・チェスト・作業台）の面は外す', () => {
  const w = world({ '1,81,0': 'chest' })
  const faces = placeFaces(w, target).map((f) => `${f.x},${f.y},${f.z}`)
  assert.deepEqual(faces, ['0,1,0', '0,0,-1', '0,0,1', '-1,0,0'])
  assert.ok(INTERACTIVE.test('oak_door') && INTERACTIVE.test('chest') && !INTERACTIVE.test('oak_planks'))
})

test('既定の GoalPlaceBlock は下のドアを基準に選ぶ。faces を絞ると、壁の板を基準に選ぶ', () => {
  const w = world()
  const eye = new Vec3(0.5, 80.6, -1.5)
  const bad = new goals.GoalPlaceBlock(target, w, { range: 5, LOS: false })
  assert.ok(bad.getFaceAndRef(eye).ref.equals(new Vec3(0, 80, 0)), '既定はドア（これが開け閉めの原因）')
  const good = new goals.GoalPlaceBlock(target, w, { range: 5, LOS: false, faces: placeFaces(w, target) })
  const fr = good.getFaceAndRef(eye)
  assert.ok(fr, '壁の板で置ける')
  assert.notEqual(w.blockAt(fr.ref).name, 'oak_door')
})

test('基準にできる面がすべて使うブロックなら、置く先を見つけない（ドアを開け閉めしない）', () => {
  const w = world({ '-1,81,0': 'chest', '0,81,1': 'crafting_table' })
  const goal = new goals.GoalPlaceBlock(target, w, { range: 5, LOS: false, faces: placeFaces(w, target) })
  assert.equal(goal.getFaceAndRef(new Vec3(0.5, 80.6, -1.5)), null)
})
