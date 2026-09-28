import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { registerFacility, mineStatus, onMineCell, finishedMine, updateStatus, FacilityError } from '../src/facilities.mjs'
import { columnWork } from '../src/leveling.mjs'
import { makeGoal, evaluate } from '../src/goals.mjs'
import { Knowledge } from '../src/knowledge.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')

// 地面は y 63 の草、その下は石（上は空気）。家は (0,64,0) のまわり 3x3。blocks は書き換えられる
function world (blocks = {}, items = []) {
  const at = (p) => blocks[`${p.x},${p.y},${p.z}`]
  const bot = {
    registry: md,
    entity: { position: new Vec3(10, 64, 0), onGround: true },
    inventory: { items: () => items, emptySlotCount: () => 30 },
    entities: {},
    findBlock: () => null,
    time: { timeOfDay: 1000, age: 0 },
    blockAt: (p) => {
      const b = at(p)
      const name = b?.name ?? b ?? (p.y > 63 ? 'air' : p.y === 63 ? 'grass_block' : 'stone')
      const empty = /air|water|lava/.test(name)
      return { name, boundingBox: empty ? 'empty' : 'block', position: p, harvestTools: undefined }
    },
    findBlocks: () => []
  }
  return bot
}
const home = { inside: new Vec3(0, 64, 0), min: new Vec3(-1, 64, -1), max: new Vec3(1, 64, 1), door: new Vec3(0, 64, 2), outside: new Vec3(0, 64, 3) }
const state = (extra = {}) => ({ home, plan: null, builds: {}, formerHomes: [], ...extra })
const ZONE = [{ min_x: 8, min_z: 0, max_x: 15, max_z: 7 }]
const MINE = { kind: 'mine', boxes: ZONE, design: { bottom_y: 40, tunnels: 2, tunnel_length: 8 } }
// 設計のセルをすべて空気にしたブロック
const dug = (fac, blocks = {}) => {
  for (const c of fac.layout.cells) for (let i = 0; i < c.h; i++) blocks[`${c.x},${c.y + i},${c.z}`] = 'air'
  return blocks
}

test('採掘場の設計: 4 x 4 の輪を回るらせん階段で底まで、下の部屋、坑道（地表の範囲は輪だけ）', () => {
  const s = state()
  const fac = registerFacility(world(), s, 'mine_1', MINE)
  const stairs = fac.layout.cells.filter((c) => c.part === 'stairs')
  assert.equal(stairs[0].y, 64)
  assert.equal(stairs.at(-1).y, 40)
  assert.equal(stairs.length, 25)
  // 隣り合う段は横に 1、下に 1
  for (let i = 1; i < stairs.length; i++) {
    assert.equal(Math.abs(stairs[i].x - stairs[i - 1].x) + Math.abs(stairs[i].z - stairs[i - 1].z), 1)
    assert.equal(stairs[i - 1].y - stairs[i].y, 1)
  }
  assert.equal(fac.layout.cells.filter((c) => c.part === 'room').length, 15)
  assert.deepEqual(fac.layout.tunnels, ['east', 'south'])
  assert.equal(fac.layout.cells.filter((c) => c.part.startsWith('tunnel')).length, 16)
  assert.equal(fac.box.max.x - fac.box.min.x, 3)
  assert.deepEqual(fac.entrance, { x: stairs[0].x, y: 64, z: stairs[0].z })
  assert.ok(onMineCell(s, new Vec3(stairs[5].x, stairs[5].y + 2, stairs[5].z)))
  assert.equal(onMineCell(s, new Vec3(stairs[5].x, stairs[5].y + 3, stairs[5].z)), false)
})

test('溶岩のある輪は避け、どこも届かなければ一番深く安全な y を返す（400: 設計を直す）', () => {
  // 中心の輪の列に溶岩: ほかの輪にずれる
  const lava = { '11,50,3': 'lava' }
  const fac = registerFacility(world(lava), state(), 'mine_1', { ...MINE, design: { bottom_y: 40 } })
  assert.ok(fac.layout.cells.every((c) => !(c.x === 11 && c.z === 3 && c.y <= 50 && c.y + c.h > 50)))
  // y 45 は一面の水: 一番深くて y 47 まで（水の隣は掘らない）
  const sea = {}
  for (let x = 0; x <= 20; x++) for (let z = -4; z <= 12; z++) sea[`${x},45,${z}`] = 'water'
  try {
    registerFacility(world(sea), state(), 'mine_2', { ...MINE, design: { bottom_y: 40 } })
    assert.fail('should refuse')
  } catch (e) {
    assert.ok(e instanceof FacilityError && !e.world)
    assert.match(e.message, /deepest safe one reaches y 47: water/)
  }
  assert.throws(() => registerFacility(world(), state(), 'mine_3', { ...MINE, design: { bottom_y: 62 } }), /not below the ground/)
})

test('facility(mine): 全部のセルが空いたら済み。次に掘るのは並びの最初の空いていないセル', () => {
  const k = new Knowledge(md)
  const s = state()
  const fac = registerFacility(world(), s, 'mine_1', MINE)
  const solverWorld = { inventory: {}, blocks: {}, mobs: {}, table: null, unlocked: () => true, remembered: new Set() }
  let bot = world({}, [{ name: 'stone_pickaxe', count: 1 }])
  s.goal = makeGoal({ predicate: 'facility', name: 'mine_1' }, bot, s, k)
  let r = evaluate(bot, s, k, solverWorld)
  assert.equal(r.met, false)
  assert.deepEqual(r.leaves.map((l) => [l.kind, l.facility]), [['dig_mine', 'mine_1']])
  assert.match(r.lines.join('\n'), /mine mine_1 \(stairs down to y 40, tunnels east\/south\)/)

  const blocks = dug(fac)
  const third = fac.layout.cells[3]
  blocks[`${third.x},${third.y + 1},${third.z}`] = 'stone'
  assert.equal(mineStatus(world(blocks), fac).index, 3)

  bot = world(dug(fac), [{ name: 'stone_pickaxe', count: 1 }])
  r = evaluate(bot, s, k, solverWorld)
  assert.equal(r.met, true)
  assert.equal(updateStatus(bot, fac) || fac.status, 'done')
  assert.equal(finishedMine(bot, s).bottom.y, 40)
})

test('掘っている途中で水が出たら、そこから先を設計から外して理由を残す', async () => {
  const { PRIMITIVES } = await import('../src/primitives.mjs')
  const s = state()
  const fac = registerFacility(world(), s, 'mine_1', MINE)
  const blocks = {}
  const bot = world(blocks, [{ name: 'stone_pickaxe', count: 1 }])
  const seventh = fac.layout.cells[7]
  bot.dig = async (b) => {
    blocks[`${b.position.x},${b.position.y},${b.position.z}`] = 'air'
    // 5 段目を掘ると、7 段目の隣に水が流れてくる
    if (b.position.y === fac.layout.cells[5].y) blocks[`${seventh.x},${seventh.y},${seventh.z + 1}`] = 'water' // 輪の外側
  }
  bot.equip = async () => {}
  bot.heldItem = null
  bot.pathfinder = { goto: async () => {}, setGoal: () => {} }
  bot.waitForTicks = async () => {}
  const total = fac.layout.cells.length
  const r = await PRIMITIVES.dig_mine(bot, s, { facility: 'mine_1' }, new AbortController().signal)
  assert.match(r, /stopped at .*: water at /)
  assert.ok(fac.layout.cells.length < total)
  assert.equal(fac.layout.stopped.reason.startsWith('water'), true)
})

test('整地は採掘場の入口を埋めない', () => {
  const s = state()
  const fac = registerFacility(world(), s, 'mine_1', MINE)
  const e = fac.layout.cells[1]
  assert.equal(columnWork(world(dug(fac)), s, e.x, e.z, 64), 'mine')
})

test('深い鉄は、下りきった採掘場があればそこから下りて探す', async () => {
  const { solve } = await import('../src/solver.mjs')
  const k = new Knowledge(md)
  const mine = { name: 'mine_1', bottom: { x: 8, y: 16, z: 0 }, above: true }
  const w = (extra) => ({ inventory: { stone_pickaxe: 1 }, blocks: {}, mobs: {}, table: null, unlocked: () => true, remembered: new Set(), digDepth: Infinity, ...extra })
  const deep = solve(k, w({ mine, buried: (name) => name === 'iron_ore' ? [{ pos: new Vec3(0, 20, 0), depth: 44 }] : [] }), [{ spec: 'raw_iron', count: 3 }])
  assert.deepEqual(deep.leaves.map((l) => [l.kind, l.mine?.name]), [['go_mine', 'mine_1']])
  // 浅いものはその場で掘り下げる
  const shallow = solve(k, w({ mine, buried: (name) => name === 'iron_ore' ? [{ pos: new Vec3(0, 60, 0), depth: 4 }] : [] }), [{ spec: 'raw_iron', count: 3 }])
  assert.deepEqual(shallow.leaves.map((l) => l.kind), ['dig_down'])
})
