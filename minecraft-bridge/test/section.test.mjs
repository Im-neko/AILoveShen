// 縦の断面 cross_section（設計書 190）: 湖の岸の高さが分かる。岩の中の物（鉱石、砂利）は言わない
import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { crossSection } from '../src/section.mjs'

const { Vec3 } = vec3Pkg
const solid = (name) => ({ name, boundingBox: 'block' })
const air = { name: 'air', boundingBox: 'empty' }
const water = { name: 'water', boundingBox: 'empty' }

// x が増える東向きの断面: 0〜2 は y=64 の地面、3〜7 は湖（水面 y=63、底 y=59）、8 から先は y=67 の岸
function lake (extra = () => null) {
  const ground = (x) => (x <= 2 ? 64 : x <= 7 ? 59 : 67)
  const blockAt = (p) => {
    const e = extra(p)
    if (e) return e
    const g = ground(p.x)
    if (p.y > g + (p.x >= 3 && p.x <= 7 ? 4 : 0) && !(p.x >= 3 && p.x <= 7)) return air
    if (p.x >= 3 && p.x <= 7) {
      if (p.y > 63) return air
      if (p.y >= 60) return water
      return solid(p.y === 59 ? 'sand' : 'stone')
    }
    if (p.y > g) return air
    return solid(p.y === g ? 'grass_block' : 'stone')
  }
  return { entity: { position: new Vec3(0.5, 65, 0.5), yaw: -Math.PI / 2 }, blockAt }
}

test('東へ見ると、湖の水面と深さ、対岸が水面より何ブロック高いかが分かる', () => {
  const r = crossSection(lake(), { direction: 'east', length: 12, up: 4, down: 8 })
  assert.equal(r.direction, 'east')
  assert.match(r.summary, /0-2 blocks east: ground at y=64/)
  assert.match(r.summary, /3-7 blocks east: water, surface y=63, 4 deep/)
  assert.match(r.summary, /8-11 blocks east: ground at y=67 \(the bank is 4 above the water surface\)/)
  const row63 = r.rows.find((l) => l.startsWith('y=  63'))
  assert.match(row63, /~~~~~/)
  assert.ok(r.rows.some((l) => l.includes('@')), 'you are marked')
  assert.equal(r.legend['~'], 'water')
})

test('向きを省くと、見ている向き（yaw）で断面を取る', () => {
  const bot = lake()
  assert.equal(crossSection(bot, { length: 3 }).direction, 'east')
  bot.entity.yaw = 0
  assert.equal(crossSection(bot, { length: 3 }).direction, 'north')
  bot.entity.yaw = Math.PI
  assert.equal(crossSection(bot, { length: 3 }).direction, 'south')
})

test('岩の中は中身を言わない: 鉱石も砂利も、空気や水に面していなければ # で、見える面だけ名前が出る', () => {
  // y=60〜62 の x=1..6 は岩。中に鉄鉱石と砂利が埋まっている。x=0 の列は空気（洞窟）で、その壁だけが見える
  const buried = (p) => {
    if (p.y < 60 || p.y > 62) return null
    if (p.x === 0) return air
    if (p.x === 3 && p.y === 61) return solid('iron_ore')
    if (p.x === 4 && p.y === 61) return solid('gravel')
    if (p.x === 1 && p.y === 61) return solid('diamond_ore') // 洞窟の壁に露出している
    return solid('stone')
  }
  const bot = { entity: { position: new Vec3(0.5, 61, 0.5), yaw: -Math.PI / 2 }, blockAt: (p) => buried(p) ?? air }
  const r = crossSection(bot, { direction: 'east', length: 7, up: 1, down: 2 })
  const row61 = r.rows.find((l) => l.startsWith('y=  61'))
  assert.equal(row61.slice(7), '@O#####', row61)
  assert.equal(r.legend.O, 'diamond_ore')
  assert.ok(!JSON.stringify(r).includes('iron_ore') && !JSON.stringify(r).includes('gravel'), 'buried blocks are not named')
})

test('読み込まれていない所は ? で、表面の言葉にも not loaded と出る。遠い起点と知らない向きは理由つきの断り', () => {
  const bot = { entity: { position: new Vec3(0.5, 65, 0.5), yaw: 0 }, blockAt: (p) => (p.x >= 2 ? null : air) }
  const r = crossSection(bot, { direction: 'east', length: 4, up: 0, down: 1 })
  assert.match(r.rows[0], /\?\?$/)
  assert.match(r.summary, /not loaded/)
  assert.throws(() => crossSection(bot, { x: 100 }), /go closer/)
  assert.throws(() => crossSection(bot, { direction: 'up' }), /unknown direction/)
})

test('長さと深さは上限で切る', () => {
  const r = crossSection(lake(), { direction: 'east', length: 999, up: 999, down: 999 })
  assert.equal(r.rows.length, 12 + 24 + 1)
  assert.equal(r.rows[0].slice(7).length, 24)
})
