import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import prismarineBlock from 'prismarine-block'
import { burrowAt, burrowSpot, inBurrow, inOpenShaft, canStepOut, sealSupport, BURROW_DEPTH } from '../src/burrow.mjs'
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

function botIn (w, { at = new Vec3(0.5, 64, 0.5), items = {} } = {}) {
  const held = { ...items }
  const bot = {
    registry: md,
    entities: {},
    entity: { position: at, onGround: true, yaw: 0 },
    blockAt: w.blockAt,
    heldItem: null,
    inventory: { items: () => Object.entries(held).filter(([, n]) => n > 0).map(([name, count]) => ({ name, count, type: md.itemsByName[name].id })) },
    equip: async () => {},
    unequip: async () => {},
    lookAt: async () => {},
    waitForTicks: async () => {},
    dig: async (block) => {
      const p = block.position
      w.set.set(`${p.x},${p.y},${p.z}`, 'air')
      const drop = { grass_block: 'dirt', dirt: 'dirt' }[block.name] ?? 'dirt'
      held[drop] = (held[drop] ?? 0) + 1
      // 立っていた足元が抜けたら、落ちる
      if (Math.floor(at.x) === p.x && Math.floor(at.z) === p.z && at.y - p.y <= 1.001 && at.y >= p.y) at.y = p.y
    },
    placeBlock: async (ref, face) => {
      const p = ref.position.plus(face)
      const name = w.blockAt(p).name
      assert.equal(name, 'air', 'placing into a filled cell')
      w.set.set(`${p.x},${p.y},${p.z}`, 'dirt')
      held.dirt -= 1
    }
  }
  return bot
}

const state = { home: null, formerHomes: [], builds: {}, plan: null, unreachableDrops: new Set() }

test('平らな土なら足元に 3 マスの穴を掘れて、いちばん上を塞ぐ', () => {
  const bot = botIn(world())
  const r = burrowAt(bot, state, 0, 64, 0)
  assert.equal(typeof r, 'object')
  assert.deepEqual(r.cells.map((c) => c.y), [63, 62, 61])
  assert.equal(r.seal.y, 63)
  assert.equal(r.floor.y, 60)
})

test('斜面・崖・水のそば・石（つるはしなし）・家の中はだめで、理由を言う', () => {
  const slope = botIn(world({ '1,63,0': 'air' })) // 隣が低い: モブが入れる
  assert.match(burrowAt(slope, state, 0, 64, 0), /not level/)
  const wet = botIn(world({ '1,62,0': 'water' }))
  assert.match(burrowAt(wet, state, 0, 64, 0), /water/)
  const rock = botIn(world({ '0,63,0': 'stone', '0,62,0': 'stone', '0,61,0': 'stone' }))
  assert.match(burrowAt(rock, state, 0, 64, 0), /stone cannot be dug/)
  assert.equal(typeof burrowAt(botIn(world({ '0,63,0': 'stone' }), { items: { stone_pickaxe: 1 } }), state, 0, 64, 0), 'object')
  const sand = botIn(world({ '0,62,0': 'gravel' }))
  assert.match(burrowAt(sand, state, 0, 64, 0), /gravel cannot be dug/)
  const cave = botIn(world({ '0,60,0': 'air' }))
  assert.match(burrowAt(cave, state, 0, 64, 0), /no solid floor/)
  const built = { ...state, plan: { origin: new Vec3(-2, 64, -2), size: { width: 6, depth: 6, height: 4 } } }
  assert.match(burrowAt(botIn(world()), built, 0, 64, 0), /house or a build/)
})

test('立っている所がだめなら、近くの平らな所を探す。どこもだめなら理由を返す', () => {
  const near = botIn(world({ '0,63,0': 'water' })) // 足元は水
  const found = burrowSpot(near, state)
  assert.ok(found.spot)
  assert.ok(found.spot.feet.distanceTo(new Vec3(0, 64, 0)) >= 1)
  const nowhere = botIn(world({ '0,63,0': 'water' }))
  const none = burrowSpot(nowhere, state, 0) // 立っている列だけ
  assert.equal(none.spot, null)
  assert.match(none.why, /./)
})

test('穴の中: 足と頭のまわりが全部ブロックで、上にも下にもある', () => {
  const dug = { '0,63,0': 'air', '0,62,0': 'air', '0,61,0': 'air' }
  const open = botIn(world(dug), { at: new Vec3(0.5, 61, 0.5) })
  assert.equal(inBurrow(open), false) // 上が開いている
  const sealed = botIn(world({ ...dug, '0,63,0': 'dirt' }), { at: new Vec3(0.5, 61, 0.5) })
  assert.equal(inBurrow(sealed), true)
  assert.equal(inBurrow(botIn(world())), false) // 地上
})

test('横に出られる高さ: 隣が空いていて、その下が地面', () => {
  const w = world({ '0,63,0': 'air', '0,62,0': 'air', '0,61,0': 'air' })
  const bot = botIn(w)
  assert.equal(canStepOut(bot, new Vec3(0, 61, 0)), false)
  assert.equal(canStepOut(bot, new Vec3(0, 64, 0)), true)
  assert.equal(sealSupport(bot, new Vec3(0, 63, 0)).face.x, -1)
})

test('dig_in: 掘って底に降り、上を掘った土で塞ぐ。dig_in はもう穴の中なら何もしない', async () => {
  const w = world()
  const bot = botIn(w)
  const ac = new AbortController()
  const text = await PRIMITIVES.dig_in(bot, state, {}, ac.signal)
  assert.match(text, /dug in: a 3-block hole/)
  assert.equal(bot.entity.position.y, 61)
  assert.equal(w.blockAt(new Vec3(0, 63, 0)).name, 'dirt') // 塞ぎ
  assert.equal(w.blockAt(new Vec3(0, 62, 0)).name, 'air')
  assert.equal(inBurrow(bot), true)
  assert.match(await PRIMITIVES.dig_in(bot, state, {}, ac.signal), /already/)
})

test('dig_in: 塞ぐ土がなければ、掘ったことと塞げなかった理由を言う', async () => {
  const w = world()
  const bot = botIn(w)
  bot.placeBlock = async () => { throw new Error('should not be called') }
  const dig = bot.dig
  bot.dig = async (b) => { await dig(b); bot.inventory.items = () => [] } // 拾えなかった
  await assert.rejects(PRIMITIVES.dig_in(bot, state, {}, new AbortController().signal), /could not seal it \(no dirt/)
})

test('leave_burrow: 塞ぎを壊し、足元に土を置いて地面の高さまで上がる', async () => {
  const w = world({ '0,63,0': 'dirt', '0,62,0': 'air', '0,61,0': 'air' }) // 63 が塞ぎ、足は 61
  const bot = botIn(w, { at: new Vec3(0.5, 61, 0.5), items: { dirt: 3 } })
  const dig = bot.dig
  bot.dig = async (b) => { const y = bot.entity.position.y; await dig(b); bot.entity.position.y = y } // 頭の上を掘っても落ちない
  let jumping = false
  bot.look = async () => {}
  bot.setControlState = (k, on) => { if (k === 'jump') jumping = on }
  bot.waitForTicks = async () => { if (jumping) bot.entity.position.y += 1 }
  assert.equal(inBurrow(bot), true)
  const text = await PRIMITIVES.leave_burrow(bot, state, {}, new AbortController().signal)
  assert.match(text, /climbed out of the hole to 0,64,0/)
  assert.equal(w.blockAt(new Vec3(0, 63, 0)).name, 'dirt') // 足場に置いた土（塞ぎは壊してから積み直した）
  assert.equal(inBurrow(bot), false)
  assert.equal(await PRIMITIVES.leave_burrow(bot, state, {}, new AbortController().signal), 'not in a hole')
})

test('leave_burrow: 塞ぎが開いたままの穴でも、足元に土を置いて上がる', async () => {
  const w = world({ '0,63,0': 'air', '0,62,0': 'air', '0,61,0': 'air' }) // 塞ぎなし: 足は 61、上は開いている
  const bot = botIn(w, { at: new Vec3(0.5, 61, 0.5), items: { dirt: 3 } })
  let jumping = false
  bot.look = async () => {}
  bot.setControlState = (k, on) => { if (k === 'jump') jumping = on }
  bot.waitForTicks = async () => { if (jumping) bot.entity.position.y += 1 }
  assert.equal(inBurrow(bot), false)
  assert.equal(inOpenShaft(bot), true)
  assert.match(await PRIMITIVES.leave_burrow(bot, state, {}, new AbortController().signal), /climbed out of the hole/)
})

test('leave_burrow: 上がる土がなければ、開けたことと理由を言う', async () => {
  const w = world({ '0,63,0': 'dirt', '0,62,0': 'air', '0,61,0': 'air' })
  const bot = botIn(w, { at: new Vec3(0.5, 61, 0.5) })
  const dig = bot.dig
  bot.dig = async (b) => { const y = bot.entity.position.y; await dig(b); bot.entity.position.y = y }
  bot.inventory.items = () => []
  await assert.rejects(PRIMITIVES.leave_burrow(bot, state, {}, new AbortController().signal), /carry no dirt or cobblestone to climb out/)
})

test('dig_in: 塞ぐのに失敗して穴が開いたままなら、次は掘り足さずに塞ぐだけ（深くなって出られなくなる）', async () => {
  const w = world()
  const bot = botIn(w)
  const place = bot.placeBlock
  let digs = 0
  const dig = bot.dig
  bot.dig = async (b) => { digs++; await dig(b) }
  bot.placeBlock = async () => { throw new Error('the server refused the block') }
  await assert.rejects(PRIMITIVES.dig_in(bot, state, {}, new AbortController().signal), /server refused/)
  assert.equal(digs, 3)
  assert.equal(inBurrow(bot), false)
  assert.equal(inOpenShaft(bot), true)
  assert.equal(typeof burrowAt(bot, state, 0, 61, 0), 'string') // 穴の底は、掘る地面ではない
  bot.placeBlock = place
  const text = await PRIMITIVES.dig_in(bot, state, {}, new AbortController().signal)
  assert.match(text, /dug in/)
  assert.equal(digs, 3, '掘り足していない')
  assert.equal(bot.entity.position.y, 61)
  assert.equal(inBurrow(bot), true)
})

test('BURROW_DEPTH は 3', () => assert.equal(BURROW_DEPTH, 3))

test('畑・植林場（柵の輪を含む）の中には穴を掘らない', () => {
  const bot = botIn(world())
  const withFarm = { ...state, facilities: { wheat_farm_1: { name: 'wheat_farm_1', kind: 'farm', box: { min: { x: -3, y: 63, z: -3 }, max: { x: 3, y: 63, z: 3 } } } } }
  assert.match(burrowAt(bot, withFarm, 0, 64, 0), /part of the facility wheat_farm_1/)
  assert.match(burrowAt(bot, withFarm, 4, 64, 0), /part of the facility/, '1 マスのよゆう')
  assert.equal(typeof burrowAt(bot, withFarm, 5, 64, 0), 'object')
})
