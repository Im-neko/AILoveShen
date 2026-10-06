import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { registerFacility, layoutStatus, nextCell, updateStatus, adoptExisting, FacilityError, cropFarms, farmSummary, workCell, waterAt } from '../src/facilities.mjs'
import { snapshot } from '../src/world.mjs'
import { makeGoal, evaluate } from '../src/goals.mjs'
import { Knowledge } from '../src/knowledge.mjs'
import { createTools } from '../src/tools.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')

// 地面は y 63 の草（上は空気）。家は (0,64,0) のまわり 3x3。x >= 40 は読み込まれていない
function world (blocks = {}, items = []) {
  const at = (p) => blocks[`${p.x},${p.y},${p.z}`]
  return {
    registry: md,
    entity: { position: new Vec3(10, 64, 0) },
    inventory: { items: () => items },
    entities: {},
    unlocked: () => true,
    findBlock: () => null,
    time: { timeOfDay: 1000, age: 0 },
    blockAt: (p) => {
      if (p.x >= 40) return null
      const b = at(p)
      if (b) return { name: b.name ?? b, boundingBox: /_sapling|wheat|air|short_grass/.test(b.name ?? b) ? 'empty' : 'block', position: p, getProperties: () => ({ age: b.age ?? 0 }) }
      if (p.y === 63) return { name: 'grass_block', boundingBox: 'block', position: p }
      if (p.y < 63) return { name: 'dirt', boundingBox: 'block', position: p }
      return { name: 'air', boundingBox: 'empty', position: p }
    },
    findBlocks: ({ point, matching, maxDistance = Infinity }) => Object.entries(blocks)
      .filter(([, b]) => [].concat(matching).includes(md.blocksByName[b.name ?? b]?.id))
      .map(([k]) => new Vec3(...k.split(',').map(Number)))
      .filter((p) => p.distanceTo(point) <= maxDistance)
  }
}
// 畑のまわりの柵とゲートが立っている世界のブロック（設計書 75）
const fenced = (fac) => ({
  ...Object.fromEntries(fac.layout.fence.map((c) => [`${c.x},${c.y},${c.z}`, 'oak_fence'])),
  [`${fac.layout.gate.x},${fac.layout.gate.y},${fac.layout.gate.z}`]: 'oak_fence_gate'
})
const home = { inside: new Vec3(0, 64, 0), min: new Vec3(-1, 64, -1), max: new Vec3(1, 64, 1), door: new Vec3(0, 64, 2), outside: new Vec3(0, 64, 3) }
const state = (extra = {}) => ({ home, plan: null, builds: {}, formerHomes: [], recipeBook: { recipesFor: () => [1] }, ...extra })
const ZONE = [{ min_x: 8, min_z: 0, max_x: 15, max_z: 7 }]

test('畑の設計: 区画の中に幅 x 奥行きの耕せるマスを選ぶ（草花の上も耕せる）', () => {
  const s = state()
  const bot = world({ '8,64,0': 'short_grass' })
  const fac = registerFacility(bot, s, 'wheat_farm', { kind: 'farm', zones: ['B1'], boxes: ZONE, design: { crop: 'wheat_seeds', width: 3, depth: 2 } })
  assert.equal(fac.layout.crop, 'wheat')
  // 9 x 9 ごとに 1 マスは水のマス（3 x 2 は 5 マスが作物）。水のマスは作物のマスに含まない
  assert.equal(fac.layout.cells.length, 5)
  assert.equal(fac.layout.water.length, 1)
  assert.ok([...fac.layout.cells, ...fac.layout.water].every((c) => c.y === 63 && c.x >= 8 && c.x <= 15 && c.z >= 0 && c.z <= 7))
  assert.ok(!fac.layout.cells.some((c) => fac.layout.water.some((w) => w.x === c.x && w.z === c.z)))
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
  // 頼んだ数に半分以上あれば、ある所だけで作る
  assert.equal(registerFacility(bot, state(), 'short', { kind: 'tree_farm', boxes: ZONE, design: { sapling: 'oak_sapling', count: 7, spacing: 4 } }).layout.cells.length, 4)
  assert.throws(() => registerFacility(bot, state(), 'gg2', { kind: 'tree_farm', boxes: ZONE, design: { count: 9, spacing: 4 } }), /at least 5 are needed/)
  assert.throws(() => registerFacility(bot, state(), 'hh', { kind: 'tree_farm', boxes: ZONE, design: { count: 2, spacing: 2 } }), /spacing must be 4-6/)
})

test('施設の今: 耕す・まく・済んだマス、次のマスは近い順、全部済めば done', () => {
  const s = state()
  // 3 x 1: まん中が水のマス、両端が作物のマス
  const fac = registerFacility(world(), s, 'wheat_farm', { kind: 'farm', boxes: ZONE, design: { width: 3, depth: 1 } })
  const [c1, c2] = fac.layout.cells
  const [w1] = fac.layout.water
  assert.equal(fac.layout.cells.length, 2)
  assert.deepEqual([w1.x - c1.x, w1.x - c2.x].sort(), [-1, 1])
  const key = (c, dy = 0) => `${c.x},${c.y + dy},${c.z}`
  let bot = world({ [key(c1)]: 'farmland' })
  const st = layoutStatus(bot, fac)
  // 水のマスは耕すマスに数えない（数えると、水になったマスを耕せず、畑が完成しない）
  assert.deepEqual([st.total, st.done, st.sow.length, st.till.length, st.water.length], [3, 0, 1, 1, 1])
  assert.deepEqual(nextCell(bot, fac, 'till'), new Vec3(c2.x, c2.y, c2.z))
  assert.deepEqual(nextCell(bot, fac, 'water'), new Vec3(w1.x, w1.y, w1.z))
  const done = { ...fenced(fac), [key(c1)]: 'farmland', [key(c1, 1)]: 'wheat', [key(c2)]: 'farmland', [key(c2, 1)]: { name: 'wheat', age: 7 } }
  // 作物が全部植わっても、水がなければ完成でない
  bot = world(done)
  assert.equal(updateStatus(bot, fac), true)
  assert.equal(fac.status, 'building')
  bot = world({ ...done, [key(w1)]: 'water' })
  assert.equal(updateStatus(bot, fac), true)
  assert.equal(fac.status, 'done')
  assert.equal(layoutStatus(bot, fac).water.length, 0)
  // 柵が欠けていれば、作物と水がそろっても完成でない
  const [gap] = fac.layout.fence
  bot = world({ ...done, [key(w1)]: 'water', [`${gap.x},${gap.y},${gap.z}`]: 'air' })
  assert.equal(updateStatus(bot, fac), true)
  assert.equal(fac.status, 'building')
  assert.deepEqual(layoutStatus(bot, fac).fence, [new Vec3(gap.x, gap.y, gap.z)])
})

test('facility(name) の目標: まだない名前は「まだ」、耕す・まく葉、設計した畑は家の畑に数えない', () => {
  const k = new Knowledge(md)
  const s = state()
  assert.throws(() => makeGoal({ predicate: 'facility', name: 'wheat_farm' }, world(), s, k), /no facility named wheat_farm/)
  const fac = registerFacility(world(), s, 'wheat_farm', { kind: 'farm', boxes: ZONE, design: { width: 3, depth: 1 } })
  const [c1] = fac.layout.cells
  const bot = world({ [`${c1.x},${c1.y},${c1.z}`]: 'farmland' }, [{ name: 'wooden_hoe', count: 1 }, { name: 'wheat_seeds', count: 3 }, { name: 'water_bucket', count: 1 }, { name: 'oak_fence', count: 20 }, { name: 'oak_fence_gate', count: 1 }])
  s.goal = makeGoal({ predicate: 'facility', name: 'wheat_farm' }, bot, s, k)
  const r = evaluate(bot, s, k, {})
  assert.equal(r.met, false)
  assert.equal(r.remaining, 3 + fac.layout.fence.length + 1) // 柵の輪とゲートも残り
  // 水のマスが先（水のバケツを持っているとき）。そのあと、まく・耕す。柵とゲートは、畑のマスが全部済んでから
  assert.deepEqual(r.leaves.map((l) => [l.kind, l.facility]), [['water', 'wheat_farm'], ['sow', 'wheat_farm'], ['till', 'wheat_farm']])
  adoptExisting(bot, s)
  assert.equal(s.facilities.home_farm, undefined, '設計した畑の耕地は home_farm にしない')
})

// 家から 56 m の区画（家のまわり 32 m の外）
const FAR = [{ min_x: -60, min_z: 0, max_x: -53, max_z: 7 }]

test('farmed は家から遠い設計した畑も数える（家のまわりだけを数えると、区画の畑が見えなかった）', () => {
  const k = new Knowledge(md)
  const s = state({ memory: {} })
  const fac = registerFacility(world(), s, 'far_wheat', { kind: 'farm', boxes: FAR, design: { width: 2, depth: 2 } }) // 水 1 と作物 3
  const [c1, c2] = fac.layout.cells
  const crops = {
    [`${c1.x},${c1.y},${c1.z}`]: 'farmland', [`${c1.x},${c1.y + 1},${c1.z}`]: { name: 'wheat', age: 7 },
    [`${c2.x},${c2.y},${c2.z}`]: 'farmland', [`${c2.x},${c2.y + 1},${c2.z}`]: { name: 'wheat', age: 3 },
    '7,63,0': 'farmland', '7,64,0': { name: 'wheat', age: 1 } // 前からある家の畑
  }
  const bot = world(crops, [{ name: 'wheat_seeds', count: 4 }, { name: 'wooden_hoe', count: 1 }])
  const f = cropFarms(bot, s, 'wheat')
  assert.deepEqual([f.planted, f.ripe, f.free, f.water], [3, 1, 1, 1])
  assert.deepEqual(f.farms.map((x) => [x.name, x.planted]), [['far_wheat', 2], ['home_farm', 1]])
  assert.deepEqual(farmSummary(bot, s).wheat.farms, ['far_wheat', 'home_farm'])
  assert.equal(snapshot(bot, s).farm('wheat').ripe, 1)

  s.goal = makeGoal({ predicate: 'farmed', item: 'wheat', count: 5 }, bot, s, k)
  const r = evaluate(bot, s, k, {})
  assert.equal(r.remaining, 2)
  assert.deepEqual(r.leaves.filter((l) => l.facility).map((l) => [l.kind, l.facility]), [['till', 'far_wheat']])
  assert.match(r.lines.join('\n'), /wheat planted on the farms: 3\/5 \(ripe 1, free cells 1; far_wheat 2, home_farm 1\)/)

  // 見えなくなっても、最後に見た数で数える（歩いても残りが変わらない）
  const away = { ...world(), blockAt: () => null, entity: { position: new Vec3(300, 64, 0) } }
  assert.equal(cropFarms(away, s, 'wheat').planted, 3)
  assert.equal(cropFarms(away, s, 'wheat').remembered, true)
})

test('畑も植林場もなければ、先に facility(name) で設計するように言う。空きより多くは頼めない', () => {
  const k = new Knowledge(md)
  const s = state({ memory: {} })
  const bot = world({}, [{ name: 'oak_sapling', count: 4 }, { name: 'wheat_seeds', count: 4 }, { name: 'wooden_hoe', count: 1 }])
  s.goal = makeGoal({ predicate: 'planted', item: 'sapling', count: 2 }, bot, s, k)
  assert.match(evaluate(bot, s, k, {}).impossible.join('\n'), /no tree farm for sapling to plant in: add facility\(<name>\)/)
  s.goal = makeGoal({ predicate: 'farmed', item: 'wheat', count: 2 }, bot, s, k)
  assert.match(evaluate(bot, s, k, {}).impossible.join('\n'), /no farm for wheat: add facility\(<name>\)/)

  registerFacility(world(), s, 'grove', { kind: 'tree_farm', boxes: ZONE, design: { sapling: 'oak_sapling', count: 2, spacing: 4 } })
  s.goal = makeGoal({ predicate: 'planted', item: 'sapling', count: 2 }, bot, s, k)
  const r = evaluate(bot, s, k, {})
  assert.deepEqual(r.leaves.filter((l) => l.kind === 'plant').map((l) => [l.item, l.facility]), [['oak_sapling', 'grove']])
  assert.equal(workCell(bot, s, 'plant', { sapling: 'birch_sapling' }), null) // オーク用の植林場

  registerFacility(world(), s, 'small', { kind: 'farm', boxes: [{ min_x: 20, min_z: 0, max_x: 27, max_z: 7 }], design: { width: 2, depth: 1 } })
  s.goal = makeGoal({ predicate: 'farmed', item: 'wheat', count: 5 }, bot, s, k)
  assert.match(evaluate(bot, s, k, {}).blocked.join('\n'), /the wheat farms have room for 1 \(asked 5\): for more, add another farm with a new name/)
})

// 2026-09-29: 種のないまま耕し続けた。水が近くにない畑は、耕しただけで何も植わっていない地面が土に戻る
test('耕すのは、まく種が耕し済みのマスより多いときだけ（耕す → まく を順に）。種がなければ集める', async () => {
  const k = new Knowledge(md)
  const s = state()
  const fac = registerFacility(world(), s, 'wheat_farm', { kind: 'farm', boxes: ZONE, design: { width: 3, depth: 1 } })
  const [c1] = fac.layout.cells
  const tilled = { [`${c1.x},${c1.y},${c1.z}`]: 'farmland' } // 1 マス耕し済み、1 マスはまだ（あと水のマス 1）
  const hoe = { name: 'wooden_hoe', count: 1 }
  const bucket = { name: 'water_bucket', count: 1 } // 水のバケツがあれば水のマスの手が加わるだけ（ソルバーは呼ばれない）
  const fences = [{ name: 'oak_fence', count: 30 }, { name: 'oak_fence_gate', count: 1 }] // 柵があれば、柵の手が加わるだけ
  const leaves = (items) => {
    const bot = world(tilled, [...items, bucket, ...fences])
    s.goal = makeGoal({ predicate: 'facility', name: 'wheat_farm' }, bot, s, k)
    return evaluate(bot, s, k, {}).leaves.filter((l) => !['water', 'fence', 'fence_gate'].includes(l.kind))
  }
  // 種 1 つで耕し済みが 1 マス: まくだけ。耕さない
  assert.deepEqual(leaves([hoe, { name: 'wheat_seeds', count: 1 }]).map((l) => l.kind), ['sow'])
  // 種 2 つで耕し済みが 1 マス: まくのと、次を耕すの
  assert.deepEqual(leaves([hoe, { name: 'wheat_seeds', count: 2 }]).map((l) => l.kind), ['sow', 'till'])
  // 種がなければ耕さない（種を集める）
  assert.ok(!leaves([hoe]).some((l) => l.kind === 'till'))
  // 何も耕していなくて種もなければ、耕さずに種を集める
  const bot = world({}, [hoe, bucket, ...fences])
  s.goal = makeGoal({ predicate: 'facility', name: 'wheat_farm' }, bot, s, k)
  assert.ok(!evaluate(bot, s, k, {}).leaves.some((l) => l.kind === 'till'))

  // 耕し済みの数は畑の集計にも出る（solver が見る）
  const withSeeds = world(tilled, [hoe, { name: 'wheat_seeds', count: 1 }])
  assert.equal(cropFarms(withSeeds, s, 'wheat').sow, 1)
})

test('till の道具: まく種が耕し済みのマスに足りなければ断り、理由を言う', async () => {
  const k = new Knowledge(md)
  const s = state()
  const fac = registerFacility(world(), s, 'wheat_farm', { kind: 'farm', boxes: ZONE, design: { width: 3, depth: 1 } })
  const [c1] = fac.layout.cells
  const tilled = { [`${c1.x},${c1.y},${c1.z}`]: 'farmland' }
  const hoe = { name: 'wooden_hoe', count: 1 }
  const call = (items) => {
    const bot = world(tilled, items)
    return createTools({ bot, state: s, knowledge: k, run: async () => ({ ok: true, result: 'done', seconds: 0 }), candidates: () => [], check: () => [] })('till', {})
  }
  const none = await call([hoe])
  assert.equal(none.refused, true)
  assert.match(none.result, /0 wheat_seeds for 1 tilled cells.*turns back to dirt without water.*Get wheat_seeds first/)
  const one = await call([hoe, { name: 'wheat_seeds', count: 1 }])
  assert.equal(one.refused, true)
  assert.match(one.result, /Sow the tilled cells first/)
  assert.equal((await call([hoe, { name: 'wheat_seeds', count: 2 }])).ok, true)
})

// 設計書 47 §17: 水のマスは四角の中に 9 x 9 ごとに 1 つ（水から縦横 4 マス以内の耕地が湿る）
const BIG = [{ min_x: 8, min_z: 0, max_x: 30, max_z: 20 }]

test('畑の水のマス: 9 x 9 ごとに 1 つ、作物は残り。水から縦横 4 マス以内にだけ作物を置く', () => {
  const farm = (width, depth) => registerFacility(world(), state(), 'ff', { kind: 'farm', boxes: BIG, design: { width, depth } }).layout
  const l9 = farm(9, 9)
  assert.deepEqual([l9.cells.length, l9.water.length], [80, 1])
  // 真ん中（9 x 9 の 5 行 5 列め）にあり、どの作物のマスも縦横 4 以内
  const [w] = l9.water
  assert.ok(l9.cells.every((c) => Math.max(Math.abs(c.x - w.x), Math.abs(c.z - w.z)) <= 4))
  assert.deepEqual([w.x - Math.min(...l9.cells.map((c) => c.x)), w.z - Math.min(...l9.cells.map((c) => c.z))], [4, 4])
  const l169 = farm(16, 9)
  assert.deepEqual([l169.cells.length, l169.water.length], [16 * 9 - 2, 2])
  const l16 = farm(16, 16)
  assert.deepEqual([l16.cells.length, l16.water.length], [16 * 16 - 4, 4])
  // 1 x 1 は水だけになるので断る（作物のマスが要る）
  assert.throws(() => farm(1, 1), /too small.*at least 2 cells/)
})

test('畑の水のマス: 横の地面が水より低いマスは選ばない（水が畑に流れ出す）。どこも低ければ断る', () => {
  const zone = [{ min_x: 10, min_z: 2, max_x: 12, max_z: 4 }]
  // 3 x 3 で、まん中の北の地面がない: まん中には置かない
  const notch = registerFacility(world({ '11,63,2': 'air' }), state(), 'gg', { kind: 'farm', boxes: zone, design: { width: 3, depth: 3 } }).layout
  assert.equal(notch.water.length, 1)
  assert.notDeepEqual([notch.water[0].x, notch.water[0].z], [11, 3])
  // 2 x 1 の 2 マスとも、横に地面のない所がある
  assert.throws(() => registerFacility(world({ '10,63,2': 'air', '11,63,4': 'air' }), state(), 'hh', { kind: 'farm', boxes: [{ min_x: 10, min_z: 3, max_x: 11, max_z: 3 }], design: { width: 2, depth: 1 } }),
    (e) => e instanceof FacilityError && /no safe place for water/.test(e.message))
})

test('水のバケツを持っていなければ、水のバケツを手に入れる（水を置く手は出ない）。あれば水の手が先', () => {
  const k = new Knowledge(md)
  const s = state()
  registerFacility(world(), s, 'wheat_farm', { kind: 'farm', boxes: ZONE, design: { width: 3, depth: 1 } })
  const w = { unlocked: () => true, blocks: {}, mobs: {}, remembered: new Set(), water: () => null }
  const bot = world({}, [{ name: 'wooden_hoe', count: 1 }, { name: 'wheat_seeds', count: 2 }])
  s.goal = makeGoal({ predicate: 'facility', name: 'wheat_farm' }, bot, s, k)
  const r = evaluate(bot, s, k, w)
  assert.ok(!r.leaves.some((l) => l.kind === 'water'))
  assert.match(r.lines.join('\n'), /water_bucket/)
  assert.match(r.lines.join('\n'), /water to place 1/)
  assert.ok(r.leaves.some((l) => l.kind === 'till'), '水のバケツを待たずに、耕す・まくは進められる')
})

test('water の道具: バケツがなければ断り、畑の水のマスにだけ置く。省けば次の水のマス', async () => {
  const k = new Knowledge(md)
  const s = state()
  const fac = registerFacility(world(), s, 'wheat_farm', { kind: 'farm', boxes: ZONE, design: { width: 3, depth: 1 } })
  const [w1] = fac.layout.water
  const [c1] = fac.layout.cells
  const call = (items, args) => createTools({ bot: world({}, items), state: s, knowledge: k, run: async () => ({ ok: true, result: 'done', seconds: 0 }), candidates: () => [], check: () => [] })('water', args)
  const none = await call([], {})
  assert.equal(none.refused, true)
  assert.match(none.result, /no water_bucket.*bucket.*iron/)
  const bucket = [{ name: 'water_bucket', count: 1 }]
  const elsewhere = await call(bucket, { x: c1.x, y: c1.y, z: c1.z })
  assert.equal(elsewhere.refused, true)
  assert.match(elsewhere.result, /not a water cell of a designed farm/)
  // 畑のそばに、水のバケツを手で注がない（水が畑の表面に流れる）
  const pour = createTools({ bot: world({}, bucket), state: s, knowledge: k, run: async () => ({ ok: true, result: 'done', seconds: 0 }), candidates: () => [], check: () => [] })
  const poured = await pour('use_item', { item: 'water_bucket', x: c1.x, y: c1.y, z: c1.z })
  assert.equal(poured.refused, true)
  assert.match(poured.result, /by hand.*use the water tool/)
  assert.equal((await call(bucket, {})).ok, true)
  assert.equal((await call(bucket, { x: w1.x, y: w1.y, z: w1.z })).ok, true)
  assert.equal(waterAt(s, new Vec3(w1.x, w1.y, w1.z)).name, 'wheat_farm')
})

const FULL = (items) => ({ inventory: {}, blocks: {}, mobs: {}, table: null, unlocked: () => true, dig: () => [], hunt: () => [], ...items })

// 2026-10-07: 種・苗木は最初に全マスの 1/4 だけ集めて植え、残りは育ったものから回収して増やす（設計書 74）
test('畑: 全マスの 1/4 が植わっていれば started が成り立つ（全マスの facility は成り立たない）', () => {
  const k = new Knowledge(md)
  const s = state()
  const fac = registerFacility(world(), s, 'wheat_farm', { kind: 'farm', boxes: ZONE, design: { width: 3, depth: 2 } })
  assert.equal(fac.layout.cells.length, 5)
  const planted = (n) => Object.fromEntries(fac.layout.cells.slice(0, n).flatMap((c) => [[`${c.x},${c.y},${c.z}`, 'farmland'], [`${c.x},${c.y + 1},${c.z}`, 'wheat']]))
  const check = (n, started) => {
    const bot = world(planted(n))
    s.goal = makeGoal({ predicate: 'facility', name: 'wheat_farm', ...(started ? { started: true } : {}) }, bot, s, k)
    return evaluate(bot, s, k, FULL())
  }
  assert.equal(layoutStatus(world(), fac).quota, 2)
  assert.equal(check(1, true).met, false)
  assert.equal(check(2, true).met, true)
  assert.equal(check(2, true).leaves.length, 0)
  assert.equal(check(2, false).met, false)
  assert.match(check(1, true).lines.join('\n'), /1\/2 cells planted to start/)
})

test('畑: 種は最初に全マスの 1/4 だけ集める。そのあとは種を持ったぶんだけまく', () => {
  const k = new Knowledge(md)
  const s = state()
  registerFacility(world(), s, 'wheat_farm', { kind: 'farm', boxes: ZONE, design: { width: 3, depth: 2 } })
  const hoe = { name: 'wooden_hoe', count: 1 }
  const seeds = (blocks, items) => {
    const bot = world(blocks, [hoe, { name: 'water_bucket', count: 1 }, ...items])
    s.goal = makeGoal({ predicate: 'facility', name: 'wheat_farm' }, bot, s, k)
    return evaluate(bot, s, k, FULL())
  }
  // 何も植わっていなくて種もなければ、集めるのは 2 つ（全マス 5 の 1/4）
  const first = seeds({}, [])
  assert.ok(JSON.stringify(first.leaves).includes('wheat_seeds'), JSON.stringify(first.leaves))
  assert.match(first.lines.join('\n'), /have 2 wheat_seeds \(0\/2\)/)
  // 種を 4 つ持っていれば、まく手（5 マスのうち持った分だけ。集めに出ない）
  const held = seeds({}, [{ name: 'wheat_seeds', count: 4 }])
  assert.ok(held.leaves.some((l) => l.kind === 'till' || l.kind === 'sow'))
  assert.doesNotMatch(held.lines.join('\n'), /wheat_seeds/)
})

test('植林場: 全マスの 1/4 の苗木を植えたら、育った木がなければ探しに出ず待つ。育った木があれば葉から集める', () => {
  const k = new Knowledge(md)
  const s = state({ plantings: [], memory: {} })
  const fac = registerFacility(world(), s, 'grove', { kind: 'tree_farm', boxes: ZONE, design: { sapling: 'oak_sapling', count: 4, spacing: 4 } })
  const [c1] = fac.layout.cells
  const at = (c, name) => ({ [`${c.x},${c.y},${c.z}`]: name })
  const run = (blocks, items = []) => {
    const bot = world(blocks, items)
    s.goal = makeGoal({ predicate: 'facility', name: 'grove' }, bot, s, k)
    return evaluate(bot, s, k, FULL())
  }
  // 何も植わっていなくて苗木もなければ、集めるのは 1 本（全マス 4 の 1/4）
  assert.match(JSON.stringify(run({}).leaves), /oak_sapling/)
  // 1 本植わった（苗木）: もう探さない
  s.plantings = [{ x: c1.x, y: c1.y, z: c1.z, item: 'oak_sapling' }]
  const waiting = run(at(c1, 'oak_sapling'))
  assert.equal(waiting.leaves.length, 0)
  assert.match(waiting.lines.join('\n'), /wait for the trees to grow/)
  // 苗木を拾えば植える（増やす）
  assert.deepEqual(run(at(c1, 'oak_sapling'), [{ name: 'oak_sapling', count: 1 }]).leaves.map((l) => l.kind), ['plant'])
  // 育った木があれば、その葉から苗木を集める手を出す
  assert.match(JSON.stringify(run(at(c1, 'oak_log')).leaves), /oak_sapling/)
})
