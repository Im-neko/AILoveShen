// 畑の水のマスの水は、バケツを満たす水源に数えない（設計書 137）。置いた水を汲み戻して畑に水が残らなかった。
// 道具の use_item で汲むのは、承知なら通す（置き間違えの汲み戻し）: free では警告を添える。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { createTools } from '../src/tools.mjs'
import { Knowledge } from '../src/knowledge.mjs'
import { waterSource } from '../src/world.mjs'

const { Vec3 } = vec3Pkg
const v = (x, y, z) => new Vec3(x, y, z)
const md = minecraftData('1.21.4')

const FARM_WATER = v(44, 74, -478)
const POND = v(60, 74, -478)
const farm = { name: 'wheat_farm', kind: 'farm', box: { min: { x: 39, y: 74, z: -483 }, max: { x: 48, y: 75, z: -474 } }, layout: { crop: 'wheat', cells: [], water: [{ x: 44, y: 74, z: -478 }] } }
const makeState = () => ({ home: null, formerHomes: [], builds: {}, memory: {}, busy: false, reflex: false, facilities: { wheat_farm: farm } })

const waterIds = new Set([`${FARM_WATER.x},${FARM_WATER.y},${FARM_WATER.z}`, `${POND.x},${POND.y},${POND.z}`])
const blockAt = (p) => {
  const key = `${p.x},${p.y},${p.z}`
  if (waterIds.has(key)) return { name: 'water', boundingBox: 'empty', position: p, getProperties: () => ({ level: 0 }) }
  if (p.y < 74) return { name: 'dirt', boundingBox: 'block', position: p, getProperties: () => ({}) }
  return { name: 'air', boundingBox: 'empty', position: p, getProperties: () => ({}) }
}

test('水源は畑の水のマスを選ばず、ほかの水を選ぶ（畑の水のほうが近くても）', () => {
  const bot = {
    entity: { position: v(46.5, 74, -476.5) },
    registry: md,
    blockAt,
    findBlocks: () => [FARM_WATER, POND]
  }
  assert.deepEqual(waterSource(bot, makeState()), POND)
  // 畑が登録されていなければ、近いほう
  assert.deepEqual(waterSource(bot, { ...makeState(), facilities: {} }), FARM_WATER)
  // 畑の水しかなければ、水源なし（探しに行く）
  assert.equal(waterSource({ ...bot, findBlocks: () => [FARM_WATER] }, makeState()), null)
})

function toolsFor () {
  const bot = {
    entity: { position: v(46.5, 74, -476.5) },
    entities: {},
    registry: md,
    time: { timeOfDay: 1000 },
    blockAt,
    inventory: { items: () => [{ name: 'bucket', count: 1 }] }
  }
  const runs = []
  const callTool = createTools({
    bot,
    state: makeState(),
    knowledge: new Knowledge(md),
    run: async (c) => { runs.push(c); return { ok: true, result: 'done', seconds: 0 } },
    candidates: () => [],
    check: () => []
  })
  return { callTool, runs }
}

test('use_item: 畑の水のマスの水を汲むのは、警告を添えて通す（置き間違えの汲み戻し）', async () => {
  const { callTool, runs } = toolsFor()
  const r = await callTool('use_item', { item: 'bucket', x: FARM_WATER.x, y: FARM_WATER.y, z: FARM_WATER.z })
  assert.equal(r.ok, true)
  assert.equal(runs.length, 1)
  assert.match(r.result, /not enforced: this water is the water cell of the farm wheat_farm: taking it dries the farmland/)
})

test('use_item: 畑の外の水は、警告なしで汲む', async () => {
  const { callTool, runs } = toolsFor()
  const r = await callTool('use_item', { item: 'bucket', x: POND.x, y: POND.y, z: POND.z })
  assert.equal(r.ok, true)
  assert.equal(runs.length, 1)
  assert.doesNotMatch(String(r.result), /water cell of the farm/)
})
