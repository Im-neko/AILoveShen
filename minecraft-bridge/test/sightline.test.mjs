// 目から先への視線（設計書 136）: サーバーのレイは小麦・草などの選択枠に当たるが、Mineflayer のレイは素通りする。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { firstBlockBetween, lookingAt, blockedNote, outlineOf, standSpotsBeside } from '../src/sightline.mjs'
import { inspectCell, inspectSelf } from '../src/inspect.mjs'
import { PRIMITIVES } from '../src/primitives.mjs'

const { Vec3 } = vec3Pkg
const v = (x, y, z) => new Vec3(x, y, z)

// blocks: 'x,y,z' → { name, age? }。ない所は空気。y < 74 は土
function world (blocks = {}) {
  return (p) => {
    const spec = blocks[`${p.x},${p.y},${p.z}`] ?? (p.y < 74 ? { name: 'dirt' } : { name: 'air' })
    const solid = spec.name === 'dirt' || spec.name === 'stone' || spec.name === 'farmland'
    return {
      name: spec.name,
      position: p,
      boundingBox: solid ? 'block' : 'empty',
      shapes: solid ? [[0, 0, 0, 1, 1, 1]] : [],
      getProperties: () => (spec.age !== undefined ? { age: String(spec.age) } : {})
    }
  }
}

// 畑: (44,74,-478) が水。ボットは (46.5, 74, -475.5)、水の中心を狙う。間のマスは (45,74,-477)
const WATER = v(44, 74, -478)
const wheat = (age, ...cells) => Object.fromEntries(cells.map(([x, z]) => [`${x},74,${z}`, { name: 'wheat', age }]))
const botAt = (blocks, feet = v(46.5, 74, -475.5), yaw = 0, pitch = 0) => ({
  entity: { position: feet, eyeHeight: 1.62, yaw, pitch },
  blockAt: world({ [`${WATER.x},${WATER.y},${WATER.z}`]: { name: 'water' }, ...blocks })
})

test('熟した小麦が間にあれば、その小麦を名指しし、横に立てば通る所を言う', () => {
  const crops = wheat(7, [45, -477], [45, -478], [46, -476])
  const bot = botAt(crops)
  const hit = firstBlockBetween(bot, v(46.5, 75.62, -475.5), v(44.5, 74.5, -477.5), WATER)
  assert.deepEqual(hit, { name: 'wheat', x: 45, y: 74, z: -477, age: 7 })
  const note = blockedNote(bot, WATER)
  assert.match(note, /the wheat \(age 7\) at 45,74,-477 is in the line between your eyes and 44,74,-478/)
  assert.match(note, /goto 45,74,-478 \(a cell beside it where the line is clear\), then use the bucket at 44,74,-478 again/)
})

test('植えたばかりの低い小麦は、線の下を通るので邪魔にならない', () => {
  const bot = botAt(wheat(0, [45, -477]))
  assert.equal(firstBlockBetween(bot, v(46.5, 75.62, -475.5), v(44.5, 74.5, -477.5), WATER), null)
  assert.equal(blockedNote(bot, WATER), '')
})

test('水の隣に立てば、間に何もなく、立つマスの小麦の上を線が越える（目が小麦の高さより上で、すぐ降りる）', () => {
  const bot = botAt(wheat(7, [45, -477], [45, -478]), v(45.5, 74, -477.5))
  assert.equal(firstBlockBetween(bot, v(45.5, 75.62, -477.5), v(44.5, 74.5, -477.5), WATER), null)
})

test('石が間にあれば（当たり判定のあるブロックは今までどおり）それを言う', () => {
  const bot = botAt({ '45,74,-477': { name: 'stone' } })
  assert.equal(firstBlockBetween(bot, v(46.5, 75.62, -475.5), v(44.5, 74.5, -477.5), WATER)?.name, 'stone')
})

test('表にない通り抜けられるブロックは、1 マスまるごと当たるものとして言う', () => {
  const bot = botAt({ '45,74,-477': { name: 'tall_grass' } })
  assert.equal(firstBlockBetween(bot, v(46.5, 75.62, -475.5), v(44.5, 74.5, -477.5), WATER)?.name, 'tall_grass')
})

test('空気と水は通り抜ける', () => {
  const bot = botAt({ '45,74,-477': { name: 'water' } })
  assert.equal(firstBlockBetween(bot, v(46.5, 75.62, -475.5), v(44.5, 74.5, -477.5), WATER), null)
})

test('選択枠の高さは作物と成長段階で決まる（Paper 1.21.4 の SHAPE_BY_AGE）', () => {
  const top = (name, age) => outlineOf({ name, getProperties: () => ({ age: String(age) }) })[0][4]
  assert.equal(top('wheat', 0), 2 / 16)
  assert.equal(top('wheat', 7), 1)
  assert.equal(top('carrots', 7), 9 / 16)
  assert.equal(top('beetroots', 3), 8 / 16)
  assert.equal(outlineOf({ name: 'oak_sapling' })[0][4], 12 / 16)
  assert.equal(outlineOf({ name: 'stone' }), null)
})

test('lookingAt: 水の手前に小麦があれば、正面は小麦（Mineflayer の blockAtCursor は水の先を見る）', () => {
  // 西南西へ。水の中心へ向く
  const feet = v(46.5, 74, -475.5)
  const dx = 44.5 - 46.5
  const dy = 74.5 - 75.62
  const dz = -477.5 + 475.5
  const yaw = Math.atan2(-dx, -dz)
  const pitch = Math.atan2(dy, Math.hypot(dx, dz))
  const bot = botAt(wheat(7, [45, -477]), feet, yaw, pitch)
  assert.deepEqual(lookingAt(bot, 5), { name: 'wheat', x: 45, y: 74, z: -477 })
  // 小麦がなければ、水を通り抜けて向こうの地面
  assert.equal(lookingAt(botAt({}, feet, yaw, pitch), 5)?.name, 'dirt')
})

test('inspect: in_the_way と looking_at が小麦を言う', () => {
  const feet = v(46.5, 74, -475.5)
  const bot = {
    ...botAt(wheat(7, [45, -477]), feet),
    entities: {},
    registry: { blocksByName: {} },
    controlState: {}
  }
  const cell = inspectCell(bot, { home: null, formerHomes: [], plan: null, memory: {} }, WATER)
  assert.deepEqual(cell.in_the_way, { block: 'wheat', x: 45, y: 74, z: -477, age: 7 })
})

// use_item: 空のバケツを水に使って何も起きなかったとき、間の小麦を言う
function bucketBot (blocks) {
  const bot = {
    ...botAt(blocks),
    entities: {},
    heldItem: null,
    inventory: { items: () => [{ name: 'bucket', count: 1, type: 1 }] },
    equip: async () => {},
    lookAt: async () => {},
    activateItem: () => {},
    waitForTicks: async () => {},
    setControlState () {}
  }
  return bot
}

test('use_item: バケツが効かなかったとき、間の小麦と、横に立てば通る所を言う', async () => {
  const bot = bucketBot(wheat(7, [45, -477], [45, -478], [46, -476]))
  await assert.rejects(
    PRIMITIVES.use_item(bot, {}, { item: 'bucket', pos: WATER }, new AbortController().signal),
    /nothing changed after using bucket at water 44,74,-478 \(the wheat \(age 7\) at 45,74,-477 is in the line between your eyes and 44,74,-478.*goto 45,74,-478 .*then use the bucket at 44,74,-478 again\)/
  )
})

test('use_item: 間に何もなければ、今までの文のまま', async () => {
  const bot = bucketBot({})
  await assert.rejects(
    PRIMITIVES.use_item(bot, {}, { item: 'bucket', pos: WATER }, new AbortController().signal),
    (e) => e.message === 'nothing changed after using bucket at water 44,74,-478'
  )
})

test('inspect {}: looking_at は選択枠のあるブロックも言う', () => {
  const bot = { ...botAt(wheat(7, [46, -477]), v(46.5, 74, -475.5), 0, -0.4), controlState: {}, inventory: { items: () => [] } }
  const me = inspectSelf(bot).self
  assert.equal(me.looking_at?.block, 'wheat')
})

// 畑 2 の形: 水のマスは地面の高さの穴（y=77）、作物はその上（y=78）、ボットは地面の上（足が y=78）に立つ
test('水のマスが地面の高さの穴なら、横の地面の上（1 つ上）に歩くよう言う', () => {
  const hole = v(44, 77, -462)
  const blocks = { '44,77,-462': { name: 'water' }, '44,78,-463': { name: 'wheat', age: 5 }, '45,78,-462': { name: 'wheat', age: 5 } }
  const at = (p) => {
    const spec = blocks[`${p.x},${p.y},${p.z}`] ?? (p.y <= 77 ? { name: 'dirt' } : { name: 'air' })
    const solid = spec.name === 'dirt'
    return { name: spec.name, position: p, boundingBox: solid ? 'block' : 'empty', shapes: solid ? [[0, 0, 0, 1, 1, 1]] : [], getProperties: () => (spec.age !== undefined ? { age: String(spec.age) } : {}) }
  }
  at.hole = true
  const bot = { entity: { position: v(44.5, 78, -464.5), eyeHeight: 1.62 }, blockAt: at }
  const note = blockedNote(bot, hole)
  assert.match(note, /wheat \(age 5\) at 44,78,-463 is in the line/)
  assert.match(note, /goto (45|43|44),78,-46[123] .*then use the bucket at 44,77,-462 again/)
  assert.equal(standSpotsBeside(bot, hole).every((p) => p.y === 78), true)
})

test('目のあるマスの背の高い小麦も数える: 畑 2 で、立つマスの熟した小麦に水のバケツが当たって水が流れた', () => {
  const blocks = { '44,77,-462': { name: 'water' }, '45,78,-462': { name: 'wheat', age: 7 } }
  const at = (p) => {
    const spec = blocks[`${p.x},${p.y},${p.z}`] ?? (p.y <= 77 ? { name: 'dirt' } : { name: 'air' })
    const solid = spec.name === 'dirt'
    return { name: spec.name, position: p, boundingBox: solid ? 'block' : 'empty', shapes: solid ? [[0, 0, 0, 1, 1, 1]] : [], getProperties: () => (spec.age !== undefined ? { age: String(spec.age) } : {}) }
  }
  const bot = { entity: { position: v(45.5, 78, -461.5), eyeHeight: 1.62 }, blockAt: at }
  const eye = v(45.5, 79.62, -461.5)
  const aim = v(44.5, 77.05, -461.5)
  assert.deepEqual(firstBlockBetween(bot, eye, aim, v(44, 77, -462)), { name: 'wheat', x: 45, y: 78, z: -462, age: 7 })
  // 低い小麦なら目より下で、線の上を通る
  blocks['45,78,-462'] = { name: 'wheat', age: 1 }
  assert.equal(firstBlockBetween(bot, eye, aim, v(44, 77, -462)), null)
})
