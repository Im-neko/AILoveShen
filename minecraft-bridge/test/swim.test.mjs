import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { swimToward, aheadKind, isSwimming, onLand } from '../src/swim.mjs'

const { Vec3 } = vec3Pkg

// 海: x < 30 は y 62 まで水（海底は y 61 の砂）。x >= 30 は陸（y 62 まで地面、水面より 1 ブロック高い岸）。
// walls: 通れない 3 高の壁 [{ x, z0, z1 }]、kelp: ケルプのセル
function sea ({ walls = [], kelp = [], unloadedFrom = Infinity, landX = 30 } = {}) {
  const block = (x, y, z) => {
    if (x >= unloadedFrom) return null
    if (walls.some((w) => w.x === x && z >= w.z0 && z <= w.z1 && y >= 62 && y <= 64)) return { name: 'stone', boundingBox: 'block' }
    if (kelp.some(([kx, ky, kz]) => kx === x && ky === y && kz === z)) return { name: 'kelp', boundingBox: 'empty' }
    if (x >= landX) return y <= 62 ? { name: 'grass_block', boundingBox: 'block' } : { name: 'air', boundingBox: 'empty' }
    if (y <= 61) return { name: 'sand', boundingBox: 'block' }
    if (y === 62) return { name: 'water', boundingBox: 'empty' }
    return { name: 'air', boundingBox: 'empty' }
  }
  const controls = {}
  const bot = {
    entity: { position: new Vec3(2.5, 62.2, 0.5), isInWater: true, onGround: false },
    yaw: 0,
    blockAt: (p) => block(p.x, p.y, p.z),
    setControlState: (c, v) => { controls[c] = v },
    look: async (yaw) => { bot.yaw = yaw },
    waitForTicks: async () => {
      if (!controls.forward) return
      const p = bot.entity.position
      const dir = [-Math.sin(bot.yaw), -Math.cos(bot.yaw)]
      const nx = p.x + dir[0] * 0.25
      const nz = p.z + dir[1] * 0.25
      const cx = Math.floor(nx); const cy = Math.floor(p.y); const cz = Math.floor(nz)
      const c0 = block(cx, cy, cz); const c1 = block(cx, cy + 1, cz); const c2 = block(cx, cy + 2, cz)
      if (!c0 || !c1) return
      const free = (b) => b.boundingBox === 'empty'
      if (free(c0) && free(c1)) {
        p.x = nx; p.z = nz
        const below = block(cx, cy - 1, cz)
        if (c0.name === 'air' && below?.name === 'water') { p.y = cy - 0.8; bot.entity.isInWater = true; bot.entity.onGround = false } // 岸から水に落ちる
        return
      }
      if (controls.jump && c2 && free(c2) && free(c1)) {
        bot.entity.position = new Vec3(nx, cy + 1, nz) // 岸に上がる
        bot.entity.isInWater = false
        bot.entity.onGround = true
      }
    }
  }
  return { bot, controls }
}

test('向こう岸まで水面沿いにまっすぐ泳ぎ、1 ブロック高い岸に上がって止まる', async () => {
  const { bot, controls } = sea()
  assert.equal(isSwimming(bot), true)
  const r = await swimToward(bot, { x: 60, z: 0 }, new AbortController().signal)
  assert.equal(r.landed, true, r.why)
  assert.equal(onLand(bot), true)
  assert.ok(bot.entity.position.x >= 30)
  assert.ok(!controls.forward && !controls.jump && !controls.sprint, '押したキーは離す')
})

test('途中の壁はよけて進む（壁沿いに回る）', async () => {
  const { bot } = sea({ walls: [{ x: 15, z0: -6, z1: 6 }] })
  const r = await swimToward(bot, { x: 60, z: 0 }, new AbortController().signal)
  assert.equal(r.landed, true, r.why)
  assert.ok(bot.entity.position.x >= 30)
})

test('ケルプの中も水として進める（空気とは違う）', async () => {
  const kelp = []
  for (let x = 5; x < 25; x++) for (let z = -2; z <= 2; z++) for (const y of [62]) kelp.push([x, y, z])
  const { bot } = sea({ kelp })
  assert.equal(isSwimming(bot), true)
  const r = await swimToward(bot, { x: 60, z: 0 }, new AbortController().signal)
  assert.equal(r.landed, true, r.why)
})

test('進めない・先が読み込まれていないときは、理由を返して止まる', async () => {
  const closed = sea({ walls: [{ x: 5, z0: -50, z1: 50 }, { x: 1, z0: -50, z1: 50 }, { x: 3, z0: -50, z1: -50 }] })
  closed.bot.entity.position = new Vec3(3.5, 62.2, 0.5)
  const stuck = await swimToward(closed.bot, { x: 60, z: 0 }, new AbortController().signal, { maxMs: 20000, progressMs: 200 })
  assert.equal(stuck.landed, false)
  assert.match(stuck.why, /no way forward|did not get closer/)
  const far = sea({ unloadedFrom: 8 })
  const r = await swimToward(far.bot, { x: 60, z: 0 }, new AbortController().signal)
  assert.equal(r.landed, false)
  assert.equal(r.why, 'the water ahead is not loaded')
})

test('前のセルの種類: 水は open、1 ブロック高い岸は bank、3 高の壁は blocked、溶岩は blocked', () => {
  const { bot } = sea({ walls: [{ x: 9, z0: 0, z1: 0 }] })
  const me = new Vec3(8.5, 62.2, 0.5)
  assert.equal(aheadKind(bot, new Vec3(2.5, 62.2, 0.5), [1, 0]), 'open')
  assert.equal(aheadKind(bot, new Vec3(29.0, 62.2, 0.5), [1, 0]), 'bank')
  assert.equal(aheadKind(bot, me, [1, 0]), 'blocked')
  const lava = { blockAt: () => ({ name: 'lava', boundingBox: 'empty' }) }
  assert.equal(aheadKind(lava, me, [1, 0]), 'blocked')
})

test('ケルプ・海草は pathfinder に水として教える（空気とみなして海底の道を作らせない）', async () => {
  const { configureMovements } = await import('../src/primitives.mjs')
  const minecraftData = (await import('minecraft-data')).default
  const md = minecraftData('1.21.4')
  let m
  const bot = { registry: md, version: '1.21.4', entities: {}, pathfinder: { setMovements (x) { m = x } } }
  configureMovements(bot, {})
  for (const name of ['water', 'kelp', 'kelp_plant', 'seagrass', 'tall_seagrass']) {
    assert.ok(m.liquids.has(md.blocksByName[name].id), name)
  }
})

test('水の中にいて目標が水でなければ、着いたと言わない。水の中で終わった行動は、陸ではないと結果に書く', async () => {
  const { arrivalReport } = await import('../src/primitives.mjs')
  const { goals } = (await import('mineflayer-pathfinder')).default
  const water = { name: 'water', boundingBox: 'empty' }
  const bot = { entity: { position: new Vec3(10.5, 62.2, 0.5), isInWater: true }, blockAt: (p) => (p.y <= 62 ? water : { name: 'air', boundingBox: 'empty' }) }
  const goal = new goals.GoalNear(12, 63, 0, 1)
  assert.throws(() => arrivalReport(bot, goal, new Vec3(12, 63, 0)), /you are in the water at .*not on land/)
})

test('岸から始めると、水に入って渡り、向こう岸に上がってから止まる（岸で「着いた」と数えない）', async () => {
  const { bot } = sea({ landX: 60 })
  // 手前の岸: x < 5 は陸（1 ブロック高い）、5 以降が海
  const seaBlock = bot.blockAt
  bot.blockAt = (p) => (p.x < 5 ? (p.y <= 62 ? { name: 'grass_block', boundingBox: 'block' } : { name: 'air', boundingBox: 'empty' }) : seaBlock(p))
  bot.entity.position = new Vec3(3.5, 63, 0.5)
  bot.entity.isInWater = false
  bot.entity.onGround = true
  assert.equal(isSwimming(bot), false)
  const r = await swimToward(bot, { x: 90, z: 0 }, new AbortController().signal)
  assert.equal(r.landed, true, r.why)
  assert.ok(r.moved > 30, `moved ${r.moved}`)
  assert.ok(bot.entity.position.x >= 60)
})

test('目標が岸のいちばん手前の陸のマスでも、手前で「着いた」にせず、岸に上がるまで押す（2026-10-07 池で 5 分動けなかった）', async () => {
  const { bot } = sea()
  const r = await swimToward(bot, { x: 30.5, z: 0.5 }, new AbortController().signal, { climb: true })
  assert.equal(r.landed, true, r.why)
  assert.equal(onLand(bot), true)
})

test('岸に上がれない所（高すぎる岸）では、押し続けず理由を返す', async () => {
  const { bot } = sea({ walls: [{ x: 30, z0: -50, z1: 50 }] })
  const r = await swimToward(bot, { x: 30.5, z: 0.5 }, new AbortController().signal, { climb: true })
  assert.equal(r.landed, false)
  assert.ok(r.why)
})

test('水から跳び出して宙にいる間（泳いでいない・まだ地面にない）に目標の近くに着いても、「着いた」で止めず、着地まで待つ', async () => {
  const { bot } = sea()
  bot.entity.position = new Vec3(29.5, 62.2, 0.5) // 岸の 1 マス手前、水の中
  let ticks = 0
  const move = bot.waitForTicks
  bot.waitForTicks = async () => {
    ticks++
    if (ticks === 1) { bot.entity.isInWater = false; bot.entity.onGround = false; bot.entity.position = new Vec3(30.2, 63.5, 0.5); return } // 跳び出した
    if (ticks === 5) { bot.entity.onGround = true; return } // 着地
    return move()
  }
  const r = await swimToward(bot, { x: 30.5, z: 0.5 }, new AbortController().signal, { climb: true })
  assert.equal(r.landed, true, r.why)
})
