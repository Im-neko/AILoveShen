// ベッドの上に立つと、家の中でも外へも経路が立たなかった（2026-10-07、docs/design/93）。
// 建築中の建物の上を歩かない規則（101 = 通れない）が、ベッドの上の足（o.y + 1）の頭のセルに掛かっていた。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import prismarineBlock from 'prismarine-block'
import pathfinderPkg from 'mineflayer-pathfinder'
import { configureMovements } from '../src/primitives.mjs'

const { Vec3 } = vec3Pkg
const { goals, pathfinder } = pathfinderPkg
const md = minecraftData('1.21.4')
const Block = prismarineBlock('1.21.4')

// 家: 床 y68、内側 x71..74 z-209..-206、壁の輪は x70/75 z-210/-205、壁 y69..72、屋根 y73。ベッドは (73,69,-207) と (73,69,-208)
function house ({ bed = true } = {}) {
  const set = new Map()
  const put = (x, y, z, name, props = {}) => { const b = Block.fromProperties(name, props, 0); b.position = new Vec3(x, y, z); set.set(`${x},${y},${z}`, b) }
  for (let x = 70; x <= 75; x++) {
    for (let z = -210; z <= -205; z++) {
      const wall = x === 70 || x === 75 || z === -210 || z === -205
      for (let y = 69; y <= 72; y++) if (wall) put(x, y, z, 'oak_planks')
      put(x, 73, z, 'oak_planks')
    }
  }
  if (bed) {
    put(73, 69, -207, 'white_bed', { part: 'foot', facing: 'north', occupied: false })
    put(73, 69, -208, 'white_bed', { part: 'head', facing: 'north', occupied: false })
  }
  return (p) => {
    const q = new Vec3(Math.floor(p.x), Math.floor(p.y), Math.floor(p.z))
    const b = set.get(`${q.x},${q.y},${q.z}`) ?? Block.fromProperties(q.y <= 68 ? 'grass_block' : 'air', {}, 0)
    b.position = q
    return b
  }
}

function pathFrom (at, goal, { bed = true } = {}) {
  const blockAt = house({ bed })
  const home = { min: new Vec3(71, 69, -209), max: new Vec3(74, 69, -206), door: new Vec3(73, 69, -205), inside: new Vec3(73, 69, -206), outside: new Vec3(73, 69, -204), breach: [] }
  const state = { home, formerHomes: [], plan: { origin: new Vec3(70, 69, -210), size: { width: 6, depth: 6, height: 5 } }, builds: {}, current: null }
  const bot = { registry: md, version: '1.21.4', entities: {}, blockAt, entity: { position: at, onGround: true, effects: [], height: 1.8 }, game: { minY: 0, height: 384 }, inventory: { items: () => [] }, world: { getBlock: blockAt }, on () {}, once () {}, removeListener () {}, emit () {}, _client: { on () {} }, clearControlStates () {}, setControlState () {}, pathfinder: {} }
  pathfinder(bot)
  let movements
  const set = bot.pathfinder.setMovements
  bot.pathfinder.setMovements = (m) => { movements = m; set(m) }
  configureMovements(bot, state)
  const { value: { result } } = bot.pathfinder.getPathFromTo(movements, at, goal, { timeout: 2000 }).next()
  return { result, movements }
}

test('ベッドの上に立っていても、家の中の別のセルへ経路が立つ', () => {
  const { result } = pathFrom(new Vec3(73.5, 69.5625, -206.1), new goals.GoalBlock(71, 69, -208))
  assert.equal(result.status, 'success')
})

test('ベッドがなければ（床の上なら）もちろん立つ', () => {
  const { result } = pathFrom(new Vec3(73.5, 69, -206.1), new goals.GoalBlock(71, 69, -208), { bed: false })
  assert.equal(result.status, 'success')
})

test('壁の上と屋根の上は今までどおり通れない（建築中に壁へ登らない）。部屋の頭の高さだけを除く', () => {
  const { movements: m } = pathFrom(new Vec3(73.5, 69, -206.1), new goals.GoalBlock(72, 69, -206), { bed: false })
  const at = (x, y, z) => ({ name: 'air', position: new Vec3(x, y, z) })
  assert.ok(m.exclusionStep(at(70, 71, -207)) >= 100, '壁の輪の上')
  assert.ok(m.exclusionStep(at(72, 72, -207)) >= 100, '部屋の屋根の高さ')
  assert.equal(m.exclusionStep(at(72, 71, -207)), 0, 'ベッドの上の頭の高さ')
  assert.equal(m.exclusionStep(at(72, 70, -207)), 0)
  assert.ok(m.exclusionStep(at(75, 71, -207)) >= 100, '壁の輪（外側）')
})
