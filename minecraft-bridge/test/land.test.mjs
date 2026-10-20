import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import prismarineBlock from 'prismarine-block'
import { findLand, findLandCached, reachLand, LAND_RADIUS } from '../src/land.mjs'
import { forNeeds } from '../src/candidates.mjs'
import { PRIMITIVES, DAMAGE_TOLERANT } from '../src/primitives.mjs'
import { Knowledge } from '../src/knowledge.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')
const Block = prismarineBlock('1.21.4')
const k = new Knowledge(md)

// 海: y 60 が海底（砂）、y 61〜62 が水。x >= landX は陸（y 62 まで土、62 が草）。blocks で上書き
function ocean ({ landX = 10, blocks = {} } = {}) {
  const set = new Map(Object.entries(blocks))
  const blockAt = (p) => {
    const q = new Vec3(Math.floor(p.x), Math.floor(p.y), Math.floor(p.z))
    let name = set.get(`${q.x},${q.y},${q.z}`)
    if (!name) {
      if (q.x >= landX) name = q.y < 62 ? 'dirt' : q.y === 62 ? 'grass_block' : 'air'
      else name = q.y < 61 ? 'sand' : q.y <= 62 ? 'water' : 'air'
    }
    const b = Block.fromProperties(name, {}, 0)
    b.position = q
    return b
  }
  return { set, blockAt }
}

function swimmer (w, at = new Vec3(0.5, 62.4, 0.5)) {
  return {
    registry: md,
    entities: {},
    entity: { position: at, onGround: false, isInWater: true, yaw: 0 },
    time: { timeOfDay: 14000, age: 0 },
    health: 20,
    food: 20,
    blockAt: w.blockAt,
    heldItem: null,
    inventory: { items: () => [], slots: [] },
    setControlState: () => {},
    clearControlStates: () => {},
    look: async () => {},
    waitForTicks: async () => {}
  }
}
const state = () => ({ home: null, formerHomes: [], builds: {}, plan: null })

test('いちばん近い陸を探す: 水面の高さの前後で、立てる地面（上が 2 マス空き）', () => {
  const land = findLand(swimmer(ocean()))
  assert.ok(land)
  assert.equal(land.x, 10)
  assert.equal(land.y, 63) // 足を置くセル（地面 62 の上）
  assert.ok(land.distance >= 9 && land.distance <= 10)
})

test('陸がなければ null。1 マスだけの岩や柱は陸にしない', () => {
  assert.equal(findLand(swimmer(ocean({ landX: 1000 }))), null)
  const pillar = ocean({ landX: 1000, blocks: { '5,61,0': 'stone', '5,62,0': 'stone' } }) // 水面から出た 1 本の柱（上は空気）
  assert.equal(findLand(swimmer(pillar)), null)
})

test('行けなかった陸のまわりは外して、次に近い陸を探す', () => {
  const w = ocean({ landX: 10 })
  const bot = swimmer(w)
  const first = findLand(bot)
  const second = findLand(bot, { avoid: [first] })
  assert.ok(second === null || Math.hypot(second.x - first.x, second.z - first.z) > 6)
})

test('水の中にいれば reach_land を聞かずに実行する候補で出す。陸にいる・ボートに乗っているときは出さない', () => {
  const bot = swimmer(ocean())
  const c = forNeeds(bot, state(), k).find((x) => x.verb === 'reach_land')
  assert.ok(c)
  assert.equal(c.urgent, true)
  assert.match(c.id, /swim to the nearest land at 10,63,/)
  const onLand = swimmer(ocean(), new Vec3(12.5, 63, 0.5))
  onLand.entity.isInWater = false
  onLand.entity.onGround = true
  assert.equal(forNeeds(onLand, state(), k).some((x) => x.verb === 'reach_land'), false)
  const none = swimmer(ocean({ landX: 1000 }))
  assert.equal(forNeeds(none, state(), k).some((x) => x.verb === 'reach_land'), false, '陸が見えなければ出さない（explore などに任せる）')
})

test('findLandCached は同じ場所なら数秒は前の答え', () => {
  const bot = swimmer(ocean())
  const a = findLandCached(bot, 1000)
  assert.equal(findLandCached(bot, 2000), a)
  assert.notEqual(findLandCached(bot, 5000), a)
})

test('reach_land は陸にいれば何もしない。陸が見つからなければ、探した範囲と次の手を言う', async () => {
  const ac = new AbortController()
  const dry = swimmer(ocean(), new Vec3(12.5, 63, 0.5))
  dry.entity.isInWater = false
  dry.entity.onGround = true
  assert.equal(await PRIMITIVES.reach_land(dry, state(), {}, ac.signal), 'already on land')
  await assert.rejects(reachLand(swimmer(ocean({ landX: 1000 })), ac.signal), new RegExp(`no land within ${LAND_RADIUS} m`))
})

test('reach_land は水を泳いで渡る間、ダメージで止められない', () => {
  assert.ok(DAMAGE_TOLERANT.has('reach_land'))
})

test('水面より 2 ブロック以上高い岸は陸にしない（泳いで上がれない）。上がれる浅い岸を選ぶ', () => {
  // x >= 10 は y 62 の岸（水面の高さ）、x < -3 は y 66 まで続く崖。東の浅い岸が選ばれる
  const cliff = {}
  for (let x = -12; x < -3; x++) for (let z = -20; z <= 20; z++) for (let y = 63; y <= 66; y++) cliff[`${x},${y},${z}`] = 'stone'
  const w = ocean({ landX: 30, blocks: cliff })
  const found = findLand(swimmer(w, new Vec3(-2.5, 62.4, 0.5)))
  assert.ok(found && found.x >= 30, `climbable shore expected, got ${JSON.stringify(found)}`)
  // 岸がどこも高い崖なら null（見えていても陸と言わない）
  const walls = {}
  for (let x = -12; x <= 80; x++) for (let z = -60; z <= 60; z++) for (let y = 63; y <= 66; y++) if (x < -3 || x > 8) walls[`${x},${y},${z}`] = 'stone'
  const trapped = ocean({ landX: 1000, blocks: walls })
  assert.equal(findLand(swimmer(trapped, new Vec3(0.5, 62.4, 0.5)), { radius: 20 }), null)
})
