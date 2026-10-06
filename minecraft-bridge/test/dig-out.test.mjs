// 閉じた所から壁を掘って出る（docs/design/90 §5.1）。水が入った土の小部屋に素手で閉じ込められたとき、
// 出る手がなかった（ベンチマークの cage: 13 手のうち 9 手が壁の外の陸へ泳ぐ手で、毎回失敗した）。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import prismarineBlock from 'prismarine-block'
import { closedSpace } from '../src/reach.mjs'
import { digOutRoute } from '../src/dig-out.mjs'
import { forNeeds } from '../src/candidates.mjs'
import { reachLand } from '../src/land.mjs'
import { PRIMITIVES } from '../src/primitives.mjs'
import { Knowledge } from '../src/knowledge.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')
const Block = prismarineBlock('1.21.4')
const k = new Knowledge(md)

// y 69 までの地面（石）の上に、土の殻（x, z は -2..2、y は 69..74）。中は x, z が -1..1、y 70..73。
// water: 中の y 70, 71 を水にする。thick: 殻の壁の厚さ（1 より大きいと外側へ土を足す）。x, y, z が 24 を超える所は未読み込み
function cage ({ water = false, thick = 1, overrides = {} } = {}) {
  const set = new Map(Object.entries(overrides))
  const blockAt = (p) => {
    const q = new Vec3(Math.floor(p.x), Math.floor(p.y), Math.floor(p.z))
    if (Math.abs(q.x) > 24 || Math.abs(q.z) > 24 || q.y > 90) return null
    const o = set.get(`${q.x},${q.y},${q.z}`)
    let name
    if (o) name = o
    else {
      const inside = Math.abs(q.x) <= 1 && Math.abs(q.z) <= 1 && q.y >= 70 && q.y <= 73
      const shell = Math.abs(q.x) <= 1 + thick && Math.abs(q.z) <= 1 + thick && q.y >= 69 && q.y <= 74
      if (inside) name = water && q.y <= 71 ? 'water' : 'air'
      else if (shell) name = 'dirt'
      else name = q.y <= 69 ? 'stone' : 'air'
    }
    const b = Block.fromProperties(name, {}, 0)
    b.position = q
    return b
  }
  return { set, blockAt }
}

function botIn (w, { at = water0(), items = {}, inWater = false } = {}) {
  const held = { ...items }
  return {
    registry: md,
    entities: {},
    entity: { position: at, onGround: !inWater, yaw: 0, isInWater: inWater },
    time: { timeOfDay: 1000, age: 0 },
    health: 20,
    food: 20,
    blockAt: w.blockAt,
    findBlock: () => null,
    findBlocks: () => [],
    heldItem: null,
    inventory: { items: () => Object.entries(held).filter(([, n]) => n > 0).map(([name, count]) => ({ name, count, type: md.itemsByName[name].id })), slots: [] },
    equip: async () => {},
    unequip: async () => {},
    lookAt: async () => {},
    look: async () => {},
    setControlState: () => {},
    waitForTicks: async () => {},
    dig: async (block) => { const p = block.position; w.set.set(`${p.x},${p.y},${p.z}`, 'air') }
  }
}
const water0 = () => new Vec3(0.5, 70, 0.5)
const state = () => ({ home: null, formerHomes: [], builds: {}, plan: null })
const signal = new AbortController().signal

test('乾いた土の小部屋は閉じた所。壁を 1 枚（足と頭の 2 ブロック）掘って、外の地面に出る道を選ぶ', () => {
  const bot = botIn(cage())
  assert.ok(closedSpace(bot))
  const r = digOutRoute(bot, state())
  assert.equal(r.why, undefined)
  assert.equal(r.cells.length, 2)
  assert.ok(r.cells.every((p) => Math.abs(p.x) === 2 || Math.abs(p.z) === 2), '壁のブロック')
  assert.equal(r.outside.y, 70)
  assert.ok(Math.abs(r.outside.x) === 3 || Math.abs(r.outside.z) === 3)
})

test('水が入った小部屋（水の中）も、同じように壁を掘って出る道がある', () => {
  const bot = botIn(cage({ water: true }), { at: new Vec3(0.5, 71.3, 0.5), inWater: true })
  assert.ok(closedSpace(bot))
  const r = digOutRoute(bot, state())
  assert.equal(r.why, undefined)
  assert.equal(r.cells.length, 2)
})

test('壁が厚ければ、厚い分だけ掘る。DIG_OUT_THICKNESS より厚ければ道がない', () => {
  const thin = digOutRoute(botIn(cage({ thick: 2 })), state())
  assert.equal(thin.cells.length, 4)
  const thick = digOutRoute(botIn(cage({ thick: 6 })), state())
  assert.match(thick.why, /no wall within 4 blocks/)
})

test('大きな水（池や海）を止めている壁は掘らず、別の向きを選ぶ', () => {
  // +x の外は大きな水（x 3..20）。-x、±z の壁を掘る
  const overrides = {}
  for (let x = 3; x <= 20; x++) for (let z = -10; z <= 10; z++) for (let y = 70; y <= 74; y++) overrides[`${x},${y},${z}`] = 'water'
  const bot = botIn(cage({ overrides }))
  const r = digOutRoute(bot, state())
  assert.equal(r.why, undefined)
  assert.ok(r.cells.every((p) => p.x < 2), `+x の壁は掘らない: ${r.cells.map((p) => `${p.x},${p.y},${p.z}`)}`)
  assert.ok(r.cells.every((p) => !(p.x === 2)))
})

test('小さな水（自分の小部屋の水）を止めている壁は掘ってよい', () => {
  const w = cage({ water: true })
  const bot = botIn(w, { at: new Vec3(0.5, 71.3, 0.5), inWater: true })
  // 水面の下の高さ（足の高さ 71）の壁にも水が接する。掘れないと言われないこと
  const b = w.blockAt(new Vec3(2, 71, 0))
  assert.equal(b.name, 'dirt')
  assert.equal(digOutRoute(bot, state()).why, undefined)
})

test('溶岩に接する壁は掘らない。どの向きも溶岩なら、理由を返す', () => {
  const overrides = {}
  for (const [x, z] of [[3, 0], [-3, 0], [0, 3], [0, -3], [3, 1], [3, -1], [-3, 1], [-3, -1], [1, 3], [-1, 3], [1, -3], [-1, -3]]) {
    for (let y = 69; y <= 74; y++) overrides[`${x},${y},${z}`] = 'lava'
  }
  const r = digOutRoute(botIn(cage({ overrides })), state())
  assert.ok(r.why)
  assert.match(r.why, /lava/)
})

test('今の家の壁は掘らない（保護）。壁がすべて保護なら、理由を返す', () => {
  // 家の部屋が x, z -1..1、y 70..73: 壁（x, z = ±2）と床・屋根は家の一部
  const home = { min: new Vec3(-1, 70, -1), max: new Vec3(1, 73, 1), inside: new Vec3(0, 70, 0), door: new Vec3(0, 70, 2), outside: new Vec3(0, 70, 3), breach: [] }
  const r = digOutRoute(botIn(cage()), { ...state(), home })
  assert.ok(r.why, `道がある: ${JSON.stringify(r.cells)}`)
  assert.doesNotMatch(r.why, /no wall within 4 blocks/)
})

test('壁の上に落ちる砂があれば、そのブロックは掘らない。どの壁の上も砂なら、理由を返す', () => {
  const walls = [[2, 0], [-2, 0], [0, 2], [0, -2], [2, 1], [2, -1], [-2, 1], [-2, -1], [1, 2], [-1, 2], [1, -2], [-1, -2]]
  const overrides = {}
  for (const [x, z] of walls) overrides[`${x},72,${z}`] = 'sand'
  // 頭の高さ（71）の壁の上が砂: 足（70）と頭（71）の両方を掘る道は、頭が掘れず成り立たない
  const r = digOutRoute(botIn(cage({ overrides })), state())
  assert.match(r.why, /sand or gravel above it would fall in/)
  // 北の壁だけ砂がなければ、そこから出る
  const only = { ...overrides }
  for (const x of [-1, 0, 1]) delete only[`${x},72,-2`]
  const ok = digOutRoute(botIn(cage({ overrides: only })), state())
  assert.equal(ok.why, undefined)
  assert.ok(ok.cells.every((p) => p.z === -2), '砂のない壁を選ぶ')
})

test('閉じていない所では、道を作らない', () => {
  const w = cage({ overrides: { '2,70,0': 'air', '2,71,0': 'air', '3,70,0': 'air' } })
  const bot = botIn(w)
  assert.equal(closedSpace(bot), null)
  assert.equal(digOutRoute(bot, state()).why, 'not shut in')
})

test('候補: 土も丸石もなければ dig_out（水の中なら urgent）、陸へ泳ぐ手は出さない。土があれば climb_out で dig_out は出さない', () => {
  const wet = botIn(cage({ water: true }), { at: new Vec3(0.5, 71.3, 0.5), inWater: true })
  const c = forNeeds(wet, state(), k)
  const dig = c.find((x) => x.verb === 'dig_out')
  assert.ok(dig, 'dig_out が出る')
  assert.equal(dig.urgent, true)
  assert.equal(dig.blocks, 2)
  assert.equal(c.some((x) => x.verb === 'reach_land'), false, '壁の外の陸へ泳ぐ手は出さない')
  assert.equal(c.some((x) => x.verb === 'climb_out'), false)
  const withDirt = botIn(cage({ water: true }), { at: new Vec3(0.5, 71.3, 0.5), inWater: true, items: { dirt: 4 } })
  const d = forNeeds(withDirt, state(), k)
  assert.ok(d.some((x) => x.verb === 'climb_out'))
  assert.equal(d.some((x) => x.verb === 'dig_out'), false)
})

test('候補: 積んで出るのに失敗した（climbBlockedUntil）あとは、土があっても dig_out を出す。dig_out の失敗のあとは urgent を外す', () => {
  const bot = botIn(cage(), { items: { dirt: 4 } })
  const blocked = { ...state(), climbBlockedUntil: Date.now() + 60000 }
  const a = forNeeds(bot, blocked, k).find((x) => x.verb === 'dig_out')
  assert.ok(a && a.urgent)
  const failed = { ...blocked, digOutBlockedUntil: Date.now() + 60000 }
  const b = forNeeds(bot, failed, k).find((x) => x.verb === 'dig_out')
  assert.ok(b && !b.urgent)
})

test('reach_land: 壁で閉じた所からは、泳いでも着けないと断る（天井を陸とみなして 4 回失敗した）', async () => {
  const bot = botIn(cage({ water: true }), { at: new Vec3(0.5, 71.3, 0.5), inWater: true })
  await assert.rejects(reachLand(bot, signal), /shut in by walls.*dig_out/)
})

test('dig_out: 壁を掘って、外へ出る手前まで進める（歩きの模擬はないので、掘ったところで止まる）', async () => {
  const w = cage()
  const bot = botIn(w)
  const route = digOutRoute(bot, state())
  const st = state()
  // 掘ったあとの歩き（pathfinder）は模擬にない: そこで止まる。掘ったことと、失敗の印を確かめる
  await assert.rejects(PRIMITIVES.dig_out(bot, st, {}, signal))
  for (const p of route.cells) assert.equal(w.blockAt(p).name, 'air', `${p.x},${p.y},${p.z} を掘った`)
  assert.ok(st.digOutBlockedUntil > Date.now(), '失敗の印')
})

test('dig_out: 閉じていなければ、掘らずに歩いて出るよう返す', async () => {
  const w = cage({ overrides: { '2,70,0': 'air', '2,71,0': 'air', '3,70,0': 'air' } })
  assert.match(await PRIMITIVES.dig_out(botIn(w), state(), {}, signal), /not shut in/)
})

test('dig_out: 掘れる道がなければ、理由をつけて失敗する（何も掘らない）', async () => {
  const w = cage({ thick: 6 })
  const bot = botIn(w)
  const st = state()
  await assert.rejects(PRIMITIVES.dig_out(bot, st, {}, signal), /no way to dig out: no wall within 4 blocks/)
  assert.equal(w.blockAt(new Vec3(2, 70, 0)).name, 'dirt')
})
