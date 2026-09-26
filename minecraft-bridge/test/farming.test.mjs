import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { plantingStatus, recordPlanting, cropStatus, plantSpot, tillSpot, sowSpot, farmRefusal, cropOf } from '../src/farming.mjs'
import { Knowledge } from '../src/knowledge.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')

// 地面は y 63 の草（上は空気）。家は (0,64,0) のまわり 3x3
function world (blocks = {}) {
  const at = (p) => blocks[`${p.x},${p.y},${p.z}`]
  return {
    registry: md,
    entity: { position: new Vec3(10, 64, 0) },
    inventory: { items: () => [] },
    blockAt: (p) => {
      const b = at(p)
      if (b) return { name: b.name ?? b, boundingBox: b.bbox ?? (/_sapling|wheat|air/.test(b.name ?? b) ? 'empty' : 'block'), position: p, getProperties: () => ({ age: b.age ?? 0 }) }
      if (p.y === 63) return { name: 'grass_block', boundingBox: 'block', position: p }
      if (p.y < 63) return { name: 'dirt', boundingBox: 'block', position: p }
      return { name: 'air', boundingBox: 'empty', position: p }
    },
    findBlocks: ({ matching }) => Object.entries(blocks)
      .filter(([, b]) => [].concat(matching).includes(md.blocksByName[b.name ?? b]?.id))
      .map(([k]) => new Vec3(...k.split(',').map(Number)))
  }
}
const home = { inside: new Vec3(0, 64, 0), min: new Vec3(-1, 64, -1), max: new Vec3(1, 64, 1), door: new Vec3(0, 64, 2) }
const state = (extra = {}) => ({ home, plan: null, builds: {}, formerHomes: [], ...extra })

test('植えた場所に苗木か育った木があれば数える（なくなったものは数えない）', () => {
  const s = state()
  recordPlanting(s, new Vec3(8, 64, 0), 'oak_sapling')
  recordPlanting(s, new Vec3(12, 64, 0), 'oak_sapling')
  recordPlanting(s, new Vec3(16, 64, 0), 'birch_sapling')
  const bot = world({ '8,64,0': 'oak_sapling', '12,64,0': 'oak_log' })
  assert.deepEqual(plantingStatus(bot, s), { saplings: 1, trees: 1, unseen: 0, gone: 1 })
  assert.deepEqual(plantingStatus(bot, s, 'oak_sapling'), { saplings: 1, trees: 1, unseen: 0, gone: 0 })
})

test('畑の作物は耕地の上のものだけ、育ちきったものを分けて数える', () => {
  const bot = world({
    '8,63,0': 'farmland', '8,64,0': { name: 'wheat', age: 7 },
    '9,63,0': 'farmland', '9,64,0': { name: 'wheat', age: 2 },
    '12,64,0': { name: 'wheat', age: 7 } // 耕地の上でない
  })
  const c = cropStatus(bot, state(), 'wheat')
  assert.equal(c.planted, 2)
  assert.equal(c.mature, 1)
  assert.equal(cropOf('carrot'), 'carrots')
})

test('植える・耕す場所: 家から 6 m 以上、木から離れた所、畑は今の畑の隣を先に', () => {
  const s = state()
  const bot = world({ '7,63,0': 'farmland', '20,64,0': 'oak_log' })
  const p = plantSpot(bot, s)
  assert.ok(Math.hypot(p.x, p.z) >= 6)
  assert.equal(p.y, 64)
  const t = tillSpot(bot, s)
  assert.equal(t.y, 63)
  assert.equal(Math.abs(t.x - 7) + Math.abs(t.z), 1) // 耕地の隣
  assert.deepEqual(sowSpot(bot, s), new Vec3(7, 63, 0))
  assert.match(farmRefusal(s, new Vec3(1, 64, 2)), /home/)
  assert.equal(farmRefusal(s, new Vec3(10, 64, 0)), null)
})

test('苗木は葉から、種は草から手に入る（確率なので、掘って落ちなくても失敗にしない）', () => {
  const k = new Knowledge(md)
  assert.deepEqual(k.blockSources.get('oak_sapling'), ['oak_leaves'])
  assert.ok(k.blockSources.get('wheat_seeds').includes('short_grass'))
  assert.ok(k.resolve('sapling').members.includes('birch_sapling'))
  assert.ok(k.resolve('hoe').members.includes('wooden_hoe'))
})

test('planted と farmed は家があるときの目標・条件になる（数と種類を確かめる）', async () => {
  const { makeGoal, CONDITION_PREDICATES } = await import('../src/goals.mjs')
  const k = new Knowledge(md)
  const bot = world()
  const s = state()
  assert.deepEqual(makeGoal({ predicate: 'planted', item: 'sapling', count: 4 }, bot, s, k).spec, { predicate: 'planted', item: 'sapling', count: 4 })
  assert.deepEqual(makeGoal({ predicate: 'farmed', item: 'wheat_seeds', count: 9 }, bot, s, k).spec, { predicate: 'farmed', item: 'wheat', count: 9 })
  assert.throws(() => makeGoal({ predicate: 'planted', item: 'oak_log', count: 1 }, bot, s, k), /sapling/)
  assert.throws(() => makeGoal({ predicate: 'farmed', item: 'melon', count: 1 }, bot, s, k), /farmed takes/)
  assert.throws(() => makeGoal({ predicate: 'planted', item: 'sapling', count: 1 }, bot, state({ home: null }), k), /no home/)
  assert.ok(CONDITION_PREDICATES.includes('planted') && CONDITION_PREDICATES.includes('farmed'))
})

test('草は届く距離まで近づいて掘る（見える位置を探すと決して着かず、種集めが毎回失敗した）', async () => {
  const { PRIMITIVES } = await import('../src/primitives.mjs')
  const goals = []
  const bot = {
    entity: { position: new Vec3(0, 64, 0), onGround: true },
    entities: {},
    blockAt: (p) => ({ name: 'short_grass', boundingBox: 'empty', position: p }),
    dig: async () => {},
    equip: async () => {},
    waitForTicks: async () => {},
    inventory: { items: () => [], emptySlotCount: () => 30 },
    pathfinder: { goto: async (g) => goals.push(g.constructor.name), setGoal: () => {} }
  }
  const r = await PRIMITIVES.dig(bot, { unreachableDrops: new Set() }, { block: 'short_grass', pos: new Vec3(3, 64, 0) }, new AbortController().signal)
  assert.equal(goals[0], 'GoalNear')
  assert.match(r, /nothing dropped this time/) // 種はときどきしか落ちない: 失敗ではない
})
