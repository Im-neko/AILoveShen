import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import prismarineBlock from 'prismarine-block'
import { campAt, campSite, campKeep, campCount, buildCamp, campExit, placeRef } from '../src/camp.mjs'
import { inBurrow } from '../src/burrow.mjs'
import { PRIMITIVES } from '../src/primitives.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')
const Block = prismarineBlock('1.21.4')

// 地面は y 63 まで土（63 は草）。立つ足の高さは y 64。blocks: { 'x,y,z': name } で上書き
function world (blocks = {}) {
  const set = new Map(Object.entries(blocks))
  const blockAt = (p) => {
    const q = new Vec3(Math.floor(p.x), Math.floor(p.y), Math.floor(p.z))
    const name = set.get(`${q.x},${q.y},${q.z}`) ?? (q.y < 63 ? 'dirt' : q.y === 63 ? 'grass_block' : 'air')
    const b = Block.fromProperties(name, {}, 0)
    b.position = q
    return b
  }
  return { set, blockAt }
}

function botIn (w, { at = new Vec3(0.5, 64, 0.5), items = {}, sources = [] } = {}) {
  const held = { ...items }
  const bot = {
    registry: md,
    entities: {},
    entity: { position: at, onGround: true, yaw: 0, isInWater: false },
    blockAt: w.blockAt,
    heldItem: null,
    health: 20,
    inventory: { items: () => Object.entries(held).filter(([, n]) => n > 0).map(([name, count]) => ({ name, count, type: md.itemsByName[name].id })) },
    equip: async () => {},
    lookAt: async () => {},
    waitForTicks: async () => {},
    findBlocks: () => sources,
    dig: async (block) => {
      const p = block.position
      w.set.set(`${p.x},${p.y},${p.z}`, 'air')
      held.dirt = (held.dirt ?? 0) + 1
    },
    placeBlock: async (ref, face) => {
      const p = ref.position.plus(face)
      assert.equal(w.blockAt(p).name, 'air', `placing into a filled cell ${p}`)
      w.set.set(`${p.x},${p.y},${p.z}`, 'dirt')
      held.dirt -= 1
    }
  }
  return { bot, held }
}

const state = { home: null, formerHomes: [], builds: {}, plan: null, unreachableDrops: new Set() }
const feet = new Vec3(0, 64, 0)

test('平らな地面なら、四方 3 段と天井の 13 ブロックで囲める', () => {
  const { bot } = botIn(world())
  const r = campAt(bot, state, feet)
  assert.equal(typeof r, 'object')
  assert.equal(r.need, 13)
  assert.equal(r.ring.length, 12)
  assert.equal(r.roof.y, 66)
})

test('すでに固いセルは数えない（壁に寄っていれば少なくて済む）', () => {
  const { bot } = botIn(world({ '1,64,0': 'stone', '1,65,0': 'stone', '1,66,0': 'stone' }))
  assert.equal(campAt(bot, state, feet).need, 10)
})

test('水・足場なし・溶岩・家の中・畑の中はだめで、理由を言う', () => {
  assert.match(campAt(botIn(world({ '0,64,0': 'water' })).bot, state, feet), /water/)
  assert.match(campAt(botIn(world({ '0,63,0': 'air' })).bot, state, feet), /no solid ground/)
  assert.match(campAt(botIn(world({ '1,65,0': 'lava' })).bot, state, feet), /lava/)
  assert.match(campAt(botIn(world()).bot, { ...state, plan: { origin: new Vec3(-2, 64, -2), size: { width: 6, depth: 6, height: 4 } } }, feet), /house or a build/)
  const farm = { ...state, facilities: { f: { name: 'f', kind: 'farm', box: { min: { x: -3, y: 63, z: -3 }, max: { x: 3, y: 63, z: 3 } } } } }
  assert.match(campAt(botIn(world()).bot, farm, feet), /facility f/)
})

test('campSite: 足元が水のときは、近くの陸で ブロックが少なく済む所を選ぶ', () => {
  const { bot } = botIn(world({ '0,63,0': 'water', '0,64,0': 'water' }))
  const { site } = campSite(bot, state)
  assert.ok(site)
  assert.ok(site.feet.distanceTo(feet) >= 1)
  const none = campSite(botIn(world({ '0,63,0': 'water' })).bot, state, 0)
  assert.equal(none.site, null)
  assert.match(none.why, /./)
})

test('寄りかかる隣: 下の地面を先に、なければ横の固いブロック', () => {
  const { bot } = botIn(world())
  const down = placeRef(bot, new Vec3(1, 64, 0))
  assert.equal(down.ref.position.y, 63)
  assert.equal(down.face.y, 1)
  const { bot: air } = botIn(world({ '1,63,0': 'air', '2,64,0': 'stone' }))
  const side = placeRef(air, new Vec3(1, 64, 0))
  assert.equal(side.ref.position.x, 2)
  assert.equal(side.face.x, -1)
  const { bot: none } = botIn(world({ '1,63,0': 'air' }))
  assert.equal(placeRef(none, new Vec3(1, 66, 0)), null, '空中には置けない')
})

test('buildCamp: 下の段から置いて、囲めば inBurrow が真になる', async () => {
  const w = world()
  const { bot, held } = botIn(w, { items: { dirt: 20 } })
  const site = campAt(bot, state, feet)
  assert.equal(inBurrow(bot), false)
  const placed = await buildCamp(bot, site, new AbortController().signal)
  assert.equal(placed, 13)
  assert.equal(held.dirt, 7)
  assert.equal(inBurrow(bot), true)
  assert.equal(w.blockAt(new Vec3(0, 66, 0)).name, 'dirt') // 天井
  assert.equal(w.blockAt(new Vec3(0, 64, 0)).name, 'air') // 中は空
})

test('buildCamp: 途中でブロックが尽きたら、置いた数と理由を言う', async () => {
  const { bot } = botIn(world(), { items: { dirt: 5 } })
  await assert.rejects(buildCamp(bot, campAt(bot, state, feet), new AbortController().signal), /ran out of blocks after placing 5/)
})

test('wall_in: 持っているブロックで囲み、朝まで待つ。もう囲んでいれば何もしない', async () => {
  const w = world()
  const { bot } = botIn(w, { items: { dirt: 13 } })
  const text = await PRIMITIVES.wall_in(bot, state, {}, new AbortController().signal)
  assert.match(text, /enclosed myself at 0,64,0 with 13 blocks/)
  assert.equal(inBurrow(bot), true)
  assert.equal(await PRIMITIVES.wall_in(bot, state, {}, new AbortController().signal), 'already enclosed')
})

test('wall_in: ブロックが足りず、掘れる土も近くになければ、足りない数を言って断る', async () => {
  const { bot } = botIn(world(), { items: { dirt: 3 } })
  await assert.rejects(PRIMITIVES.wall_in(bot, state, {}, new AbortController().signal), /needs 13 blocks and you carry 3: no dirt within reach/)
})

test('wall_in: 水の中では囲まない（先に陸へ）', async () => {
  const { bot } = botIn(world(), { items: { dirt: 13 } })
  bot.entity.isInWater = true
  await assert.rejects(PRIMITIVES.wall_in(bot, state, {}, new AbortController().signal), /swim to land first/)
})

test('囲んだ土を掘るときに、囲み・足元・立つ所は掘らない（campKeep）', () => {
  const { bot } = botIn(world())
  const site = campAt(bot, state, feet)
  const keep = campKeep(site)
  assert.ok(keep.has('0,64,0') && keep.has('0,65,0') && keep.has('0,63,0'))
  assert.ok(keep.has('1,64,0') && keep.has('0,66,0'))
  assert.ok(!keep.has('3,63,0'))
  assert.equal(campCount(bot), 0)
})

test('朝: 囲いの壁の 1 方を掘って出る。縦穴（壁が自然の地面）には出口の道がない', async () => {
  const w = world()
  const { bot, held } = botIn(w, { items: { dirt: 20 } })
  await buildCamp(bot, campAt(bot, state, feet), new AbortController().signal)
  // 囲いの外は、壁の向こうの草地: 4 方のうち、向こうが空いている方向がある
  const exit = campExit(bot)
  assert.ok(exit)
  assert.equal(exit.cells.length, 2)
  const before = held.dirt
  // 壁を掘ったあとの歩き（pathfinder）は模擬にないので、そこで止まる。掘るところまでを確かめる
  await assert.rejects(PRIMITIVES.leave_burrow(bot, state, {}, new AbortController().signal))
  assert.equal(held.dirt, before + 2)
  assert.equal(w.blockAt(exit.cells[0]).name, 'air')
  assert.equal(w.blockAt(exit.cells[1]).name, 'air')
  // 縦穴: 四方が自然の地面で、向こうも固い
  const burrow = world({ '0,63,0': 'air', '0,62,0': 'air', '0,61,0': 'air' })
  const { bot: inHole } = botIn(burrow, { at: new Vec3(0.5, 61, 0.5) })
  assert.equal(campExit(inHole), null)
})
