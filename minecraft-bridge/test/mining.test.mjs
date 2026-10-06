import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { designMine, mineStatus, updateStatus, minesAt, FacilityError } from '../src/facilities.mjs'
import { oreY, bottomFor, minePlan, mineSites, shallowStone, mineFromHome, SITE_NEAR, SITE_FAR, SURFACE_DEPTH } from '../src/mining.mjs'
import { solve } from '../src/solver.mjs'
import { Knowledge } from '../src/knowledge.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')
const knowledge = new Knowledge(md)

// 地面は y 63 の草、その下は石（上は空気）。家は (0,64,0) のまわり 3x3
function world (blocks = {}, at = new Vec3(10, 64, 0)) {
  return {
    registry: md,
    entity: { position: at, onGround: true },
    inventory: { items: () => [], emptySlotCount: () => 30 },
    entities: {},
    findBlock: () => null,
    time: { timeOfDay: 1000, age: 0 },
    blockAt: (p) => {
      const b = blocks[`${p.x},${p.y},${p.z}`]
      const name = b?.name ?? b ?? (p.y > 63 ? 'air' : p.y === 63 ? 'grass_block' : 'stone')
      const empty = /air|water|lava/.test(name)
      return { name, boundingBox: empty ? 'empty' : 'block', position: p }
    },
    findBlocks: () => []
  }
}
const home = { inside: new Vec3(0, 64, 0), min: new Vec3(-1, 64, -1), max: new Vec3(1, 64, 1), door: new Vec3(0, 64, 2), outside: new Vec3(0, 64, 3) }
const state = (extra = {}) => ({ home, plan: null, builds: {}, formerHomes: [], ...extra })
const dug = (fac, blocks = {}) => {
  for (const c of fac.layout.cells) c.sealed = true // 枝のある採掘場は、囲まれて（sealed）から済み
  for (const c of fac.layout.cells) for (let i = 0; i < c.h; i++) blocks[`${c.x},${c.y + i},${c.z}`] = 'air'
  return blocks
}
const homeDistance = (e) => Math.hypot(e.x + 1.5 - 0.5, e.z + 1.5 - 0.5) - 1.5

test('鉱石の高さ: 鉄 16、深層岩の鉱石も同じ、石だけなら決まらない', () => {
  assert.equal(oreY(['iron_ore', 'deepslate_iron_ore']), 16)
  assert.equal(oreY(['deepslate_diamond_ore']), -58)
  assert.equal(oreY(['stone']), null)
  assert.equal(oreY(['deepslate']), -20)
})

test('新しい採掘場の底: 鉱石の高さ、石だけなら家の床から 14 下（家の床より 4 以上下、岩盤より上）', () => {
  assert.equal(bottomFor(state(), ['iron_ore']), 16)
  assert.equal(bottomFor(state(), ['stone']), 50)
  assert.equal(bottomFor(state(), ['redstone_ore']), -58)
})

test('家があるときだけ採掘場を通す: 見つけた拠点（仮の宿）は家に数えない', () => {
  assert.equal(mineFromHome(state()), true)
  assert.equal(mineFromHome({ home: { ...home, found: true } }), false)
  assert.equal(mineFromHome({ home: null }), false)
})

test('設計: 入口は家の壁から 10〜30 m、階段は鉱石の高さまで、東西の坑道 32 マスに枝（長さ 10）', () => {
  const s = state()
  const fac = designMine(world(), s, 16, mineSites(s))
  assert.equal(fac.name, 'mine_1')
  const e = fac.layout.cells[0]
  const d = homeDistance(e)
  assert.ok(d >= SITE_NEAR - 3 && d <= SITE_FAR + 3, `distance ${d}`)
  assert.equal(fac.layout.cells.filter((c) => c.part === 'stairs').at(-1).y, 16)
  assert.deepEqual(fac.layout.tunnels, ['east', 'west'])
  assert.equal(fac.layout.tunnel_length, 32)
  assert.equal(fac.layout.branch_length, 10)
  assert.equal(s.facilities.mine_1, fac)
})

test('2 つ目は前の入口と同じ行に並べ、坑道はほかの採掘場に突き当たらない', () => {
  const s = state()
  const bot = world()
  const first = designMine(bot, s, 16, mineSites(s))
  const second = designMine(bot, s, 16, mineSites(s))
  assert.equal(second.name, 'mine_2')
  const [a, b] = [first.layout.cells[0], second.layout.cells[0]]
  assert.ok(a.x === b.x || a.z === b.z, `${a.x},${a.z} と ${b.x},${b.z} が同じ行にない`)
  assert.ok(Math.abs(a.x - b.x) + Math.abs(a.z - b.z) >= 6)
  const open = new Set(first.layout.cells.flatMap((c) => Array.from({ length: c.h }, (_, i) => `${c.x},${c.y + i},${c.z}`)))
  for (const c of second.layout.cells) for (let i = 0; i < c.h; i++) assert.ok(!open.has(`${c.x},${c.y + i},${c.z}`))
})

test('坑道は危なければ短くする（溶岩のある向きの先は使わない）。東西の 2 本は保つ', () => {
  const s = state()
  const probe = designMine(world(), state(), 16, mineSites(state()))
  // 最初に選ばれる入口の東の坑道の先に溶岩を置く
  const e = probe.layout.cells[0]
  const lava = {}
  const tunnel = probe.layout.cells.filter((c) => c.part === 'tunnel_east')
  const far = tunnel.at(-1)
  lava[`${far.x},${far.y},${far.z}`] = 'lava'
  const fac = designMine(world(lava), s, 16, mineSites(s))
  assert.equal(fac.layout.cells[0].x, e.x)
  assert.equal(fac.layout.tunnel_length, 24)
  assert.deepEqual(fac.layout.tunnels, ['east', 'west'])
})

test('岩盤より下へは設計できない', () => {
  const s = state()
  assert.throws(() => designMine(world({}), s, -100, mineSites(s)), FacilityError)
})

test('採掘の次の一手: なければ設計、途中なら続き、下りきったら下りる、底まで掘り尽くしたら新しい採掘場', () => {
  const s = state()
  const bot = world()
  assert.deepEqual(minePlan(bot, s, ['iron_ore']), { make: 16 })
  const fac = designMine(bot, s, 16, mineSites(s))
  assert.deepEqual(minePlan(bot, s, ['iron_ore']), { dig: fac.name })
  // 石だけなら、どの高さの採掘場でもよい
  assert.deepEqual(minePlan(bot, s, ['stone']), { dig: fac.name })
  // 遠い高さの鉱石には別の採掘場（ダイヤは -58）
  assert.deepEqual(minePlan(bot, s, ['diamond_ore']), { make: -58 })
  const done = world(dug(fac))
  updateStatus(done, fac)
  assert.equal(fac.status, 'done')
  assert.equal(minePlan(done, s, ['iron_ore']).go.name, fac.name)
  const bottom = fac.layout.cells.filter((c) => c.part === 'stairs').at(-1)
  const below = world(dug(fac), new Vec3(bottom.x, bottom.y, bottom.z))
  // 底にいて、見える所に鉱石がない（solver はそのときだけ計画を聞く）: 掘り尽くした。新しい採掘場を、印の名前つきで
  assert.deepEqual(minePlan(below, s, ['iron_ore']), { make: 16, exhausted: fac.name })
  assert.equal(minesAt(done, s, 16).length, 1)
  // 印を付けた採掘場は、もう当てにしない（下りない・続きとも言わない）
  fac.layout.exhausted = true
  assert.equal(minesAt(done, s, 16).length, 0)
  assert.deepEqual(minePlan(done, s, ['iron_ore']), { make: 16 })
  // 底の高さが 8 より離れた採掘場は、鉱石の高さに合わない（坑道と枝は底の 2 マスしか見えない）
  fac.layout.exhausted = false
  assert.equal(minesAt(done, s, 16 + 9, 8).length, 0)
})

test('地表に近い石は掘らず、10 マス下からは掘る。鉱石・土・家がないときは対象外', () => {
  const s = state()
  const bot = world()
  assert.equal(shallowStone(bot, s, new Vec3(5, 62, 5), 'stone'), true)
  assert.equal(shallowStone(bot, s, new Vec3(5, 63 - SURFACE_DEPTH, 5), 'stone'), false)
  assert.equal(shallowStone(bot, s, new Vec3(5, 62, 5), 'iron_ore'), false)
  assert.equal(shallowStone(bot, s, new Vec3(5, 62, 5), 'dirt'), false)
  assert.equal(shallowStone(bot, { home: null }, new Vec3(5, 62, 5), 'stone'), false)
})

test('solver: 家があれば、地下の鉄は採掘場を作ってから。なければ探す。家がなければ今までどおり', () => {
  const s = state()
  const bot = world()
  const base = {
    inventory: { stone_pickaxe: 1, torch: 8, wooden_sword: 1 }, stored: {}, remembered: new Set(), blocks: {}, mobs: {}, table: 'near', furnace: 'near',
    unlocked: () => true, dig: () => [], hunt: () => [], buried: () => [], digDepth: Infinity, mines: []
  }
  const withHome = { ...base, home: true, minePlan: (sources) => minePlan(bot, s, sources) }
  const leafOf = (w) => solve(knowledge, w, [{ spec: 'raw_iron', count: 1 }]).leaves[0]
  assert.equal(leafOf(withHome).kind, 'make_mine')
  assert.equal(leafOf(withHome).bottom_y, 16)
  const noHome = { ...base, home: false, buried: () => [{ pos: new Vec3(0, 40, 0), depth: 20 }] }
  assert.equal(leafOf(noHome).kind, 'dig_down')
})

test('solver: 地下へ下りる手は、松明を持ってから（松明は石炭か木炭と棒から作る手が先に出る）', () => {
  const s = state()
  const bot = world()
  const base = {
    inventory: { stone_pickaxe: 1, stick: 4, coal: 2, wooden_sword: 1 }, stored: {}, remembered: new Set(), blocks: {}, mobs: {}, table: 'near', furnace: 'near',
    unlocked: () => true, dig: () => [], hunt: () => [], buried: () => [], digDepth: Infinity, mines: [], home: true,
    minePlan: (sources) => minePlan(bot, s, sources)
  }
  const none = solve(knowledge, base, [{ spec: 'raw_iron', count: 1 }])
  assert.deepEqual(none.leaves.map((l) => l.kind), ['craft']) // 松明を作る。採掘場はまだ
  assert.equal(none.leaves[0].item, 'torch')
  assert.ok(none.lines.some((l) => l.includes('torch')), none.lines.join('\n'))
  const held = solve(knowledge, { ...base, inventory: { ...base.inventory, torch: 8 } }, [{ spec: 'raw_iron', count: 1 }])
  assert.equal(held.leaves[0].kind, 'make_mine')
  const few = solve(knowledge, { ...base, inventory: { ...base.inventory, torch: 3 } }, [{ spec: 'raw_iron', count: 1 }])
  assert.equal(few.leaves[0].item, 'torch')
})
