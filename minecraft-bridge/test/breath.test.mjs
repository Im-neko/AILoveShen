import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { holdsWater, headUnderwater, findAir, breathTrigger, ticksToSwim, surface, underwater, waterNeed, airLevel, MAX_AIR } from '../src/breath.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')

// 世界: y 60 以下は石。blocks: "x,y,z" → 名前。水は water、その他は指定のとおり。既定は空気
function world (blocks = {}, { at = new Vec3(0.5, 62, 0.5), air = undefined, effects = {} } = {}) {
  const controls = {}
  const bot = {
    registry: md,
    oxygenLevel: air,
    entity: { position: at, eyeHeight: 1.62, effects },
    blockAt: (q) => {
      const p = new Vec3(Math.floor(q.x), Math.floor(q.y), Math.floor(q.z))
      const name = blocks[`${p.x},${p.y},${p.z}`] ?? (p.y <= 60 ? 'stone' : 'air')
      const solid = !/^(air|water|kelp|seagrass|lava)$/.test(name)
      return { name, boundingBox: solid ? 'block' : 'empty', position: p, getProperties: () => ({}) }
    },
    setControlState: (c, v) => { controls[c] = v },
    controls,
    lookAt: async (p) => { bot.looking = p },
    looking: null,
    waitForTicks: async () => {
      // 1 ティックの泳ぎ: 前進は見ている方へ 0.15、ジャンプは 0.1 上、スニークは 0.1 下
      const e = bot.entity.position
      if (controls.forward && bot.looking) {
        const dx = bot.looking.x - e.x
        const dz = bot.looking.z - e.z
        const len = Math.hypot(dx, dz) || 1
        const step = Math.min(0.15, len)
        e.x += (dx / len) * step
        e.z += (dz / len) * step
      }
      if (controls.jump) e.y += 0.1
      if (controls.sneak) e.y -= 0.1
      if (bot.oxygenLevel !== undefined && headUnderwater(bot)) bot.oxygenLevel = Math.max(0, bot.oxygenLevel - 0.2)
      else if (bot.oxygenLevel !== undefined) bot.oxygenLevel = Math.min(MAX_AIR, bot.oxygenLevel + 4)
    }
  }
  return bot
}

// 半径 r の池（y 61〜65 が水）。天井を張れば、端の 1 か所だけ空気
function pond (r = 8, top = 65) {
  const b = {}
  for (let x = -r; x <= r; x++) for (let z = -r; z <= r; z++) for (let y = 61; y <= top; y++) b[`${x},${y},${z}`] = 'water'
  return b
}
const roofed = (b, r = 8, y = 66, except = []) => {
  for (let x = -r; x <= r; x++) for (let z = -r; z <= r; z++) if (!except.some(([ex, ez]) => ex === x && ez === z)) b[`${x},${y},${z}`] = 'stone'
  return b
}

test('水の判定: 水・昆布・海草・waterlogged。頭は目の高さのセルで見る', () => {
  assert.equal(holdsWater({ name: 'water' }), true)
  assert.equal(holdsWater({ name: 'kelp_plant' }), true)
  assert.equal(holdsWater({ name: 'oak_stairs', getProperties: () => ({ waterlogged: 'true' }) }), true)
  assert.equal(holdsWater({ name: 'stone' }), false)
  assert.equal(headUnderwater(world(pond(), { at: new Vec3(0.5, 62, 0.5) })), true) // 目は 63.6
  assert.equal(headUnderwater(world(pond(2, 63), { at: new Vec3(0.5, 63, 0.5) })), false) // 目は 64.6（水面の上）
})

test('真上が開いていれば、真上へ泳ぐ道（上向きに 1 マスずつ）', () => {
  const bot = world(pond(), { at: new Vec3(0.5, 62, 0.5) })
  const path = findAir(bot)
  assert.ok(path.length >= 3 && path.length <= 4)
  assert.ok(path.every((c) => c.x === 0 && c.z === 0))
  assert.deepEqual(path.map((c) => c.y), [...path.map((c) => c.y)].sort((a, b) => a - b))
})

test('天井があれば、横へ泳いで空気のある所へ（天井の穴か端）', () => {
  const blocks = roofed(pond(), 8, 66, [[6, 0]]) // x=6, z=0 だけ天井がない
  const bot = world(blocks, { at: new Vec3(0.5, 62, 0.5) })
  const path = findAir(bot)
  assert.ok(path, '道がある')
  const end = path.at(-1)
  assert.equal(end.x, 6)
  assert.equal(end.z, 0)
  assert.ok(path.length <= 10, `${path.length}`)
})

test('水の中に閉じ込められて空気がなければ null', () => {
  const blocks = roofed(pond(3, 65), 4, 66)
  for (let y = 61; y <= 66; y++) for (let i = -4; i <= 4; i++) for (const [x, z] of [[i, -4], [i, 4], [-4, i], [4, i]]) blocks[`${x},${y},${z}`] = 'stone'
  assert.equal(findAir(world(blocks, { at: new Vec3(0.5, 62, 0.5) }), undefined, 500), null)
})

test('きっかけ: 息が十分ならなし、道のりに足りなくなったら出る。水の外・水中呼吸ではなし', () => {
  const blocks = roofed(pond(), 8, 66, [[6, 0]])
  const at = new Vec3(0.5, 62, 0.5)
  assert.equal(breathTrigger(world(blocks, { at, air: 20 })), null)
  assert.equal(breathTrigger(world(blocks, { at, air: undefined })), null) // まだ届いていない = 満タン
  const path = findAir(world(blocks, { at }))
  const needed = Math.ceil((ticksToSwim(path.length) + 60) / 15)
  assert.equal(breathTrigger(world(blocks, { at, air: needed + 2 })), null)
  const t = breathTrigger(world(blocks, { at, air: needed }))
  assert.ok(t && t.path.length === path.length)
  // 水中呼吸の効果
  const id = md.effectsArray.find((e) => /water_?breathing/i.test(e.name)).id
  assert.equal(breathTrigger(world(blocks, { at, air: 3, effects: { [id]: { amplifier: 0, duration: 100 } } })), null)
  // 頭が水の外
  assert.equal(breathTrigger(world(pond(2, 63), { at: new Vec3(0.5, 63, 0.5), air: 3 })), null)
})

test('道がなくても、息が 6 以下になれば真上へ（null の道）', () => {
  const blocks = roofed(pond(3, 65), 4, 66)
  for (let y = 61; y <= 66; y++) for (let i = -4; i <= 4; i++) for (const [x, z] of [[i, -4], [i, 4], [-4, i], [4, i]]) blocks[`${x},${y},${z}`] = 'stone'
  const at = new Vec3(0.5, 62, 0.5)
  assert.equal(breathTrigger(world(blocks, { at, air: 8 })), null)
  assert.deepEqual(breathTrigger(world(blocks, { at, air: 5 })), { path: null, air: 5 })
})

test('泳ぐ: 天井の穴まで横へ、上がって、頭が水から出て息が戻るまで浮く', async () => {
  const blocks = roofed(pond(), 8, 66, [[6, 0]])
  const bot = world(blocks, { at: new Vec3(0.5, 62, 0.5), air: 4 })
  const path = findAir(bot)
  const result = await surface(bot, path)
  assert.match(result, /^surfaced/)
  assert.equal(headUnderwater(bot), false)
  assert.ok(airLevel(bot) >= 16)
  assert.ok(Math.abs(bot.entity.position.x - 6.5) < 1, `x ${bot.entity.position.x}`)
  assert.deepEqual(bot.controls, { forward: false, jump: false, sneak: false }) // 離した
})

test('自覚: 頭が水の中なら欲求に息を出す。息が減れば浮上を促す。水の外では出さない', () => {
  const dive = (air) => world(pond(), { at: new Vec3(0.5, 62, 0.5), air })
  assert.deepEqual(underwater(dive(20)), { air: 20 })
  assert.match(waterNeed(dive(9)), /head is underwater \(air 9\/20\): swim up/)
  assert.equal(waterNeed(dive(20)), 'your head is underwater (air 20/20)')
  assert.equal(waterNeed(world(pond(2, 63), { at: new Vec3(0.5, 63, 0.5), air: 9 })), null)
})

test('息の見積もりは頭が水の中にいた時間から: oxygenLevel が気泡の尺度でなくても（水の中で 300 前後）、深い海で息が尽きる前に反射が出る', () => {
  const b = pond(8, 70) // y 61〜70 が水: 水面まで 9 ブロックの深い水
  const bot = world(b, { at: new Vec3(0.5, 61, 0.5), air: 307 })
  assert.equal(breathTrigger(bot, 0), null) // 入ったばかり
  assert.equal(breathTrigger(bot, 1000), null)
  let late = null
  for (let t = 2000; t <= 9000 && !late; t += 1000) late = breathTrigger(bot, t) // 反射は 1 秒おきに見る
  assert.ok(late && late.air <= 12, JSON.stringify(late && late.air)) // 水面まで泳ぐのに足りなくなる前に出る
})

test('息の見積もり: 水から出ていた間（呼ばれない間）は数え直す。水の外では bot.oxygenLevel のまま', () => {
  const bot = world(pond(8, 70), { at: new Vec3(0.5, 61, 0.5), air: 20 })
  for (let t = 0; t <= 4000; t += 1000) breathTrigger(bot, t)
  assert.equal(airLevel(bot, 4000) <= 16, true)
  // 5 秒呼ばれなかった（水から出ていた）あと、また入った: 数え直し
  breathTrigger(bot, 9000)
  assert.equal(airLevel(bot, 9000), MAX_AIR)
  const dry = world({}, { air: 20 })
  assert.equal(airLevel(dry, 99999), 20)
})
