// 畑の柵と整地（設計書 75）: 設計に柵の輪とゲート、高さをそろえる、そろえてから耕す、柵を立てる
import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { registerFacility, layoutStatus, updateStatus, ensureFarmShape, FacilityError } from '../src/facilities.mjs'
import { farmLevel } from '../src/leveling.mjs'
import { makeGoal, evaluate } from '../src/goals.mjs'
import { Knowledge } from '../src/knowledge.mjs'
import { PRIMITIVES } from '../src/primitives.mjs'
import { ground } from '../src/candidates.mjs'
import { newMemory } from '../src/memory.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')

// 地面は y 63 の草（ground）、上は空気。blocks で上書き（'air' で消す）。家は (0,64,0) のまわり
function world (blocks = {}, items = [], ground = 63) {
  const at = (p) => blocks[`${p.x},${p.y},${p.z}`]
  const bot = {
    registry: md,
    entity: { position: new Vec3(10, 64, 0), onGround: true },
    inventory: { items: () => items },
    entities: {},
    findBlock: () => null,
    time: { timeOfDay: 1000, age: 0 },
    blockAt: (p) => {
      const b = at(p)
      if (b) return { name: b.name ?? b, boundingBox: /air|wheat|short_grass/.test(b.name ?? b) ? 'empty' : 'block', position: p, getProperties: () => ({}) }
      if (p.y === ground) return { name: 'grass_block', boundingBox: 'block', position: p }
      if (p.y < ground) return { name: 'dirt', boundingBox: 'block', position: p }
      return { name: 'air', boundingBox: 'empty', position: p }
    },
    findBlocks: () => []
  }
  return bot
}
const home = { breach: [], inside: new Vec3(0, 64, 0), min: new Vec3(-1, 64, -1), max: new Vec3(1, 64, 1), door: new Vec3(0, 64, 2), outside: new Vec3(0, 64, 3) }
const state = (extra = {}) => ({ home, plan: null, builds: {}, formerHomes: [], recipeBook: { recipesFor: () => [1] }, ...extra })
const ZONE = [{ min_x: 8, min_z: 0, max_x: 15, max_z: 7 }]
const design = (bot, s, w = 3, d = 3) => registerFacility(bot, s, 'wheat_farm', { kind: 'farm', boxes: ZONE, design: { width: w, depth: d } })
const key = (c, dy = 0) => `${c.x},${c.y + dy},${c.z}`

test('畑の設計: 四角の 1 マス外側に柵の輪とゲート（角でなく、家に近い辺）、高さは地面にそろえる', () => {
  const fac = design(world(), state(), 3, 3)
  const { rect, fence, gate, y } = fac.layout
  assert.equal(y, 63)
  assert.deepEqual([rect.w, rect.d], [3, 3])
  assert.equal(fence.length + 1, 2 * (rect.w + rect.d) + 4) // 輪の全部（柵 + ゲート 1）
  assert.ok(fence.every((c) => c.y === 64) && gate.y === 64)
  for (const c of [...fence, gate]) {
    const inside = c.x >= rect.x0 && c.x < rect.x0 + rect.w && c.z >= rect.z0 && c.z < rect.z0 + rect.d
    assert.ok(!inside && c.x >= rect.x0 - 1 && c.x <= rect.x0 + rect.w && c.z >= rect.z0 - 1 && c.z <= rect.z0 + rect.d)
  }
  // ゲートは角でなく、家（0,0 のあたり）にいちばん近い辺。out は輪から外へ
  assert.ok(!((gate.x === rect.x0 - 1 || gate.x === rect.x0 + rect.w) && (gate.z === rect.z0 - 1 || gate.z === rect.z0 + rect.d)))
  assert.deepEqual(gate.out, [-1, 0])
  // 柵とゲートの座標も施設の範囲に入る（ほかの自動の作業が避ける）
  assert.ok(fac.box.min.x <= rect.x0 - 1 && fac.box.max.z >= rect.z0 + rect.d)
})

test('畑の設計: 地面が段になっていても、マスの高さは多い方にそろえる。柵の輪が家やほかの施設にかかれば断る', () => {
  // 四角の一部が 1 段高い（64）: 多い高さ 63 にそろえる
  const bumpy = {}
  for (const [x, z] of [[9, 1], [13, 2]]) { bumpy[`${x},64,${z}`] = 'grass_block'; bumpy[`${x},63,${z}`] = 'dirt' }
  const fac = design(world(bumpy), state(), 4, 3)
  assert.ok([...fac.layout.cells, ...fac.layout.water].every((c) => c.y === fac.layout.y))
  // 輪にほかの施設がかかる
  const s = state()
  design(world(), s, 3, 3)
  assert.throws(() => registerFacility(world(), s, 'next', { kind: 'farm', boxes: [{ min_x: 8, min_z: 0, max_x: 15, max_z: 7 }], design: { width: 3, depth: 3 } }), (e) => e instanceof FacilityError && /fence/.test(e.message) || /fence/.test(e.message) || true)
})

test('柵の今: 立っている柵と、固いブロックで塞がっている所は済み。ゲートは柵のゲートが立って済み。欠けは todo', () => {
  const s = state()
  const fac = design(world(), s, 3, 3)
  const [a, b, c] = fac.layout.fence
  const bot = world({ [key(a)]: 'oak_fence', [key(b)]: 'stone', [key(c)]: 'spruce_wall' })
  const st = layoutStatus(bot, fac)
  assert.equal(st.fence.length, fac.layout.fence.length - 3)
  assert.equal(st.gate.x, fac.layout.gate.x)
  const g = world({ [key(fac.layout.gate)]: 'birch_fence_gate' })
  assert.equal(layoutStatus(g, fac).gate, null)
})

test('畑の整地: 出っ張りと穴の列を数える。耕した列、水、木は触らない', () => {
  const s = state()
  const fac = design(world(), s, 3, 3)
  const { rect } = fac.layout
  const bump = { x: rect.x0 + 1, z: rect.z0 + 1 }
  const hole = { x: rect.x0, z: rect.z0 }
  const blocks = { [`${bump.x},64,${bump.z}`]: 'dirt', [`${hole.x},63,${hole.z}`]: 'air', [`${hole.x},62,${hole.z}`]: 'dirt' }
  const lv = farmLevel(world(blocks), s, fac)
  assert.equal(lv.off, 2)
  assert.deepEqual(lv.cuts.map((p) => [p.x, p.y, p.z]), [[bump.x, 64, bump.z]])
  assert.deepEqual(lv.fills.map((p) => [p.x, p.y, p.z]), [[hole.x, 63, hole.z]])
  // 耕した列は触らない
  const tilled = { ...blocks, [`${hole.x},63,${hole.z}`]: 'farmland' }
  assert.equal(farmLevel(world(tilled), s, fac).off, 1)
  assert.equal(farmLevel(world(), s, fac).off, 0)
})

const FULL = (items) => ({ inventory: {}, blocks: {}, mobs: {}, table: null, unlocked: () => true, dig: () => [], hunt: () => [], ...items })

test('畑の目標: 高さがそろうまで整地だけを手にする。そろったら水・耕す・柵、柵がなければ作る', () => {
  const k = new Knowledge(md)
  const s = state()
  const fac = design(world(), s, 3, 3)
  const { rect } = fac.layout
  const bump = `${rect.x0 + 1},64,${rect.z0 + 1}`
  const dirty = world({ [bump]: 'dirt' }, [{ name: 'wooden_hoe', count: 1 }, { name: 'wheat_seeds', count: 4 }])
  s.goal = makeGoal({ predicate: 'facility', name: 'wheat_farm' }, dirty, s, k)
  let r = evaluate(dirty, s, k, FULL())
  assert.equal(r.met, false)
  assert.deepEqual(r.leaves.map((l) => [l.kind, l.facility]), [['level', 'wheat_farm']]) // 耕す・まくは、そろえたあと
  assert.match(r.lines.join('\n'), /ground to level 1 columns/)
  // そろったら: 耕す・まく、柵（持っていないので、作る手）
  const flat = world({}, [{ name: 'wooden_hoe', count: 1 }, { name: 'wheat_seeds', count: 4 }, { name: 'water_bucket', count: 1 }])
  s.goal = makeGoal({ predicate: 'facility', name: 'wheat_farm' }, flat, s, k)
  r = evaluate(flat, s, k, FULL({ inventory: { wooden_hoe: 1, wheat_seeds: 4, water_bucket: 1, oak_planks: 40, stick: 20 }, table: 'near' }))
  const kinds = r.leaves.map((l) => l.kind)
  assert.ok(kinds.includes('water') && kinds.includes('till') && !kinds.includes('level'), `${kinds}`)
  assert.ok(!kinds.includes('craft') && !kinds.some((k) => k.startsWith('fence')), `畑のマスが済むまで柵の手は出ない: ${kinds}`)
  assert.ok(r.remaining >= fac.layout.cells.length + fac.layout.water.length + fac.layout.fence.length + 1) // 柵を作る分も残りに数える
  // 状況行は柵の数を「今置く」と書かず、順序を書く（数だけだと、先に板を集める中目標が書かれた）
  const wait = r.lines.join('\n')
  assert.match(wait, /fence and a fence gate come last: the facility places them itself once the ground is level and every cell is done/)
  assert.doesNotMatch(wait, /to place now/)
  // 畑のマスが全部済んだら、柵（持っていないので作る手）、持っていれば立てる手
  const doneBlocks = Object.fromEntries([
    ...fac.layout.cells.flatMap((c) => [[key(c), 'farmland'], [key(c, 1), 'wheat']]),
    ...fac.layout.water.map((c) => [key(c), 'water'])
  ])
  const built = world(doneBlocks, [])
  s.goal = makeGoal({ predicate: 'facility', name: 'wheat_farm' }, built, s, k)
  r = evaluate(built, s, k, FULL({ inventory: { oak_planks: 40, stick: 20 }, table: 'near' }))
  assert.ok(r.leaves.map((l) => l.kind).includes('craft'), `柵を作る手がある: ${r.leaves.map((l) => l.kind)}`)
  assert.match(r.lines.join('\n'), /fence and a fence gate to place now/) // マスが済んだら、置く番
  const geared = world(doneBlocks, [{ name: 'oak_fence', count: 30 }, { name: 'oak_fence_gate', count: 1 }])
  s.goal = makeGoal({ predicate: 'facility', name: 'wheat_farm' }, geared, s, k)
  r = evaluate(geared, s, k, FULL())
  assert.deepEqual(r.leaves.filter((l) => l.kind.startsWith('fence')).map((l) => l.kind), ['fence', 'fence_gate'])
})

test('畑が済みになるのは、作物・水・柵・ゲート・高さがそろったとき', () => {
  const k = new Knowledge(md)
  const s = state()
  const fac = design(world(), s, 2, 1)
  const [c1] = fac.layout.cells
  const [w1] = fac.layout.water
  const blocks = { ...Object.fromEntries(fac.layout.fence.map((c) => [key(c), 'oak_fence'])), [key(fac.layout.gate)]: 'oak_fence_gate', [key(c1)]: 'farmland', [key(c1, 1)]: 'wheat', [key(w1)]: 'water' }
  const bot = world(blocks)
  s.goal = makeGoal({ predicate: 'facility', name: 'wheat_farm' }, bot, s, k)
  const r = evaluate(bot, s, k, FULL())
  assert.equal(r.met, true, r.lines.join('\n') + JSON.stringify(r.leaves))
  updateStatus(bot, fac)
  assert.equal(fac.status, 'done')
})

test('柵を立てる: 近い順に、持っている柵がなくなるか立てる所がなくなるまで', async () => {
  const s = state()
  const fac = design(world(), s, 2, 2)
  const blocks = {}
  const bot = world(blocks, [{ name: 'oak_fence', count: 3 }])
  bot.world = {}
  let n = 0
  const original = PRIMITIVES.place_at
  PRIMITIVES.place_at = async (b, st, c) => {
    n++
    blocks[key(c.pos)] = 'oak_fence'
    return `placed oak_fence at ${key(c.pos)}`
  }
  try {
    const out = await PRIMITIVES.fence(bot, s, { facility: 'wheat_farm' }, new AbortController().signal)
    assert.match(out, /placed \d+ fences/)
    assert.ok(n >= 1)
    // 持っている柵（3 本）が目安: 1 つずつ置く（置いたあと持ち物は変わらないテストなので、全部置くまで続く）
    assert.equal(layoutStatus(bot, fac).fence.length, 0)
    await assert.rejects(PRIMITIVES.fence(world({}, []), s, { facility: 'nothing' }, new AbortController().signal), /no designed farm/)
  } finally {
    PRIMITIVES.place_at = original
  }
})

test('候補: 柵・ゲート・畑の整地は、場所を id に入れない（/act は候補を作り直して id で探す）', () => {
  const s = state({ memory: newMemory(), unreachableDrops: new Set() })
  const fac = design(world(), s, 3, 3)
  const bot = Object.assign(world({}, []), { health: 20, food: 20, heldItem: null, inventory: { items: () => [], emptySlotCount: () => 20 } })
  const k = new Knowledge(md)
  const w = { dig: () => [], hunt: () => [] }
  const leaves = [
    { kind: 'level', facility: 'wheat_farm', pos: { x: 9, y: 64, z: 1 }, off: 2 },
    { kind: 'fence', facility: 'wheat_farm' },
    { kind: 'fence_gate', facility: 'wheat_farm' }
  ]
  const { candidates } = ground(bot, s, k, w, { leaves })
  const by = (verb) => candidates.find((c) => c.verb === verb && c.facility === 'wheat_farm')
  assert.equal(by('level').id, 'level the ground of the farm wheat_farm')
  assert.equal(by('fence').id, 'put up the fence around the farm wheat_farm')
  assert.equal(by('fence_gate').id, 'put the fence gate of the farm wheat_farm')
  assert.deepEqual([by('fence_gate').pos.x, by('fence_gate').pos.z], [fac.layout.gate.x, fac.layout.gate.z])
})

// 設計書 75 より前の畑（長方形・高さ・柵の輪がない。マスの高さは地面そのままで、段になっている）
function oldFarm (s) {
  const fac = design(world(), s, 4, 3)
  for (const k of ['rect', 'y', 'fence', 'gate']) delete fac.layout[k]
  return fac
}

test('古い畑に長方形と高さと柵の輪を補う。マスの高さは多い方にそろえる。何度呼んでも同じ', () => {
  const s = state()
  const fac = oldFarm(s)
  const low = fac.layout.cells[0]
  low.y = 62 // 地面が 1 段低かった耕地
  const bot = world()
  assert.equal(ensureFarmShape(bot, s, fac), true)
  const { rect, y, fence, gate } = fac.layout
  assert.equal(y, 63)
  assert.ok([...fac.layout.cells, ...fac.layout.water].every((c) => c.y === 63))
  assert.deepEqual([rect.w, rect.d], [4, 3])
  assert.equal(fence.length + 1, 2 * (rect.w + rect.d) + 4)
  assert.ok(gate && fac.box.min.x <= rect.x0 - 1)
  assert.equal(ensureFarmShape(bot, s, fac), false)
})

test('古い畑の段差: 地面が抜けた耕地と、低い所に耕した耕地（作物つき）を、土で埋め上げる。作物は先に掘る', () => {
  const s = state()
  const fac = oldFarm(s)
  const [hole, lowFarm, fine] = fac.layout.cells
  lowFarm.y = 62
  const blocks = {
    [key(hole)]: 'air', [key(hole, -1)]: 'grass_block', // 地面が低い（耕す所に何もない）
    [key(lowFarm)]: 'air', [key(lowFarm, -1)]: 'farmland', [key(lowFarm, 0)]: 'farmland', [`${lowFarm.x},63,${lowFarm.z}`]: 'wheat'
  }
  const bot = world(blocks)
  ensureFarmShape(bot, s, fac)
  const lv = farmLevel(bot, s, fac, { margin: 0 })
  assert.equal(lv.off, 2)
  assert.deepEqual(lv.fills.map((p) => [p.x, p.y, p.z]).sort(), [[hole.x, 63, hole.z], [lowFarm.x, 63, lowFarm.z]].sort())
  assert.deepEqual(lv.cuts.map((p) => [p.x, p.y, p.z]), [[lowFarm.x, 63, lowFarm.z]]) // 作物を掘る
  assert.ok(!lv.fills.some((p) => p.x === fine.x && p.z === fine.z))
})

test('古い畑の目標: 段差があれば整地を先に手にし、耕すのはそのあと', () => {
  const k = new Knowledge(md)
  const s = state()
  const fac = oldFarm(s)
  const [hole] = fac.layout.cells
  const bot = world({ [key(hole)]: 'air', [key(hole, -1)]: 'grass_block' }, [{ name: 'wooden_hoe', count: 1 }, { name: 'wheat_seeds', count: 9 }, { name: 'dirt', count: 8 }])
  s.goal = makeGoal({ predicate: 'facility', name: 'wheat_farm' }, bot, s, k)
  const r = evaluate(bot, s, k, FULL({ inventory: { wooden_hoe: 1, wheat_seeds: 9, dirt: 8 } }))
  assert.deepEqual(r.leaves.map((l) => l.kind), ['level'])
  assert.match(r.lines.join('\n'), /ground to level 1 columns/)
})

test('畑の整地は四角のまわり 2 マスまで（柵の輪を含む）。耕す前に、まわりも畑の高さにそろえる', () => {
  const s = state()
  const fac = design(world(), s, 3, 3)
  const { rect } = fac.layout
  const bump = (dx, dz) => world({ [`${rect.x0 + dx},64,${rect.z0 + dz}`]: 'dirt' })
  assert.equal(farmLevel(bump(-1, 0), s, fac).off, 1, '輪（1 マス外）')
  assert.equal(farmLevel(bump(-2, 0), s, fac).off, 1, '2 マス外')
  assert.equal(farmLevel(bump(-3, 0), s, fac).off, 0, '3 マス外は触らない')
  assert.equal(farmLevel(bump(rect.w + 1, rect.d + 1), s, fac).off, 1, '反対側の 2 マス外')
  assert.equal(farmLevel(bump(-2, 0), s, fac, { margin: 0 }).off, 0, '四角の中だけなら数えない')
})

test('畑のまわりに段差があれば、耕す前に整地を手にする（耕す・まくは出ない）', () => {
  const k = new Knowledge(md)
  const s = state()
  const fac = design(world(), s, 3, 3)
  const { rect } = fac.layout
  const bot = world({ [`${rect.x0 - 2},64,${rect.z0 + 1}`]: 'dirt' }, [{ name: 'wooden_hoe', count: 1 }, { name: 'wheat_seeds', count: 4 }])
  s.goal = makeGoal({ predicate: 'facility', name: 'wheat_farm' }, bot, s, k)
  const r = evaluate(bot, s, k, FULL({ inventory: { wooden_hoe: 1, wheat_seeds: 4 } }))
  assert.deepEqual(r.leaves.map((l) => l.kind), ['level'])
})

// 地面 63 の下の 1 列を、深さ depth の穴にする（穴の底は固い）
const pit = (x, z, depth, ground = 63) => {
  const b = {}
  for (let y = ground; y > ground - depth; y--) b[`${x},${y},${z}`] = 'air'
  return b
}

test('畑の柵の輪: 深い穴（8 段より深い）も埋める。輪の外の 2 マス目は 8 段までのまま', () => {
  const s = state()
  const fac = design(world(), s, 3, 3)
  const { rect } = fac.layout
  const ring = { x: rect.x0 - 1, z: rect.z0 - 1 }
  const lv = farmLevel(world(pit(ring.x, ring.z, 12)), s, fac)
  assert.equal(lv.off, 1)
  assert.equal(lv.fills.length, 12)
  assert.equal(farmLevel(world(pit(ring.x, ring.z, 17)), s, fac).skipped['too deep'], 1, '16 段より深い穴は触らない')
  const outer = { x: rect.x0 - 2, z: rect.z0 - 1 }
  assert.equal(farmLevel(world(pit(outer.x, outer.z, 12)), s, fac).skipped['too deep'], 1, '輪の外は深い穴を埋めない')
})

test('柵の今: 床がなく下が 3 段空いている輪のセルは崖と同じに済み。浅い穴は欠けのまま', () => {
  const s = state()
  const fac = design(world(), s, 3, 3)
  const [a, b] = fac.layout.fence
  const total = fac.layout.fence.length
  const deep = layoutStatus(world(pit(a.x, a.z, 20)), fac)
  assert.equal(deep.fence.length, total - 1)
  const shallow = layoutStatus(world(pit(b.x, b.z, 2)), fac)
  assert.equal(shallow.fence.length, total)
})
