import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { Knowledge } from '../src/knowledge.mjs'
import { ground } from '../src/candidates.mjs'
import { PRIMITIVES } from '../src/primitives.mjs'
import { surfaceExits, trackSurface } from '../src/surface.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')
const k = new Knowledge(md)

// 地下（y 40、頭上 3 ブロックが岩）に立つ。items: { 名前: 個数 }
function cave (items = {}, y = 40) {
  return {
    entity: { position: new Vec3(0.5, y, 0.5), onGround: true },
    entities: {},
    time: { timeOfDay: 1000 },
    health: 20,
    food: 20,
    heldItem: null,
    registry: md,
    inventory: { items: () => Object.entries(items).map(([name, count]) => ({ name, count })), emptySlotCount: () => 30 },
    blockAt: (p) => (p.y < y || p.y === y + 3 ? { name: 'stone', boundingBox: 'block' } : { name: 'cave_air', boundingBox: 'empty' }),
    findBlock: () => null,
    findBlocks: () => []
  }
}

const candidates = (bot, state) => ground(bot, { home: null, plan: null, unreachableDrops: new Set(), ...state }, k, { dig: () => [], hunt: () => [] }, { leaves: [] }).candidates

test('地下で松明が 4 本より少なければ、地上に出る手を出す。足りているとき、地上では出さない', () => {
  const state = { lastSurface: { x: 10, y: 64, z: 0 } }
  const c = candidates(cave({ torch: 2 }), state).find((x) => x.verb === 'surface')
  assert.ok(c)
  assert.deepEqual([c.pos.x, c.pos.y, c.pos.z], [10, 64, 0])
  assert.ok(!candidates(cave({ torch: 8 }), state).some((x) => x.verb === 'surface'))
  assert.ok(!candidates(cave({}, 70), state).some((x) => x.verb === 'surface'), '地上')
  // 戻る道がなければ出さない（どこへ行けばよいか分からない）
  assert.ok(!candidates(cave({}), {}).some((x) => x.verb === 'surface'))
})

test('地上に出る道: 最後に地上にいた場所が先、あとは鉱山の入口と家の玄関の外を近い順。今より高い所だけ', () => {
  const bot = cave()
  const state = {
    lastSurface: { x: 50, y: 64, z: 0 },
    facilities: { mine_1: { kind: 'mine', entrance: { x: 5, y: 63, z: 0 } }, farm: { kind: 'farm', entrance: { x: 1, y: 70, z: 1 } } },
    home: { outside: new Vec3(20, 70, 0) }
  }
  assert.deepEqual(surfaceExits(bot, state).map((p) => `${p.x},${p.z}`), ['50,0', '5,0', '20,0'])
  // 同じ高さの出口（地下の鉱山の底など）は道に数えない
  state.facilities.mine_1.entrance = { x: 5, y: 41, z: 0 }
  assert.deepEqual(surfaceExits(bot, state).map((p) => `${p.x},${p.z}`), ['50,0', '20,0'])
})

test('地上に立っているときだけ、最後に地上にいた場所を覚える', () => {
  const state = {}
  trackSurface(cave({}, 70), state, false)
  assert.deepEqual(state.lastSurface, { x: 0, y: 70, z: 0 })
  trackSurface(cave({}, 40), state, true) // 地下
  assert.deepEqual(state.lastSurface, { x: 0, y: 70, z: 0 })
  const air = cave({}, 70)
  air.entity.onGround = false
  air.entity.position = new Vec3(9, 70, 9)
  trackSurface(air, state, false) // 空中
  assert.equal(state.lastSurface.x, 0)
})

test('道がなければ、surface は理由を言って失敗する', async () => {
  await assert.rejects(PRIMITIVES.surface(cave(), {}, {}, new AbortController().signal), /no known way out/)
})
