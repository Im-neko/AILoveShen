import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { noteDig, noteShaft, quarrySpot, usableShaft, onShaftTop, isQuarryKind, QUARRY_REACH, QUARRY_RADIUS } from '../src/quarry.mjs'
import { digTargets, buriedTargets } from '../src/world.mjs'
import { aroundMe } from '../src/around.mjs'
import { columnWork } from '../src/leveling.mjs'
import { homeToJSON } from '../src/home.mjs'

const { Vec3 } = vec3Pkg
const v = (x, y, z) => new Vec3(x, y, z)
const md = minecraftData('1.21.4')

test('採掘の場所: 最初に地形を掘った所。近くを掘る間は動かず、離れた所を掘ったら移る', () => {
  const state = {}
  noteDig(state, v(10, 64, 10), 'stone', v(10, 65, 10))
  assert.deepEqual(state.quarry.spot, { x: 10, y: 64, z: 10 })
  noteDig(state, v(14, 63, 10), 'dirt', v(12, 65, 10)) // 場所の近く: 動かない
  assert.deepEqual(state.quarry.spot, { x: 10, y: 64, z: 10 })
  noteDig(state, v(10 + QUARRY_RADIUS + 5, 64, 10), 'stone', v(30, 65, 10)) // 離れた所: そこが新しい場所
  assert.equal(state.quarry.spot.x, 10 + QUARRY_RADIUS + 5)
})

test('木や作物を掘っても採掘の場所にならない', () => {
  const state = {}
  assert.equal(isQuarryKind('oak_log'), false)
  assert.equal(isQuarryKind('wheat'), false)
  noteDig(state, v(1, 64, 1), 'oak_log', v(1, 65, 1))
  assert.equal(state.quarry, undefined)
})

test('遠くに離れたら、その場所は使わない（遠出した先で戻らない）', () => {
  const state = {}
  noteDig(state, v(0, 64, 0), 'stone', v(0, 65, 0))
  assert.ok(quarrySpot(state, v(QUARRY_REACH - 1, 65, 0)))
  assert.equal(quarrySpot(state, v(QUARRY_REACH + 5, 65, 0)), null)
})

test('階段: 続きを掘れば底だけ深くなる。底が鉱石より下の階段は使わない', () => {
  const state = {}
  noteShaft(state, v(0, 70, 0), v(4, 62, 0))
  noteShaft(state, v(1, 70, 0), v(8, 55, 0)) // 入口が近い: 同じ階段の続き
  assert.equal(state.quarry.shafts.length, 1)
  assert.equal(state.quarry.shafts[0].bottom.y, 55)
  assert.ok(usableShaft(state, v(3, 70, 3), 50)) // 底 55 は鉱石 50 より上: 続けて下りられる
  assert.equal(usableShaft(state, v(3, 70, 3), 58), null) // 鉱石 58 は底より上: 階段は下へしか掘らない
  assert.equal(usableShaft(state, v(200, 70, 0), 50), null) // 遠い
  assert.ok(onShaftTop(state, 1, 1))
  assert.equal(onShaftTop(state, 20, 20), false)
})

test('採掘の場所と階段は保存される', () => {
  const state = { home: null, formerHomes: [] }
  noteDig(state, v(3, 64, 3), 'stone', v(3, 65, 3))
  noteShaft(state, v(3, 70, 3), v(3, 60, 6))
  const copy = JSON.parse(JSON.stringify({ quarry: state.quarry }))
  assert.deepEqual(copy.quarry, state.quarry)
  assert.equal(typeof homeToJSON, 'function')
})

// 石の地面（y < 70）に石が露出している所を 2 か所（場所の近くと、ボットの近く）用意する
function stoneBot (at) {
  const exposedStones = [v(30, 69, 0), v(2, 69, 0)]
  return {
    registry: md,
    entity: { position: at },
    entities: {},
    findBlocks: ({ matching, point, maxDistance }) => {
      const from = point ?? at
      return exposedStones.filter((p) => [].concat(matching).includes(md.blocksByName.stone.id) && p.distanceTo(from) <= maxDistance)
    },
    blockAt: (p) => {
      if (exposedStones.some((o) => o.equals(p))) return { name: 'stone', boundingBox: 'block', position: p }
      return p.y < 70 ? { name: 'stone', boundingBox: 'block', position: p } : { name: 'air', boundingBox: 'empty', position: p }
    }
  }
}
const noHome = () => ({ home: null, plan: null, unreachableBlocks: new Set() })

test('石を掘るときは、採掘の場所の近くを先に（ボットにいちばん近い所ではなく）', () => {
  const bot = stoneBot(v(10, 70, 0))
  const state = noHome()
  assert.deepEqual(digTargets(bot, state, 'stone').map((p) => p.x), [2, 30]) // 場所がなければ近い順
  state.quarry = { spot: { x: 30, y: 69, z: 0 }, shafts: [] }
  assert.deepEqual(digTargets(bot, state, 'stone').map((p) => p.x), [30]) // 場所があれば、その近く
  state.quarry.spot = { x: 60, y: 69, z: 0 } // 場所が遠ければ（ボットから 48 より遠い）使わない
  assert.deepEqual(digTargets(bot, state, 'stone').map((p) => p.x), [2, 30])
})

test('目の前のブロックは、採掘の場所の中のものを先に示す', () => {
  const at = v(0.5, 70, 0.5)
  const cells = new Map([['7,70,0', 'stone'], ['-2,70,0', 'stone']]) // 7 は場所の中、-2 のほうが近い
  const bot = {
    entity: { position: at },
    entities: {},
    blockAt: (p) => {
      const name = cells.get(`${p.x},${p.y},${p.z}`) ?? (p.y < 70 ? 'dirt' : 'air')
      return { name, position: p, getProperties: () => ({}) }
    }
  }
  const state = { quarry: { spot: { x: 12, y: 70, z: 0 }, shafts: [] } }
  const stone = (s) => aroundMe(bot, () => [], () => false, () => false, s).blocks.find((b) => b.name === 'stone')
  assert.equal(stone(null).nearest.x, -2) // 場所がなければ近い順
  assert.equal(stone(state).nearest.x, 7)
  assert.equal(stone(state).quarry, true)
})

test('前に掘った階段の底から続けて届く鉱石を先に選ぶ', () => {
  const ores = [v(2, 60, 0), v(20, 60, 0)]
  const bot = {
    registry: md,
    entity: { position: v(0, 70, 0) },
    entities: {},
    findBlocks: () => ores,
    blockAt: (p) => (ores.some((o) => o.equals(p)) ? { name: 'coal_ore', boundingBox: 'block' } : { name: 'stone', boundingBox: 'block' })
  }
  const state = noHome()
  assert.deepEqual(buriedTargets(bot, state, 'coal_ore', 70).map((t) => t.pos.x), [2, 20])
  state.quarry = { spot: null, shafts: [{ top: { x: 18, y: 70, z: 0 }, bottom: { x: 19, y: 62, z: 0 } }] }
  assert.deepEqual(buriedTargets(bot, state, 'coal_ore', 70).map((t) => t.pos.x), [20, 2]) // 階段の底に近いほうが先
})

test('整地は階段の入口の列を埋めない', () => {
  const bot = { blockAt: (p) => ({ name: p.y < 70 ? 'dirt' : 'air', position: p, boundingBox: p.y < 70 ? 'block' : 'empty' }) }
  const state = { home: null, plantings: [], quarry: { spot: null, shafts: [{ top: { x: 5, y: 70, z: 5 }, bottom: { x: 5, y: 60, z: 8 } }] } }
  assert.equal(columnWork(bot, state, 5, 5, 70), 'quarry stairs')
  assert.equal(typeof columnWork(bot, state, 20, 20, 70), 'object')
})

test('dig_down: 前の階段があれば、その底まで歩いて続きから（新しい階段を掘らない）', async () => {
  const { PRIMITIVES } = await import('../src/primitives.mjs')
  const ore = v(20, 60, 0)
  const at = v(0.5, 70, 0.5)
  const goals = []
  const bot = {
    registry: md,
    entity: { position: at, onGround: true },
    entities: {},
    time: { timeOfDay: 1000 },
    blockAt: (p) => (p.equals(ore) ? { name: 'coal_ore', boundingBox: 'block', position: p } : { name: p.y < 60 ? 'stone' : 'air', boundingBox: p.y < 60 ? 'block' : 'empty', position: p }),
    findBlocks: () => [ore],
    inventory: { items: () => [] },
    on: () => {},
    off: () => {},
    waitForTicks: async () => {},
    clearControlStates: () => {},
    pathfinder: {
      movements: {},
      setMovements () {},
      setGoal () {},
      goto: async (g) => { goals.push(g); bot.entity.position = v(g.x + 0.5, g.y, g.z + 0.5) }
    }
  }
  const state = { home: null, plan: null, unreachableBlocks: new Set(), loopCells: new Map(), goal: { surfaceY: 70 }, quarry: { spot: null, shafts: [{ top: { x: 18, y: 70, z: 0 }, bottom: { x: 19, y: 62, z: 0 } }] } }
  const result = await PRIMITIVES.dig_down(bot, state, { target: 'coal_ore', pos: ore }, new AbortController().signal)
  assert.match(result, /^walked down the stairs dug before/)
  assert.deepEqual([goals[0].x, goals[0].y, goals[0].z], [19, 62, 0])
  assert.equal(state.quarry.shafts.length, 1) // 同じ階段
  assert.equal(state.unreachableBlocks.size, 0)
})
