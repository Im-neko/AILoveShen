// 家から出る道（設計書 163）: 玄関のセルに草ブロックが残り、まぐさで高さが 1 マスしかない家の中に閉じ込められ、
// 壁を壊して出ようにも「先に家から出る」ことを求められて、何も効かなかった（2026-10-11）
import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { doorBlocker, leaveRoute } from '../src/exit-route.mjs'
import { needsOutside, ground } from '../src/candidates.mjs'
import { leaveHouse } from '../src/primitives.mjs'
import { Knowledge } from '../src/knowledge.mjs'
import { EXIT_DROP } from '../src/home.mjs'

const { Vec3 } = vec3Pkg
const v = (x, y, z) => new Vec3(x, y, z)
const knowledge = new Knowledge(minecraftData('1.21.4'))
const solid = (name) => ({ name, boundingBox: 'block' })
const air = { name: 'air', boundingBox: 'empty' }

// y 70 の石の上の 5x5 の家（壁は高さ 2、屋根なし）。ドアは (2, 70, 4)。lower: 外の地面を下げる、doorCell: 玄関のセルの中身
function house ({ lower = 0, doorCell = 'oak_door', gap = false, registeredY = 70 } = {}) {
  const home = { door: v(2, registeredY, 4), inside: v(2, registeredY, 3), outside: v(2, registeredY, 5), min: v(1, registeredY, 1), max: v(3, registeredY, 3), bed: null, breach: [] }
  const blockAt = (p) => {
    const outside = p.x < 0 || p.x > 4 || p.z < 0 || p.z > 4
    if (lower && outside && p.y >= 70 - lower && p.y < 70) return air
    if (p.y < 70) return solid('stone')
    if (p.x === 2 && p.z === 4 && p.y === 72) return solid('oak_planks') // まぐさ
    if (p.x === 2 && p.z === 4 && p.y <= 71) return doorCell === 'oak_door' ? { ...solid('oak_door'), getProperties: () => ({ open: false }) } : (p.y === 70 ? solid(doorCell) : air)
    if (gap && p.x === 0 && p.z === 2 && p.y <= 71) return air
    const wall = (p.x === 0 || p.x === 4 || p.z === 0 || p.z === 4) && p.x >= 0 && p.x <= 4 && p.z >= 0 && p.z <= 4
    return wall && p.y <= 71 ? solid('oak_planks') : air
  }
  const entity = { position: v(2.5, 70, 2.5), eyeHeight: 1.62, height: 1.8 }
  const bot = {
    entity, entities: { 0: entity }, blockAt, time: { timeOfDay: 1000 }, health: 20, food: 20, heldItem: null,
    world: { raycast: () => ({ name: 'oak_planks' }) }, inventory: { items: () => [] }
  }
  return { home, bot, blockAt }
}

test('玄関のセルに草ブロックが残っていれば、何がふさいでいるかを言う。ふつうの家は何もふさがない', () => {
  const ok = house()
  assert.equal(doorBlocker(ok.bot, ok.home), '')
  assert.equal(leaveRoute(ok.bot, ok.home).route, 'door')
  const shut = house({ doorCell: 'grass_block' })
  assert.match(doorBlocker(shut.bot, shut.home), /door cell 2,70,4 holds grass_block/)
  // 玄関の道が使えなくても、壁を掘って出られる所があれば、壁から出る
  assert.equal(leaveRoute(shut.bot, shut.home).route, 'wall')
})

test('玄関も壁の出口もなく、閉じていれば stuck（何がふさいでいるかつき）。崖に開いた所なら歩いて出られる（walk）', () => {
  const stuck = house({ doorCell: 'grass_block', lower: EXIT_DROP + 3 })
  const r = leaveRoute(stuck.bot, stuck.home)
  assert.equal(r.route, 'stuck')
  assert.match(r.blocked, /grass_block/)
  const open = house({ doorCell: 'grass_block', lower: EXIT_DROP + 3, gap: true })
  assert.equal(leaveRoute(open.bot, open.home).route, 'walk')
})

test('出られない家から出る手は、先に出ることを求めず、ふさいでいる理由を言う。歩いて出られるなら何もしない', async () => {
  const stuck = house({ doorCell: 'grass_block', lower: EXIT_DROP + 3 })
  await assert.rejects(leaveHouse(stuck.bot, {}, stuck.home, new AbortController().signal), /the way out of the house is shut: the door cell 2,70,4 holds grass_block.*dig it from here/s)
  const open = house({ doorCell: 'grass_block', lower: EXIT_DROP + 3, gap: true })
  await leaveHouse(open.bot, {}, open.home, new AbortController().signal)
})

test('家の中から手が届くブロックを掘る・置く手は、外へ出なくてよい。遠いものや歩く手は今までどおり', () => {
  const { home, bot } = house()
  const dig = (x, y, z) => ({ verb: 'dig', pos: v(x, y, z) })
  assert.equal(needsOutside(dig(0, 70, 2), home, bot), false) // 壁
  assert.equal(needsOutside({ verb: 'place_at', pos: v(4, 70, 2) }, home, bot), false)
  assert.equal(needsOutside(dig(0, 70, 2), home), true) // bot を渡さなければ今までどおり
  assert.equal(needsOutside(dig(10, 70, 10), home, bot), true) // 遠い
  assert.equal(needsOutside({ verb: 'goto', pos: v(0, 70, 2) }, home, bot), true) // 歩く手は外へ出る
})

test('家の中で出口がなければ、閉じ込められた所から出る手（壁を掘る）を出す。出口があれば出さない', () => {
  const snap = { dig: () => [], hunt: () => [] }
  const ids = (h) => ground(h.bot, { home: h.home, plan: null, unreachableDrops: new Set() }, knowledge, snap, { leaves: [] }).candidates.map((c) => c.id)
  // 家が 1 マス高く登録された（玄関と家の中が、実際に立つ高さの 1 つ上）ために、壁の出口も見つからない
  const stuck = house({ doorCell: 'grass_block', registeredY: 71 })
  assert.equal(leaveRoute(stuck.bot, stuck.home).route, 'stuck')
  assert.ok(ids(stuck).some((id) => /dig out of the closed space|climb out of the closed space/.test(id)), ids(stuck).join(' | '))
  const normal = house()
  assert.ok(!ids(normal).some((id) => /closed space/.test(id)), ids(normal).join(' | '))
})
