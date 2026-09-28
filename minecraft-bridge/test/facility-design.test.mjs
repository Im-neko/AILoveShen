import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { registerFacility, layoutStatus, nextCell, updateStatus, adoptExisting, FacilityError } from '../src/facilities.mjs'
import { makeGoal, evaluate } from '../src/goals.mjs'
import { Knowledge } from '../src/knowledge.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')

// 地面は y 63 の草（上は空気）。家は (0,64,0) のまわり 3x3。x >= 40 は読み込まれていない
function world (blocks = {}, items = []) {
  const at = (p) => blocks[`${p.x},${p.y},${p.z}`]
  return {
    registry: md,
    entity: { position: new Vec3(10, 64, 0) },
    inventory: { items: () => items },
    time: { timeOfDay: 1000, age: 0 },
    blockAt: (p) => {
      if (p.x >= 40) return null
      const b = at(p)
      if (b) return { name: b.name ?? b, boundingBox: /_sapling|wheat|air|short_grass/.test(b.name ?? b) ? 'empty' : 'block', position: p, getProperties: () => ({ age: b.age ?? 0 }) }
      if (p.y === 63) return { name: 'grass_block', boundingBox: 'block', position: p }
      if (p.y < 63) return { name: 'dirt', boundingBox: 'block', position: p }
      return { name: 'air', boundingBox: 'empty', position: p }
    },
    findBlocks: ({ matching }) => Object.entries(blocks)
      .filter(([, b]) => [].concat(matching).includes(md.blocksByName[b.name ?? b]?.id))
      .map(([k]) => new Vec3(...k.split(',').map(Number)))
  }
}
const home = { inside: new Vec3(0, 64, 0), min: new Vec3(-1, 64, -1), max: new Vec3(1, 64, 1), door: new Vec3(0, 64, 2), outside: new Vec3(0, 64, 3) }
const state = (extra = {}) => ({ home, plan: null, builds: {}, formerHomes: [], ...extra })
const ZONE = [{ min_x: 8, min_z: 0, max_x: 15, max_z: 7 }]

test('畑の設計: 区画の中に幅 x 奥行きの耕せるマスを選ぶ（草花の上も耕せる）', () => {
  const s = state()
  const bot = world({ '8,64,0': 'short_grass' })
  const fac = registerFacility(bot, s, 'wheat_farm', { kind: 'farm', zones: ['B1'], boxes: ZONE, design: { crop: 'wheat_seeds', width: 3, depth: 2 } })
  assert.equal(fac.layout.crop, 'wheat')
  assert.equal(fac.layout.cells.length, 6)
  assert.ok(fac.layout.cells.every((c) => c.y === 63 && c.x >= 8 && c.x <= 15 && c.z >= 0 && c.z <= 7))
  assert.equal(fac.status, 'planned')
  assert.equal(s.facilities.wheat_farm.zone, 'B1')
  assert.throws(() => registerFacility(bot, s, 'wheat_farm', { kind: 'farm', boxes: ZONE, design: { width: 2, depth: 2 } }), /already/)
})

test('畑の設計を断る: 大きすぎる（直せる）、読み込まれていない（世界の状態: world）', () => {
  const bot = world({ '10,63,2': 'stone', '11,63,2': 'stone', '12,63,2': 'stone', '13,63,2': 'stone' })
  assert.throws(() => registerFacility(bot, state(), 'ff', { kind: 'farm', boxes: ZONE, design: { width: 17, depth: 2 } }), /width must be/)
  try {
    registerFacility(bot, state(), 'far', { kind: 'farm', boxes: [{ min_x: 40, min_z: 0, max_x: 47, max_z: 7 }], design: { width: 3, depth: 3 } })
    assert.fail('should refuse')
  } catch (e) {
    assert.ok(e instanceof FacilityError && e.world)
  }
})

test('植林場の設計: 間隔 4 以上の格子、足りなければ理由（4 未満は断る）', () => {
  const bot = world()
  const fac = registerFacility(bot, state(), 'oak_grove', { kind: 'tree_farm', boxes: ZONE, design: { sapling: 'oak_sapling', count: 4, spacing: 4 } })
  assert.equal(fac.layout.cells.length, 4)
  const [a, b] = fac.layout.cells
  assert.ok(Math.abs(a.x - b.x) % 4 === 0 && Math.abs(a.z - b.z) % 4 === 0)
  assert.ok(fac.layout.cells.every((c) => c.y === 64))
  assert.throws(() => registerFacility(bot, state(), 'gg', { kind: 'tree_farm', boxes: ZONE, design: { count: 9, spacing: 4 } }), /only 4 places/)
  assert.throws(() => registerFacility(bot, state(), 'hh', { kind: 'tree_farm', boxes: ZONE, design: { count: 2, spacing: 2 } }), /spacing must be 4-6/)
})

test('施設の今: 耕す・まく・済んだマス、次のマスは近い順、全部済めば done', () => {
  const s = state()
  const fac = registerFacility(world(), s, 'wheat_farm', { kind: 'farm', boxes: ZONE, design: { width: 2, depth: 1 } })
  const [c1, c2] = fac.layout.cells
  const key = (c, dy = 0) => `${c.x},${c.y + dy},${c.z}`
  let bot = world({ [key(c1)]: 'farmland' })
  const st = layoutStatus(bot, fac)
  assert.deepEqual([st.done, st.sow.length, st.till.length], [0, 1, 1])
  assert.deepEqual(nextCell(bot, fac, 'till'), new Vec3(c2.x, c2.y, c2.z))
  bot = world({ [key(c1)]: 'farmland', [key(c1, 1)]: 'wheat', [key(c2)]: 'farmland', [key(c2, 1)]: { name: 'wheat', age: 7 } })
  assert.equal(updateStatus(bot, fac), true)
  assert.equal(fac.status, 'done')
})

test('facility(name) の目標: まだない名前は「まだ」、耕す・まく葉、設計した畑は家の畑に数えない', () => {
  const k = new Knowledge(md)
  const s = state()
  assert.throws(() => makeGoal({ predicate: 'facility', name: 'wheat_farm' }, world(), s, k), /no facility named wheat_farm/)
  const fac = registerFacility(world(), s, 'wheat_farm', { kind: 'farm', boxes: ZONE, design: { width: 2, depth: 1 } })
  const [c1] = fac.layout.cells
  const bot = world({ [`${c1.x},${c1.y},${c1.z}`]: 'farmland' }, [{ name: 'wooden_hoe', count: 1 }, { name: 'wheat_seeds', count: 3 }])
  s.goal = makeGoal({ predicate: 'facility', name: 'wheat_farm' }, bot, s, k)
  const r = evaluate(bot, s, k, {})
  assert.equal(r.met, false)
  assert.equal(r.remaining, 2)
  assert.deepEqual(r.leaves.map((l) => [l.kind, l.facility]), [['sow', 'wheat_farm'], ['till', 'wheat_farm']])
  adoptExisting(bot, s)
  assert.equal(s.facilities.home_farm, undefined, '設計した畑の耕地は home_farm にしない')
})
