// 畑の水のマス（place_water）: バケツの視線が作物の枠にさえぎられると、水が穴に入らず畑の表面に流れた（設計書 136）。
// 立つマスをさえぎられないマスから選び、どこもさえぎられるときは、さえぎる作物を刈ってから置く。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { PRIMITIVES } from '../src/primitives.mjs'

const { Vec3 } = vec3Pkg
const v = (x, y, z) => new Vec3(x, y, z)
const key = (p) => `${p.x},${p.y},${p.z}`
const HOLE = v(44, 77, -462)
const signal = new AbortController().signal

// 地面は y <= 77 の土（穴のマスは草ブロック）。blocks に上書き。作物は { name: 'wheat', age }
function fakeBot (blocks, start = v(48.5, 78, -462.5)) {
  const items = [{ name: 'water_bucket', count: 1 }]
  const bot = {
    entity: { position: start, eyeHeight: 1.62, yaw: 0, pitch: 0 },
    entities: {},
    dug: [],
    walked: [],
    blockAt: (p) => {
      const spec = blocks[key(p)] ?? (p.y <= 77 ? { name: 'dirt' } : { name: 'air' })
      const solid = spec.name === 'dirt' || spec.name === 'grass_block'
      return {
        name: spec.name,
        position: p,
        boundingBox: solid ? 'block' : 'empty',
        shapes: solid ? [[0, 0, 0, 1, 1, 1]] : [],
        getProperties: () => (spec.age !== undefined ? { age: String(spec.age) } : {})
      }
    },
    inventory: { items: () => items.filter((i) => i.count > 0) },
    equip: async () => {},
    lookAt: async () => {},
    waitForTicks: async () => {},
    dig: async (b) => { bot.dug.push(key(b.position)); blocks[key(b.position)] = { name: 'air' } },
    activateItem: () => {
      // 視線が作物にさえぎられていると、水は穴でなく手前に出る（実機の失敗）
      const w = bot.entity.position
      const target = bot.blockedWhenPressed?.() ? v(Math.floor(w.x), 78, Math.floor(w.z)) : HOLE
      blocks[key(target)] = { name: 'water' }
      items[0] = { name: 'bucket', count: 1 }
    },
    pathfinder: {
      bestHarvestTool: () => null,
      goto: async (g) => { bot.walked.push(`${g.x},${g.y},${g.z}`); bot.entity.position = new Vec3(g.x + 0.5, g.y, g.z + 0.5) },
      setGoal: () => {}
    }
  }
  return bot
}
const ripe = (...cells) => Object.fromEntries(cells.map(([x, y, z]) => [`${x},${y},${z}`, { name: 'wheat', age: 7 }]))

test('立つマスは、視線がさえぎられないマスを選ぶ（近い順より先）', async () => {
  // 東（ボットに近い）の足のマスに熟した小麦。西は何もない
  const blocks = { [key(HOLE)]: { name: 'grass_block' }, ...ripe([45, 78, -462]) }
  const bot = fakeBot(blocks)
  await PRIMITIVES.place_water(bot, {}, { pos: HOLE }, signal)
  assert.notEqual(bot.walked[0], '45,78,-462', '小麦のあるマスには立たない（東が一番近いが、そこの熟した小麦に線が当たる）')
  assert.deepEqual(bot.dug, [key(HOLE)], '小麦は刈らず、穴だけ掘る')
  assert.equal(blocks[key(HOLE)].name, 'water')
})

test('どの横のマスでもさえぎられるなら、さえぎる小麦を刈ってから置く', async () => {
  const blocks = { [key(HOLE)]: { name: 'grass_block' }, ...ripe([45, 78, -462], [43, 78, -462], [44, 78, -461], [44, 78, -463]) }
  const bot = fakeBot(blocks)
  await PRIMITIVES.place_water(bot, {}, { pos: HOLE }, signal)
  assert.ok(bot.dug.some((k) => /^(45|43),78,-462$|^44,78,-46[13]$/.test(k)), `小麦を刈った: ${bot.dug}`)
  assert.equal(blocks[key(HOLE)].name, 'water')
})
