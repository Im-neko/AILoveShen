import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { walkableRegion, rockBetween } from '../src/reach.mjs'

const { Vec3 } = vec3Pkg
const v = (x, y, z) => new Vec3(x, y, z)

// 石の地面（y < 64）。open(x, y, z) が真のマスだけが空気で、あとは石。unloaded の中は未読み込み
function world ({ open = () => false, unloaded = () => false, at = v(0.5, 64, 0.5) } = {}) {
  const entity = { position: at, onGround: true, isInWater: false }
  return {
    entity,
    blockAt: (p) => {
      if (unloaded(p)) return null
      if (open(p.x, p.y, p.z)) return { name: 'air', boundingBox: 'empty', position: p }
      return { name: 'stone', boundingBox: 'block', position: p }
    }
  }
}
// 地面の上で、-3..3 の四角の 3 マス高い部屋の外側は全部岩
const room = (x, y, z) => y >= 64 && y <= 66 && Math.abs(x) <= 3 && Math.abs(z) <= 3
const outdoors = (x, y, z) => y >= 64

test('岩に囲まれた小部屋: 歩ける範囲が閉じていて、遠い目標までは岩の向こう', () => {
  const bot = world({ open: room })
  const region = walkableRegion(bot, v(0, 64, 0))
  assert.equal(region.open, false)
  assert.equal(region.cells.size, 49)
  const r = rockBetween(bot, v(0, 64, 12))
  assert.equal(r.cells, 49)
  assert.equal(r.gap, 9)
})

test('目標が部屋のすぐ外（掘れば届く近さ）なら断らない', () => {
  assert.equal(rockBetween(world({ open: room }), v(0, 64, 6)), null)
})

test('外に出られる（空が開けている）なら、遠い目標でも断らない', () => {
  assert.equal(rockBetween(world({ open: outdoors }), v(0, 64, 40)), null)
})

test('部屋の壁に穴があって外へ出られるなら断らない', () => {
  const open = (x, y, z) => room(x, y, z) || (x === 4 && y >= 64 && y <= 65 && z === 0) || (y >= 64 && y <= 65 && x > 4 && x < 60 && z === 0)
  assert.equal(rockBetween(world({ open }), v(30, 64, 0)), null)
})

test('1 段の階段は上れる、降りられる', () => {
  // x 0..3 は y=64、x 4..7 は y=65 の床（その下は石）
  const open = (x, y, z) => z === 0 && x >= 0 && x <= 7 && y >= (x <= 3 ? 64 : 65) && y <= 68
  const region = walkableRegion(world({ open }), v(0, 64, 0))
  assert.ok(region.cells.has('7,65,0') || [...region.cells.values()].some((p) => p.x === 7 && p.y === 65))
  const down = walkableRegion(world({ open, at: v(7.5, 65, 0.5) }), v(7, 65, 0))
  assert.ok([...down.cells.values()].some((p) => p.x === 0 && p.y === 64))
})

test('2 段の壁は上れない', () => {
  const open = (x, y, z) => z === 0 && x >= 0 && x <= 7 && y >= (x <= 3 ? 64 : 66) && y <= 69
  const region = walkableRegion(world({ open }), v(0, 64, 0))
  assert.ok(![...region.cells.values()].some((p) => p.x >= 4))
})

test('読み込まれていない所に届くなら、閉じているとは言えない', () => {
  const bot = world({ open: room, unloaded: (p) => p.x === 4 })
  assert.equal(walkableRegion(bot, v(0, 64, 0)).open, true)
  assert.equal(rockBetween(bot, v(0, 64, 12)), null)
})

test('水の中や船に乗っているときは調べない', () => {
  const bot = world({ open: room })
  assert.equal(rockBetween(bot, v(0, 64, 12), true), null)
  bot.entity.isInWater = true
  assert.equal(rockBetween(bot, v(0, 64, 12)), null)
})

test('扉は通れる', () => {
  const bot = world({ open: (x, y, z) => room(x, y, z) || (y >= 64 && y <= 65 && x > 3 && x < 40 && z === 0) })
  const orig = bot.blockAt
  bot.blockAt = (p) => (p.x === 4 && p.z === 0 && p.y >= 64 && p.y <= 65 ? { name: 'oak_door', boundingBox: 'block', position: p } : orig(p))
  assert.equal(rockBetween(bot, v(30, 64, 0)), null)
})
