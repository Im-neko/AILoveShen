// 建物の天井を貫いて屋根の上に出て、降りる手がないまま止まった事故（2026-10-10 20:24〜20:35 JST、docs/design/155）の再現。
// 積んで上がった先が出口か（屋根・柱の頂上なら積まない）、上が保護された建物なら横に出る、屋根の縁から飛び降りる、
// 自分が積んだ列だけを戻る。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import prismarineBlock from 'prismarine-block'
import { closedSpace, isolatedSpace, climbExit, dropOffs, rockBetween, regionCached, forgetRegion, piledUnder } from '../src/reach.mjs'
import { digOutRoute } from '../src/dig-out.mjs'
import { forNeeds } from '../src/candidates.mjs'
import { PRIMITIVES } from '../src/primitives.mjs'
import { Knowledge } from '../src/knowledge.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')
const Block = prismarineBlock('1.21.4')
const k = new Knowledge(md)
const signal = new AbortController().signal

// 地面は y 64 まで石（立つ高さ 65）。x, z が -3..3 の 7x7 の固まりが y 65 から height 段。room: 中の 3x3 の空洞 { y0, y1 }
function tower ({ height, room = null, hole = null } = {}) {
  const set = new Map()
  const blockAt = (p) => {
    const q = new Vec3(Math.floor(p.x), Math.floor(p.y), Math.floor(p.z))
    if (Math.abs(q.x) > 40 || Math.abs(q.z) > 40) return null
    let name = set.get(`${q.x},${q.y},${q.z}`)
    if (!name) {
      if (q.y <= 64) name = 'stone'
      else if (Math.abs(q.x) <= 3 && Math.abs(q.z) <= 3 && q.y <= 64 + height) {
        const inRoom = room && Math.abs(q.x) <= 1 && Math.abs(q.z) <= 1 && q.y >= room.y0 && q.y <= room.y1
        name = inRoom ? 'air' : 'cobblestone'
      } else name = 'air'
    }
    const b = Block.fromProperties(name, {}, 0)
    b.position = q
    return b
  }
  return { set, blockAt }
}

function botAt (w, at, { items = { dirt: 8 }, health = 20 } = {}) {
  const held = { ...items }
  let forward = false
  const bot = {
    registry: md,
    entities: {},
    entity: { position: at, onGround: true, yaw: 0, isInWater: false },
    time: { timeOfDay: 1000, age: 0 },
    health,
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
    setControlState: (key, on) => { if (key === 'forward') forward = on },
    waitForTicks: async () => { if (forward && bot.fallTo) { at.x = bot.fallTo.x + 0.5; at.y = bot.fallTo.y; at.z = bot.fallTo.z + 0.5 } },
    dig: async (block) => { const p = block.position; w.set.set(`${p.x},${p.y},${p.z}`, 'air'); at.y = Math.min(at.y, p.y) },
    placeBlock: async () => { throw new Error('nothing should be placed') }
  }
  return bot
}

// 建物の敷地（保護）。x, z が -w..w、y 65..
const withBuild = (half) => ({ home: null, formerHomes: [], builds: {}, plan: { origin: { x: -half, y: 65, z: -half }, size: { width: 2 * half + 1, depth: 2 * half + 1, height: 12 } } })
const open = () => ({ home: null, formerHomes: [], builds: {}, plan: null })

test('密閉した部屋（建物の中）から真上へ積むと、四方が崖の屋根の上に出るだけ: climbExit が plateau と言う', () => {
  const w = tower({ height: 9, room: { y0: 66, y1: 67 } })
  const bot = botAt(w, new Vec3(0.5, 66, 0.5))
  assert.ok(closedSpace(bot))
  const exit = climbExit(bot, new Vec3(0, 66, 0))
  assert.equal(exit.ok, false)
  assert.equal(exit.plateau, true)
  assert.ok(exit.dug >= 5, `屋根までに掘る数: ${exit.dug}`)
})

test('climb_out は、屋根の上に出るだけなら何も掘らず積まずに断る。横に掘って出る道を案内し、聞き直しを止める', async () => {
  const w = tower({ height: 9, room: { y0: 66, y1: 67 } })
  const before = new Map(w.set)
  const s = open()
  const bot = botAt(w, new Vec3(0.5, 66, 0.5))
  await assert.rejects(PRIMITIVES.climb_out(bot, s, {}, signal), /roof or pillar top with a drop on every side.*Dig out sideways/)
  assert.ok(s.climbBlockedUntil > Date.now())
  assert.deepEqual([...w.set], [...before], '何も掘らない')
})

test('候補: 屋根の上に出るだけなら climb_out は出さず、横に掘る dig_out を出す（積む土があっても）', () => {
  const w = tower({ height: 9, room: { y0: 66, y1: 67 } })
  const c = forNeeds(botAt(w, new Vec3(0.5, 66, 0.5)), open(), k)
  assert.equal(c.some((x) => x.verb === 'climb_out'), false)
  const dig = c.find((x) => x.verb === 'dig_out')
  assert.ok(dig, 'dig_out が出る')
  assert.equal(dig.breakProtected, undefined)
})

test('壁が保護された建物しかないなら、最後の手として壊して出る dig_out を、壊すと分かる名前で出す（聞かずには実行しない）', () => {
  const w = tower({ height: 9, room: { y0: 66, y1: 67 } })
  const s = withBuild(3)
  const bot = botAt(w, new Vec3(0.5, 66, 0.5))
  assert.match(digOutRoute(bot, s).why, /part of the house being built/)
  const forced = digOutRoute(bot, s, undefined, { breakProtected: true })
  assert.equal(forced.why, undefined)
  assert.ok(forced.protectedCells > 0)
  const dig = forNeeds(bot, s, k).find((x) => x.verb === 'dig_out')
  assert.equal(dig.breakProtected, true)
  assert.match(dig.id, /wall of a home or build \(takes part of it down\)/)
  assert.notEqual(dig.urgent, true)
})

test('上が保護された建物で、横に出る保護されていない道があるなら、建物を貫く climb_out は出さず横へ', async () => {
  // 部屋の真上（3x3）だけが敷地。天井は 1 枚で、積むと地面と同じ高さの屋根に出る（歩いて出られる）が、敷地を貫く
  const w = tower({ height: 3, room: { y0: 65, y1: 66 } })
  const s = withBuild(1)
  const bot = botAt(w, new Vec3(0.5, 65, 0.5))
  const exit = climbExit(bot, new Vec3(0, 65, 0), { isProtected: () => true })
  assert.equal(exit.ok, true)
  assert.ok(exit.protectedCount > 0)
  assert.equal(digOutRoute(bot, s).why, undefined, '横に出る道（敷地の外の壁）がある')
  const c = forNeeds(bot, s, k)
  assert.equal(c.some((x) => x.verb === 'climb_out'), false)
  assert.ok(c.some((x) => x.verb === 'dig_out'))
  await assert.rejects(PRIMITIVES.climb_out(bot, s, {}, signal), /goes through \d+ blocks of a home or build: dig out sideways/)
})

test('屋根の上: 縁から降りる先は、落差 − 3 が落下ダメージ。ダメージの少ない縁、近い縁の順', () => {
  const w = tower({ height: 9 })
  const bot = botAt(w, new Vec3(3.5, 74, 0.5)) // 屋根（y 73 の固まりの上、足は 74）の東の縁
  assert.ok(isolatedSpace(bot))
  const [best] = dropOffs(bot)
  assert.equal(best.drop, 9)
  assert.equal(best.damage, 6)
  assert.deepEqual([best.from.x, best.from.z], [3, 0])
})

test('候補: 屋根の上で体力が十分なら step_off を出す。足りなければ出さない', () => {
  const w = tower({ height: 9 })
  const hi = forNeeds(botAt(w, new Vec3(3.5, 74, 0.5), { health: 20 }), open(), k).find((x) => x.verb === 'step_off')
  assert.ok(hi)
  assert.equal(hi.drop, 9)
  assert.match(hi.id, /step off the edge and drop 9 blocks to .* \(6 damage, health 20\)/)
  const lo = forNeeds(botAt(w, new Vec3(3.5, 74, 0.5), { health: 9 }), open(), k).find((x) => x.verb === 'step_off')
  assert.equal(lo, undefined)
})

test('step_off: 縁から歩き出して降り、歩ける所に着く。体力が足りなければ断る', async () => {
  const w = tower({ height: 9 })
  const bot = botAt(w, new Vec3(3.5, 74, 0.5))
  bot.fallTo = new Vec3(4, 65, 0)
  const text = await PRIMITIVES.step_off(bot, open(), {}, signal)
  assert.match(text, /stepped off the edge and dropped 9 blocks \(6 fall damage expected\) to 4,65,0/)
  const weak = botAt(w, new Vec3(3.5, 74, 0.5), { health: 8 })
  await assert.rejects(PRIMITIVES.step_off(weak, open(), {}, signal), /every edge drops too far for your health 8\/20/)
  const walk = botAt(w, new Vec3(8.5, 65, 0.5))
  assert.match(await PRIMITIVES.step_off(walk, open(), {}, signal), /not on a roof or pillar top/)
})

test('climb_down: 自分が積んだ列なら、横が壁で柱の形でなくても、その列だけを掘って降りる。積んでいない足元は掘らない', async () => {
  const w = tower({ height: 0 })
  for (let y = 65; y <= 70; y++) w.set.set(`0,${y},0`, 'cobblestone')
  w.set.set('1,68,0', 'stone') // 横の壁: pillarUnder は null になる
  const s = open()
  const bot = botAt(w, new Vec3(0.5, 71, 0.5))
  await assert.rejects(PRIMITIVES.climb_down(bot, s, {}, signal), /not a free-standing pillar or a pile you made/)
  s.pillar = [65, 66, 67, 68, 69, 70].map((y) => ({ x: 0, y, z: 0 }))
  assert.ok(piledUnder(bot, s))
  const text = await PRIMITIVES.climb_down(bot, s, {}, signal)
  assert.match(text, /dug down \d+ blocks? of the pillar and can walk off now/)
  assert.ok(s.pillar.length < 6, '掘った列は記録から外れる')
  assert.ok(forNeeds(botAt(tower({ height: 0 }), new Vec3(0.5, 66, 0.5)), open(), k).every((x) => x.verb !== 'climb_down'))
})

test('rockBetween: 崖の上は、既定では岩の向こうと言わない（cliff: true のときだけ崖と言う）', () => {
  const w = tower({ height: 9 })
  const bot = botAt(w, new Vec3(3.5, 74, 0.5))
  const far = new Vec3(30, 65, 0)
  assert.equal(rockBetween(bot, far, false), null)
  const r = rockBetween(bot, far, false, { cliff: true })
  assert.equal(r.cliff, true)
})

test('歩ける範囲の覚えは、forgetRegion で捨てられる（動かずに足元を掘っても、3 秒古い結果が返らない）', () => {
  const w = tower({ height: 9, room: { y0: 66, y1: 67 } })
  const bot = botAt(w, new Vec3(0.5, 66, 0.5))
  const first = regionCached(bot)
  assert.equal(first.open, false)
  for (let y = 66; y <= 74; y++) w.set.set(`0,${y},0`, 'air') // 天井を抜く
  assert.equal(regionCached(bot), first, '同じ場所では覚えを返す')
  forgetRegion(bot)
  assert.notEqual(regionCached(bot), first)
})
