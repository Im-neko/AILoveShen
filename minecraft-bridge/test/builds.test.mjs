import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { registerBuild, homeSideOrigin, buildAllowsDig, growHome, BuildError, isHomeCell } from '../src/builds.mjs'
import { BuildPlan } from '../src/build.mjs'
import { protectedReason, inHouse } from '../src/home.mjs'

const { Vec3 } = vec3Pkg
const v = (x, y, z) => new Vec3(x, y, z)

// 室内 3x3（x 101..103、z 1..3、立つ高さ y 70、床 y 69）。壁は x 100..104、z 0..4、高さ 70..72、屋根 73。
// ドアは北の壁 (102, 70, 0)
function world (extra = {}) {
  const blocks = new Map()
  const set = (x, y, z, name) => blocks.set(`${x},${y},${z}`, name)
  for (let x = 90; x <= 120; x++) for (let z = -10; z <= 20; z++) set(x, 69, z, 'grass_block')
  for (let x = 100; x <= 104; x++) {
    for (let z = 0; z <= 4; z++) {
      set(x, 69, z, 'oak_planks')
      set(x, 73, z, 'oak_planks')
      const wall = x === 100 || x === 104 || z === 0 || z === 4
      for (let y = 70; y <= 72; y++) if (wall) set(x, y, z, 'oak_planks')
    }
  }
  set(102, 70, 0, 'oak_door')
  set(102, 71, 0, 'oak_door')
  for (const [k, name] of Object.entries(extra)) blocks.set(k, name)
  const bot = {
    blockAt: (p) => {
      const name = blocks.get(`${p.x},${p.y},${p.z}`) ?? 'air'
      return { name, position: p, boundingBox: name === 'air' || name.endsWith('_door') ? 'empty' : 'block', getProperties: () => ({ facing: 'north' }) }
    }
  }
  return { bot, set }
}

const home = () => ({ min: v(101, 70, 1), max: v(103, 70, 3), door: v(102, 70, 0), inside: v(102, 70, 1), outside: v(102, 70, -1), breach: [] })

// 東に 5x5 の部屋: 床、外殻、家との境の壁の真ん中に 2 段の入口
function annex () {
  const blocks = []
  for (let x = 0; x < 5; x++) for (let z = 0; z < 5; z++) blocks.push({ x, y: 0, z, block: 'planks' })
  for (let y = 1; y <= 3; y++) {
    for (let x = 0; x < 5; x++) {
      for (let z = 0; z < 5; z++) {
        const wall = x === 0 || x === 4 || z === 0 || z === 4
        if (wall) blocks.push({ x, y, z, block: x === 0 && z === 2 && y <= 2 ? 'air' : 'planks' })
      }
    }
  }
  for (let x = 0; x < 5; x++) for (let z = 0; z < 5; z++) blocks.push({ x, y: 4, z, block: 'planks' })
  return { blocks, size: { width: 5, depth: 5, height: 5 } }
}

test('増築は家の外壁の列に重ね、壁に沿って中央をそろえる（床の高さは家の床）', () => {
  const plan = { size: { width: 5, depth: 7 } }
  assert.deepEqual(homeSideOrigin(home(), plan, 'east'), v(104, 69, -1))
  assert.deepEqual(homeSideOrigin(home(), plan, 'west'), v(96, 69, -1))
  assert.deepEqual(homeSideOrigin(home(), { size: { width: 5, depth: 4 } }, 'south'), v(100, 69, 4))
  assert.deepEqual(homeSideOrigin(home(), { size: { width: 5, depth: 4 } }, 'north'), v(100, 69, -3))
})

test('東の増築を登録すると、家の壁の入口だけ掘ってよくなる', () => {
  const { bot } = world()
  const state = { home: home(), builds: {} }
  const plan = registerBuild(bot, state, { name: 'annex', ...annex(), anchor: 'home:east', purpose: 'a bedroom' })
  assert.deepEqual(plan.origin, v(104, 69, 0))
  const opening = v(104, 70, 2)
  assert.equal(buildAllowsDig(state, opening, bot.blockAt(opening)), true)
  assert.equal(protectedReason(state, opening, 0, bot.blockAt(opening)), null)
  // 入口でない家の壁は守る
  const wall = v(104, 70, 1)
  assert.match(protectedReason(state, wall, 0, bot.blockAt(wall)), /current home/)
  // 建物の範囲は採掘の対象から外す
  assert.equal(inHouse(state, v(107, 71, 2)), true)
  // 空けるマスの判定: 壁があるうちは済んでいない
  assert.equal(plan.isPlaced(bot, plan.blocks.find((b) => b.block === 'air')), false)
  assert.equal(plan.materialsNeeded(bot).air, undefined)
})

test('ドア・床・重なり・家がないときは断り、理由を返す', () => {
  const { bot } = world()
  const state = { home: home(), builds: {} }
  const door = { blocks: [{ x: 2, y: 1, z: 4, block: 'air' }, { x: 0, y: 0, z: 0, block: 'planks' }], size: { width: 5, depth: 5, height: 2 } }
  assert.throws(() => registerBuild(bot, state, { name: 'a', ...door, anchor: 'home:north' }), /oak_door/)
  const floor = { blocks: [{ x: 0, y: 0, z: 2, block: 'air' }, { x: 4, y: 1, z: 0, block: 'planks' }], size: { width: 5, depth: 5, height: 2 } }
  assert.throws(() => registerBuild(bot, state, { name: 'c', ...floor, anchor: 'home:east' }), /floor/)
  registerBuild(bot, state, { name: 'annex', ...annex(), anchor: 'home:east' })
  assert.throws(() => registerBuild(bot, state, { name: 'annex', ...annex(), anchor: 'home:west' }), /already exists/)
  assert.throws(() => registerBuild(bot, state, { name: 'twin', ...annex(), anchor: 'home:east' }), /overlap the build annex/)
  assert.throws(() => registerBuild(bot, { home: null }, { name: 'x', ...annex(), anchor: 'near_home' }), (e) => e instanceof BuildError && /no home/.test(e.message))
})

test('near_home は家から離れた平らな場所を探す', () => {
  const { bot } = world()
  const state = { home: home(), builds: {} }
  const small = { blocks: [{ x: 0, y: 0, z: 0, block: 'cobblestone' }, { x: 2, y: 2, z: 2, block: 'cobblestone' }], size: { width: 3, depth: 3, height: 3 } }
  const plan = registerBuild(bot, state, { name: 'shed', ...small, anchor: 'near_home' })
  assert.equal(plan.origin.y, 69)
  const clear = (a, b) => a.max < b.min - 3 || a.min > b.max + 3
  assert.ok(clear({ min: plan.origin.x, max: plan.origin.x + 2 }, { min: 100, max: 104 }) || clear({ min: plan.origin.z, max: plan.origin.z + 2 }, { min: 0, max: 4 }))
})

test('増築ができたら、家の室内を新しい部屋まで広げる（閉じていなければ広げない）', () => {
  const { bot, set } = world()
  const h = home()
  // 東の部屋: 壁 x 104..108、z 0..4、入口 (104, 70..71, 2)、屋根 73
  for (let x = 104; x <= 108; x++) {
    for (let z = 0; z <= 4; z++) {
      set(x, 69, z, 'oak_planks')
      set(x, 73, z, 'oak_planks')
      const wall = x === 108 || z === 0 || z === 4 || x === 104
      for (let y = 70; y <= 72; y++) if (wall) set(x, y, z, 'oak_planks')
      if (!wall) for (let y = 70; y <= 72; y++) set(x, y, z, 'air')
    }
  }
  set(104, 70, 2, 'air')
  set(104, 71, 2, 'air')
  assert.equal(growHome(bot, h), true)
  assert.equal(isHomeCell(h, v(106, 70, 2)), true)
  assert.equal(isHomeCell(h, v(104, 70, 2)), true) // 入口
  assert.equal(isHomeCell(h, v(104, 70, 1)), false) // 壁
  assert.deepEqual([h.min.x, h.max.x], [101, 107])
  // 屋根に穴があれば外とつながっている: 広げない
  const leaky = world()
  leaky.set(104, 70, 2, 'air')
  leaky.set(104, 71, 2, 'air')
  const h2 = home()
  assert.equal(growHome(leaky.bot, h2), false)
  assert.equal(h2.cells, undefined)
})

test('空けるマスは名前付きの建物だけ', () => {
  assert.throws(() => new BuildPlan({ blocks: [{ x: 0, y: 0, z: 0, block: 'air' }], width: 1, depth: 1, height: 1 }), /only for named builds/)
  const p = new BuildPlan({ blocks: [{ x: 0, y: 0, z: 0, block: 'air' }], width: 1, depth: 1, height: 1, kind: 'build' })
  assert.equal(BuildPlan.fromJSON(p.toJSON()).kind, 'build')
})

test('家の壁の中のドアと、木・水のある側は断る（置けずに詰まるため）', () => {
  const state = { home: home(), builds: {} }
  const doorInWall = { blocks: [{ x: 4, y: 1, z: 2, block: 'planks' }, { x: 0, y: 1, z: 2, block: 'door' }], size: { width: 5, depth: 5, height: 2 } }
  assert.throws(() => registerBuild(world().bot, state, { name: 'a', ...doorInWall, anchor: 'home:east' }), /inside the oak_planks of the home's wall/)
  const tree = world({ '108,70,2': 'oak_log' }) // 東の壁のマス
  assert.throws(() => registerBuild(tree.bot, state, { name: 'b', ...annex(), anchor: 'home:east' }), /oak_log .* home:east side/)
  const pond = world({ '106,69,2': 'water' })
  assert.throws(() => registerBuild(pond.bot, state, { name: 'c', ...annex(), anchor: 'home:east' }), /water/)
})

test('地図のマス（anchor map）: 選んだ場所のまわりの平らな所に置く。マスがなければ断る', async () => {
  const { mapAround, mapKind } = await import('../src/map.mjs')
  const { bot } = world({ '112,70,12': 'oak_log' })
  bot.entity = { position: v(102.5, 70, 2.5) }
  const state = { home: home(), builds: {} }
  const small = { blocks: [{ x: 0, y: 0, z: 0, block: 'cobblestone' }, { x: 2, y: 2, z: 2, block: 'cobblestone' }], size: { width: 3, depth: 3, height: 3 } }
  assert.throws(() => registerBuild(bot, state, { name: 's', ...small, anchor: 'map' }), /needs the chosen cell/)
  const plan = registerBuild(bot, state, { name: 's', ...small, anchor: 'map', site: { x: 110, z: 10 } })
  assert.ok(Math.abs(plan.origin.x + 1 - 110) <= 6 && Math.abs(plan.origin.z + 1 - 10) <= 6)
  assert.equal(plan.origin.y, 69)
  // 地図: 家の中心から 64x64、家と建物の範囲、種類
  const map = mapAround(bot, state, 8)
  assert.deepEqual(map.center, { x: 102, z: 2 })
  assert.equal(map.cells.length, 16)
  assert.deepEqual(map.cells[8][8], ['built', 4]) // 家の屋根（床の層から +4）
  assert.deepEqual(map.cells[0][0], ['ground', 0])
  assert.equal(map.builds[0].name, 's')
  assert.equal(mapKind('water'), 'water')
  assert.equal(mapKind('oak_log'), 'tree')
})
