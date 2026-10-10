// ブランチマイニング（設計書 68 §6 段階 2、86）: 東西の坑道（幹）に 3 マスおきの枝、岩は採掘場のセルのほかは
// 経路探索に掘らせない、掘り尽くしたら新しい採掘場
import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { designMine, mineStatus, stopMine, minesAt, plannedBlocks, BRANCH } from '../src/facilities.mjs'
import { mineSites, protectedRock, caveOre, minePlan } from '../src/mining.mjs'
import { configureMovements, PRIMITIVES } from '../src/primitives.mjs'
import { diggable } from '../src/world.mjs'
import { enclosure } from '../src/sealing.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')

// 地面は y 63 の草、その下は石（上は空気）。家は (0,64,0) のまわり 3x3。blocks は書き換えられる
function world (blocks = {}, items = []) {
  return {
    registry: md,
    entity: { position: new Vec3(10, 64, 0), onGround: true },
    inventory: { items: () => items, emptySlotCount: () => 30 },
    entities: {},
    findBlock: () => null,
    time: { timeOfDay: 1000, age: 0 },
    blockAt: (p) => {
      const b = blocks[`${p.x},${p.y},${p.z}`]
      const name = b?.name ?? b ?? (p.y > 63 ? 'air' : p.y === 63 ? 'grass_block' : 'stone')
      const empty = /air|water|lava/.test(name)
      return { name, boundingBox: empty ? 'empty' : 'block', position: p, harvestTools: undefined }
    },
    findBlocks: () => []
  }
}
const home = { inside: new Vec3(0, 64, 0), min: new Vec3(-1, 64, -1), max: new Vec3(1, 64, 1), door: new Vec3(0, 64, 2), outside: new Vec3(0, 64, 3) }
const state = (extra = {}) => ({ home, plan: null, builds: {}, formerHomes: [], ...extra })
const design = (blocks = {}, s = state()) => ({ s, fac: designMine(world(blocks), s, 16, mineSites(s)) })
const key = (c) => `${c.x},${c.y},${c.z}`

function digger (items, blocks = {}) {
  const bot = world(blocks, items)
  bot.dig = async (b) => { blocks[key(b.position)] = 'air' }
  bot.equip = async () => {}
  bot.heldItem = null
  bot.pathfinder = { goto: async () => {}, setGoal: () => {} }
  bot.waitForTicks = async () => {}
  bot.placeBlock = async () => {}
  return bot
}

test('枝: 坑道の 3 マスおきに左右へ。枝の間に岩が 2 マス残る。坑道は東西の一直線', () => {
  const { fac } = design()
  const cells = fac.layout.cells
  const tunnel = (dir) => cells.filter((c) => c.part === `tunnel_${dir}`)
  assert.equal(tunnel('east').length, 32)
  assert.equal(tunnel('west').length, 32)
  assert.ok(new Set(tunnel('east').map((c) => c.z)).size === 1, '東の坑道は一直線')
  const branches = new Map()
  for (const c of cells.filter((x) => x.part.startsWith('branch'))) branches.set(c.part, [...(branches.get(c.part) ?? []), c])
  // 最後の 1 マス手前まで、3 マスおき（i = 2, 5, 8 … 29）: 坑道 1 本に 10 か所 x 左右
  assert.equal(branches.size, 2 * 10 * 2)
  for (const list of branches.values()) assert.equal(list.length, BRANCH.length)
  const east = [...branches.entries()].filter(([part]) => part.startsWith('branch_east_'))
  const columns = [...new Set(east.map(([, list]) => list[0].x))].sort((a, b) => a - b)
  for (let i = 1; i < columns.length; i++) assert.equal(columns[i] - columns[i - 1], 3)
  // 枝は坑道に直角（東西の坑道なら南北）に伸びる
  for (const [, list] of east) assert.ok(list.every((c) => c.x === list[0].x))
  assert.deepEqual([...new Set(east.map(([part]) => part.at(-1)))].sort(), ['a', 'b'])
})

test('枝のセルも、先に空けた隣のセルに立って掘れる順に並ぶ。同じマスを 2 度数えない', () => {
  const { fac } = design()
  const cells = fac.layout.cells
  assert.equal(new Set(cells.map(key)).size, cells.length)
  const first = cells.findIndex((c) => c.part.startsWith('branch'))
  assert.ok(cells.slice(0, first).every((c) => !c.part.startsWith('branch')), '枝は坑道をすべて掘ってから')
  for (let i = cells.findIndex((c) => c.part.startsWith('tunnel')); i < cells.length; i++) {
    const c = cells[i]
    const stand = cells.slice(0, i).some((a) => Math.abs(a.x - c.x) + Math.abs(a.z - c.z) === 1 && a.y - c.y >= 0 && a.y - c.y <= 1)
    assert.ok(stand, `${c.part} ${key(c)} に隣り合う前のセルがない`)
  }
  // 入口に近い枝から、左右ひとつずつ
  const parts = cells.filter((c) => c.part.startsWith('branch')).map((c) => c.part).filter((p, i, a) => a.indexOf(p) === i)
  assert.deepEqual(parts.slice(0, 4), ['branch_east_2_a', 'branch_east_2_b', 'branch_west_2_a', 'branch_west_2_b'])
})

test('枝のない設計（LLM が facility(mine) で設計する採掘場）は、今までどおり坑道だけ', async () => {
  const { registerFacility } = await import('../src/facilities.mjs')
  const fac = registerFacility(world(), state(), 'mine_1', { kind: 'mine', boxes: [{ min_x: 8, min_z: 0, max_x: 15, max_z: 7 }], design: { bottom_y: 40, tunnels: 2, tunnel_length: 8 } })
  assert.ok(fac.layout.cells.every((c) => !c.part.startsWith('branch')))
  assert.equal(fac.layout.branch_length, 0)
})

test('stopMine: 枝のセルなら、その枝の先だけを外す。坑道なら、そこから先すべて', () => {
  const { fac } = design()
  const cells = fac.layout.cells
  const i = cells.findIndex((c) => c.part === 'branch_east_5_a')
  const before = cells.length
  stopMine(fac, i + 3, 'lava at 1,2,3') // 枝の 4 セル目で危ない
  const after = fac.layout.cells
  assert.equal(after.filter((c) => c.part === 'branch_east_5_a').length, 3)
  assert.equal(before - after.length, BRANCH.length - 3)
  assert.ok(after.some((c) => c.part === 'branch_west_5_b'), 'ほかの枝は残る')
  assert.equal(fac.layout.stopped.reason, 'lava at 1,2,3')
  const t = after.findIndex((c) => c.part === 'tunnel_east')
  stopMine(fac, t + 10, 'cave')
  assert.equal(fac.layout.cells.length, t + 10)
})

test('dig_mine: 枝が危なければその枝だけ打ち切って、ほかの枝を掘り続ける', async () => {
  const { fac } = design()
  const s = state({ facilities: { mine_1: fac } })
  const blocks = {}
  const bot = digger([{ name: 'stone_pickaxe', count: 1 }, { name: 'torch', count: 40 }], blocks)
  // 枝 branch_east_2_a の 2 セル目の隣に溶岩
  const bad = fac.layout.cells.filter((c) => c.part === 'branch_east_2_a')[1]
  blocks[`${bad.x},${bad.y},${bad.z + 1}`] = 'lava'
  bot.entity.position = new Vec3(fac.layout.cells[0].x, 64, fac.layout.cells[0].z)
  const r = await PRIMITIVES.dig_mine(bot, s, { facility: 'mine_1' }, new AbortController().signal)
  assert.match(r, /the mine is complete/)
  assert.match(r, /branches cut short for danger: branch_east_2_a at .*: lava at /)
  assert.equal(fac.layout.cells.filter((c) => c.part === 'branch_east_2_a').length, 1)
  assert.ok(fac.layout.cells.filter((c) => c.part === 'branch_west_29_b' || c.part === 'branch_west_26_b').length > 0)
  assert.equal(mineStatus(bot, fac).next, null)
})

test('dig_mine: 探している鉱石が壁に出たら、そこで止めて掘る手に任せる。1 回で少なくとも数セルは掘る', async () => {
  const { fac } = design()
  const s = state({ facilities: { mine_1: fac } })
  const blocks = {}
  const items = [{ name: 'stone_pickaxe', count: 1 }, { name: 'torch', count: 40 }]
  const bot = digger(items, blocks)
  // 10 セル掘ったあとから、採掘場の最初のセルに接する壁に、空気に面した鉄鉱石が届くことにする（digTargets: findBlocks、空気に面す、届く。
  // 採掘場のセルに接さない洞窟の鉱石は取らない: caveOre）
  const c0 = fac.layout.cells[0]
  const inCell = (p) => fac.layout.cells.some((c) => c.x === p.x && c.z === p.z && p.y >= c.y && p.y < c.y + c.h)
  const ore = [[1, 0, 0], [-1, 0, 0], [0, 0, 1], [0, 0, -1]].map(([x, y, z]) => new Vec3(c0.x + x, c0.y + y, c0.z + z)).find((p) => !inCell(p))
  bot.findBlocks = ({ matching }) => (matching === md.blocksByName.iron_ore.id && Object.keys(blocks).length > 10 ? [ore] : [])
  const base = bot.blockAt
  bot.blockAt = (p) => (p.equals(ore) ? { name: 'iron_ore', boundingBox: 'block', position: p } : base(p))
  bot.entity.position = new Vec3(c0.x + 0.5, c0.y, c0.z + 0.5)
  const r = await PRIMITIVES.dig_mine(bot, s, { facility: 'mine_1', sources: ['iron_ore'] }, new AbortController().signal)
  assert.match(r, new RegExp(`iron_ore shows in the wall at ${ore.x},${ore.y},${ore.z}: dig it`))
  assert.ok(Number(/^dug (\d+)/.exec(r)[1]) >= 3)
  assert.ok(mineStatus(bot, fac).next, '掘り終えていない: 続きは次の手')
})

test('make_mine: 掘り尽くした採掘場に印を付け、別の入口に新しい採掘場を作る（同じ所を二度掘らない）', async () => {
  const s = state()
  const bot = digger([{ name: 'stone_pickaxe', count: 1 }, { name: 'torch', count: 40 }], {})
  const first = designMine(bot, s, 16, mineSites(s))
  // make_mine は家の近くから始める（遠ければ家へ歩く）
  bot.entity.position = new Vec3(0, 64, 0)
  // 1 つ目を掘り尽くした状態にする
  for (const c of first.layout.cells) for (let i = 0; i < c.h; i++) bot.dig({ position: new Vec3(c.x, c.y + i, c.z) })
  await new Promise((resolve) => setImmediate(resolve))
  first.status = 'done'
  const r = await PRIMITIVES.make_mine(bot, s, { bottom_y: 16, exhausted: first.name, sources: ['iron_ore'] }, new AbortController().signal)
  assert.match(r, /designed the mine mine_2/)
  assert.equal(first.layout.exhausted, true)
  const [a, b] = [first.layout.cells[0], s.facilities.mine_2.layout.cells[0]]
  assert.ok(Math.abs(a.x - b.x) + Math.abs(a.z - b.z) >= 6, '別の入口')
  assert.equal(minesAt(bot, s, 16, 8).filter((m) => m.fac.name === first.name).length, 0)
})

test('経路探索は、採掘場のほかの深い岩を掘らない。採掘場のセルと浅い岩、脱出する行動は掘れる', () => {
  const { fac } = design()
  const s = state({ facilities: { mine_1: fac } })
  let m
  const bot = { registry: md, entities: {}, entity: { position: new Vec3(0, 70, 0) }, blockAt: () => null, pathfinder: { setMovements: (set) => { m = set } } }
  configureMovements(bot, s)
  const at = (name, x, y, z) => ({ name, position: new Vec3(x, y, z) })
  const cell = fac.layout.cells.find((c) => c.part === 'tunnel_east')
  // 深い岩・鉱石（家の床 64 より 10 以上下）は掘らない
  for (const name of ['stone', 'deepslate', 'andesite', 'iron_ore', 'deepslate_iron_ore']) assert.ok(m.exclusionBreak(at(name, 100, 40, 100)) >= 100, name)
  // 採掘場のセルは掘ってよい（まだ掘っていない坑道を通るとき）
  assert.equal(m.exclusionBreak(at('stone', cell.x, cell.y, cell.z)), 0)
  // 浅い岩、土と砂は今までどおり
  assert.equal(m.exclusionBreak(at('stone', 100, 60, 100)), 0)
  assert.equal(m.exclusionBreak(at('dirt', 100, 40, 100)), 0)
  // 帰る・脱出する行動は、閉じた所から出られなくならないように掘れる
  s.current = { verb: 'go_home' }
  assert.equal(m.exclusionBreak(at('stone', 100, 40, 100)), 0)
  s.current = { verb: 'goto_pos' }
  assert.ok(m.exclusionBreak(at('stone', 100, 40, 100)) >= 100)
})

test('protectedRock: 自分で建てた家があるときだけ。見つけた拠点（仮の宿）と家なしは今までどおり', () => {
  const p = new Vec3(100, 40, 100)
  assert.equal(protectedRock(state(), p, 'stone'), true)
  assert.equal(protectedRock({ home: { ...home, found: true } }, p, 'stone'), false)
  assert.equal(protectedRock({ home: null }, p, 'stone'), false)
  assert.equal(protectedRock(state(), p, 'dirt'), false)
})

test('caveOre: 洞窟に出ている深い鉱石は取らない。採掘場の壁（セルに接する）と浅い露頭は取る（2026-10-07 洞窟で蜘蛛に殺された）', () => {
  const { fac } = design()
  const s = state({ facilities: { mine_1: fac } })
  const cell = fac.layout.cells.find((c) => c.part === 'tunnel_east')
  const deep = new Vec3(100, 40, 100) // 家の床 64 より 10 以上下
  assert.equal(caveOre(s, deep, 'iron_ore'), true)
  assert.equal(caveOre(s, deep, 'deepslate_iron_ore'), true)
  // 採掘場のセルに接する壁の鉱石
  assert.equal(caveOre(s, new Vec3(cell.x, cell.y, cell.z + 1), 'iron_ore'), false)
  assert.equal(caveOre(s, new Vec3(cell.x, cell.y, cell.z), 'iron_ore'), false)
  // 浅い露頭（地表の石炭）、鉱石でないもの、家がないときは今までどおり
  assert.equal(caveOre(s, new Vec3(100, 60, 100), 'coal_ore'), false)
  assert.equal(caveOre(s, deep, 'stone'), false)
  assert.equal(caveOre({ home: null }, deep, 'iron_ore'), false)
  assert.equal(caveOre({ home: { ...home, found: true } }, deep, 'iron_ore'), false)
})

test('diggable: 洞窟に出ている深い鉱石は掘る対象にならない（歩いて行ける洞窟でも）。採掘場の壁と浅い露頭は対象', () => {
  const { fac } = design()
  const s = state({ facilities: { mine_1: fac } })
  const deep = new Vec3(100, 40, 100)
  const shallow = new Vec3(100, 62, 100)
  // どちらも空気に面した鉄鉱石（洞窟の壁）
  const blocks = { [`${deep.x},${deep.y},${deep.z}`]: 'iron_ore', [`${deep.x},${deep.y + 1},${deep.z}`]: 'air', [`${shallow.x},${shallow.y},${shallow.z}`]: 'iron_ore', [`${shallow.x},${shallow.y + 1},${shallow.z}`]: 'air' }
  const bot = world(blocks)
  bot.entity.position = new Vec3(100, 41, 100)
  assert.equal(diggable(bot, s, 'iron_ore', deep), false)
  bot.entity.position = new Vec3(100, 63, 100)
  assert.equal(diggable(bot, s, 'iron_ore', shallow), true)
})

// ---- 空洞に出たら、丸石で床・壁・天井を作って塞ぐ（sealing.mjs）----

// 置く動作のテスト用: 丸石を 1 個減らし、置いた場所を固いブロックにする。置けない場所（借りる隣が空気）は投げる
function sealer (items, blocks) {
  const bot = digger(items, blocks)
  const base = bot.blockAt
  let held = null
  bot.equip = async (item) => { held = item?.name }
  bot.placeBlock = async (ref, face) => {
    if (held === 'torch') return // 松明は壁に付くだけ（通路は空いたまま）
    const solid = base(ref.position).boundingBox === 'block' || blocks[key(ref.position)] === 'cobblestone'
    if (!solid) throw new Error('no reference block')
    const stone = items.find((i) => i.name === 'cobblestone')
    if (!stone?.count) throw new Error('no cobblestone')
    stone.count--
    blocks[key(ref.position.plus(face))] = 'cobblestone'
  }
  bot.blockAt = (p) => {
    const b = base(p)
    return b.name === 'cobblestone' ? { ...b, boundingBox: 'block' } : b
  }
  return bot
}

// 枝の 2 セル目のまわり（左の壁、天井、床）を洞窟にする
function cave (fac, blocks) {
  const cell = fac.layout.cells.filter((c) => c.part === 'branch_east_2_a')[1]
  blocks[`${cell.x - 1},${cell.y},${cell.z}`] = 'air' // 壁（枝は東西の坑道に直角: 南北に伸びるので、横は x）
  blocks[`${cell.x + 1},${cell.y + 1},${cell.z}`] = 'cave_air'
  blocks[`${cell.x},${cell.y + 2},${cell.z}`] = 'air' // 天井
  blocks[`${cell.x},${cell.y - 1},${cell.z}`] = 'air' // 床の穴
  return cell
}

test('枝が洞窟に出たら、丸石で床・壁・天井を作って塞ぎ、枝は打ち切らずに掘り進む', async () => {
  const { fac } = design()
  const s = state({ facilities: { mine_1: fac } })
  const blocks = {}
  const items = [{ name: 'stone_pickaxe', count: 1 }, { name: 'torch', count: 40 }, { name: 'cobblestone', count: 30 }]
  const bot = sealer(items, blocks)
  const cell = cave(fac, blocks)
  bot.entity.position = new Vec3(fac.layout.cells[0].x, 64, fac.layout.cells[0].z)
  const r = await PRIMITIVES.dig_mine(bot, s, { facility: 'mine_1' }, new AbortController().signal)
  assert.match(r, /walled off 4 cave openings with cobblestone/)
  assert.doesNotMatch(r, /branches cut short/)
  // 開いていた 4 面が丸石になっている
  for (const k of [`${cell.x - 1},${cell.y},${cell.z}`, `${cell.x + 1},${cell.y + 1},${cell.z}`, `${cell.x},${cell.y + 2},${cell.z}`, `${cell.x},${cell.y - 1},${cell.z}`]) {
    assert.equal(blocks[k], 'cobblestone', k)
  }
  assert.equal(items.find((i) => i.name === 'cobblestone').count, 30 - 4)
  // 枝は最後まで残り、囲まれた印が付く
  assert.equal(fac.layout.cells.filter((c) => c.part === 'branch_east_2_a').length, BRANCH.length)
  assert.equal(cell.sealed, true)
  assert.equal(mineStatus(bot, fac).next, null)
})

test('洞窟に出て丸石が足りなければ、設計は切らずに止まって待つ（paused）。通路を洞窟につなげない', async () => {
  const { fac } = design()
  const s = state({ facilities: { mine_1: fac } })
  const blocks = {}
  const items = [{ name: 'stone_pickaxe', count: 1 }, { name: 'torch', count: 40 }, { name: 'cobblestone', count: 2 }]
  const bot = sealer(items, blocks)
  const cell = cave(fac, blocks)
  const cells = fac.layout.cells.length
  bot.entity.position = new Vec3(fac.layout.cells[0].x, 64, fac.layout.cells[0].z)
  const r = await PRIMITIVES.dig_mine(bot, s, { facility: 'mine_1' }, new AbortController().signal)
  assert.match(r, /stopped: a cave opens at .*walling it off takes 4 cobblestone and you carry 2/)
  assert.equal(fac.layout.cells.length, cells, '設計は切らない')
  assert.equal(fac.layout.stopped, undefined)
  assert.deepEqual({ x: fac.layout.paused.x, y: fac.layout.paused.y, z: fac.layout.paused.z, need: fac.layout.paused.need }, { x: cell.x, y: cell.y, z: cell.z, need: 4 })
  assert.equal(fac.status, 'building')
  assert.notEqual(blocks[`${cell.x},${cell.y},${cell.z}`], 'air') // 洞窟側の石は残っている
  // 丸石を持って戻れば、続きを塞いで掘り、待ちの印は消える
  items.find((i) => i.name === 'cobblestone').count = 30
  const again = await PRIMITIVES.dig_mine(bot, s, { facility: 'mine_1' }, new AbortController().signal)
  assert.match(again, /walled off 4 cave openings/)
  assert.equal(fac.layout.paused, undefined)
})

test('洞窟に出て丸石が足りないとき、立っている所の横の石を掘って丸石にしてから塞ぐ', async () => {
  const { fac } = design()
  const s = state({ facilities: { mine_1: fac } })
  const blocks = {}
  const items = [{ name: 'stone_pickaxe', count: 1 }, { name: 'torch', count: 40 }, { name: 'cobblestone', count: 1 }]
  const bot = sealer(items, blocks)
  const dig = bot.dig
  const planned = plannedBlocks(fac.layout.cells)
  const quarried = []
  // 設計のマスの外の石だけが丸石になる（設計のマスを掘って落ちる分は、この試験では数えない）
  bot.dig = async (b) => {
    await dig(b)
    if (b.name === 'stone' && !planned.has(key(b.position))) { quarried.push(key(b.position)); items.find((i) => i.name === 'cobblestone').count++ }
  }
  bot.pathfinder.goto = async (g) => { if (g.x !== undefined) bot.entity.position = new Vec3(g.x + 0.5, g.y, g.z + 0.5) }
  cave(fac, blocks)
  bot.entity.position = new Vec3(fac.layout.cells[0].x, 64, fac.layout.cells[0].z)
  const r = await PRIMITIVES.dig_mine(bot, s, { facility: 'mine_1' }, new AbortController().signal)
  assert.match(r, /walled off 4 cave openings/)
  assert.equal(fac.layout.paused, undefined)
  assert.equal(quarried.length, 3, `掘った石: ${quarried}`) // 4 面に要る 4 個 - 持っていた 1 個
})

test('丸石が足りなくて待っている採掘場は、丸石を持つまで後回しにして、ほかに掘れる採掘場があればそちらを掘る', () => {
  const s = state()
  const a = designMine(world(), s, 16, mineSites(s))
  const b = designMine(world(), s, 16, mineSites(s))
  const bot = world({}, [{ name: 'cobblestone', count: 0 }])
  bot.entity.position = new Vec3(0, 64, 0)
  a.layout.paused = { x: 0, y: 0, z: 0, need: 4, reason: 'x' }
  assert.deepEqual(minePlan(bot, s, ['iron_ore']), { dig: b.name })
  // ほかになければ、その採掘場の続き（石を掘って丸石にする）
  b.status = 'done'
  assert.deepEqual(minePlan(bot, s, ['iron_ore']), { dig: a.name })
  // 丸石を持てば、先に待っていた採掘場に戻る
  b.status = 'planned'
  const stocked = world({}, [{ name: 'cobblestone', count: 4 }])
  stocked.entity.position = new Vec3(0, 64, 0)
  assert.deepEqual(minePlan(stocked, s, ['iron_ore']), { dig: a.name })
})

test('丸石を置けない（借りる隣がない）なら、そのセルを丸石でふさいで枝を打ち切る', async () => {
  const { fac } = design()
  const s = state({ facilities: { mine_1: fac } })
  const blocks = {}
  const items = [{ name: 'stone_pickaxe', count: 1 }, { name: 'torch', count: 40 }, { name: 'cobblestone', count: 30 }]
  const bot = sealer(items, blocks)
  const cell = cave(fac, blocks)
  // 壁の面だけ置けない（失敗しても、通路はふさぐ）
  const place = bot.placeBlock
  bot.placeBlock = async (ref, face) => {
    if (ref.position.plus(face).equals(new Vec3(cell.x - 1, cell.y, cell.z))) throw new Error('refused')
    return place(ref, face)
  }
  bot.entity.position = new Vec3(fac.layout.cells[0].x, 64, fac.layout.cells[0].z)
  const r = await PRIMITIVES.dig_mine(bot, s, { facility: 'mine_1' }, new AbortController().signal)
  assert.match(r, /branch_east_2_a at .*: could not wall off the cave at .*\(the passage was plugged\)/)
  assert.equal(blocks[`${cell.x},${cell.y},${cell.z}`], 'cobblestone') // 通路をふさいだ
})

test('もう空いている（洞窟の）セルも順に点検して、塞いでから先へ進む。囲まれていれば印だけ', async () => {
  const { fac } = design()
  const s = state({ facilities: { mine_1: fac } })
  const blocks = {}
  const items = [{ name: 'stone_pickaxe', count: 1 }, { name: 'torch', count: 40 }, { name: 'cobblestone', count: 30 }]
  const bot = sealer(items, blocks)
  const cell = cave(fac, blocks)
  // 洞窟のセルそのものも、もう空気（洞窟の一部）
  blocks[`${cell.x},${cell.y},${cell.z}`] = 'air'
  blocks[`${cell.x},${cell.y + 1},${cell.z}`] = 'air'
  bot.entity.position = new Vec3(fac.layout.cells[0].x, 64, fac.layout.cells[0].z)
  const r = await PRIMITIVES.dig_mine(bot, s, { facility: 'mine_1' }, new AbortController().signal)
  assert.match(r, /walled off 4 cave openings/)
  assert.equal(cell.sealed, true)
  assert.equal(mineStatus(bot, fac).next, null)
})

// 階段のセルの、囲む面のひとつ（壁）を洞窟にする（設計書 134）
function caveBesideStairs (fac, s, index, blocks) {
  const cell = fac.layout.cells[index]
  assert.equal(cell.part, 'stairs')
  const wall = enclosure(s, cell).find((f) => f.role === 'wall').pos
  blocks[`${wall.x},${wall.y},${wall.z}`] = 'air'
  return { cell, wall }
}
const stairsItems = (cobble) => [{ name: 'stone_pickaxe', count: 1 }, { name: 'torch', count: 40 }, { name: 'cobblestone', count: cobble }]

test('階段も、入口から 4 段より深くなったら洞窟に面した壁を丸石で塞ぐ（設計書 134）', async () => {
  const { fac, s } = design()
  const blocks = {}
  const items = stairsItems(30)
  const bot = sealer(items, blocks)
  const { wall } = caveBesideStairs(fac, s, 5, blocks)
  bot.entity.position = new Vec3(fac.layout.cells[0].x, 64, fac.layout.cells[0].z)
  s.facilities = { mine_1: fac }
  const r = await PRIMITIVES.dig_mine(bot, s, { facility: 'mine_1' }, new AbortController().signal)
  assert.match(r, /walled off 1 cave openings/)
  assert.equal(blocks[`${wall.x},${wall.y},${wall.z}`], 'cobblestone')
})

test('階段が洞窟に出て丸石が足りなければ、設計を切らずに待つ。浅い段（地上の空気に面した所）は塞がない', async () => {
  const deep = design()
  const blocksDeep = {}
  const botDeep = sealer(stairsItems(0), blocksDeep)
  const { cell } = caveBesideStairs(deep.fac, deep.s, 5, blocksDeep)
  botDeep.entity.position = new Vec3(deep.fac.layout.cells[0].x, 64, deep.fac.layout.cells[0].z)
  deep.s.facilities = { mine_1: deep.fac }
  const cells = deep.fac.layout.cells.length
  const r = await PRIMITIVES.dig_mine(botDeep, deep.s, { facility: 'mine_1' }, new AbortController().signal)
  assert.match(r, new RegExp(`stopped: a cave opens at .*walling it off takes 1 cobblestone and you carry 0`))
  assert.equal(deep.fac.layout.cells.length, cells)
  assert.deepEqual([deep.fac.layout.paused.x, deep.fac.layout.paused.y, deep.fac.layout.paused.z], [cell.x, cell.y, cell.z])

  const shallow = design()
  const blocksShallow = {}
  const botShallow = sealer(stairsItems(0), blocksShallow)
  caveBesideStairs(shallow.fac, shallow.s, 2, blocksShallow)
  botShallow.entity.position = new Vec3(shallow.fac.layout.cells[0].x, 64, shallow.fac.layout.cells[0].z)
  shallow.s.facilities = { mine_1: shallow.fac }
  const r2 = await PRIMITIVES.dig_mine(botShallow, shallow.s, { facility: 'mine_1' }, new AbortController().signal)
  assert.doesNotMatch(r2, /a cave opens/)
})

test('枝のない採掘場（LLM 設計）と階段は塞ぎ・点検の対象外。床の穴は今までどおり危ないとして切る', async () => {
  const { registerFacility } = await import('../src/facilities.mjs')
  const s = state()
  const fac = registerFacility(world(), s, 'mine_1', { kind: 'mine', boxes: [{ min_x: 8, min_z: 0, max_x: 15, max_z: 7 }], design: { bottom_y: 40, tunnels: 2, tunnel_length: 8 } })
  assert.equal(fac.layout.sealing, undefined)
  assert.equal(mineStatus(world(), fac).total, fac.layout.cells.length)
})
