// 家の部屋のいらないブロックを片付ける（設計書 165）
import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { strayBlocks, describeStrays } from '../src/room-clear.mjs'
import { PRIMITIVES } from '../src/primitives.mjs'
import { ground } from '../src/candidates.mjs'
import minecraftData from 'minecraft-data'
import { Knowledge } from '../src/knowledge.mjs'

const { Vec3 } = vec3Pkg
const v = (x, y, z) => new Vec3(x, y, z)
const knowledge = new Knowledge(minecraftData('1.21.4'))

// 部屋は x 1..3、z 1..3、床の高さ y 70、壁の高さ 3。blocks で上書き
function room (blocks = {}, { plan = null } = {}) {
  const set = new Map(Object.entries(blocks))
  const home = { door: v(2, 70, 4), inside: v(2, 70, 3), outside: v(2, 70, 5), min: v(1, 70, 1), max: v(3, 70, 3), bed: null, breach: [], design: { wall_height: 3 } }
  const blockAt = (p) => {
    const name = set.get(`${p.x},${p.y},${p.z}`) ?? (p.y < 70 ? 'dirt' : 'air')
    return { name, boundingBox: /air|torch|grass$/.test(name) && name !== 'grass_block' ? 'empty' : 'block', position: p }
  }
  const entity = { position: v(2.5, 70, 2.5), eyeHeight: 1.62, height: 1.8, onGround: true }
  const dug = []
  const bot = {
    entity, entities: {}, blockAt, time: { timeOfDay: 1000, age: 0 }, health: 20, food: 20, heldItem: null,
    canDigBlock: () => true, equip: async () => {}, waitForTicks: async () => {}, on: () => {}, off: () => {},
    dig: async (b) => { set.set(`${b.position.x},${b.position.y},${b.position.z}`, 'air'); dug.push(b.name) },
    inventory: { items: () => [], emptySlotCount: () => 30 },
    world: { raycast: () => null }, pathfinder: { bestHarvestTool: () => null, goto: async () => {}, setGoal: () => {} }
  }
  return { home, bot, dug, state: { home, plan, unreachableDrops: new Set() } }
}

test('部屋の丸石・土・草ブロック・板材は片付ける。家具・松明・ドア・ベッドと、部屋の外は残す', () => {
  const r = room({
    '1,70,1': 'cobblestone', '2,70,2': 'dirt', '3,70,3': 'grass_block', '3,72,1': 'oak_planks',
    '2,70,1': 'chest', '1,70,2': 'crafting_table', '3,70,2': 'white_bed', '1,71,3': 'wall_torch', '2,70,3': 'furnace',
    '0,70,2': 'oak_planks', '4,70,2': 'cobblestone', '2,73,2': 'cobblestone', '2,69,2': 'cobblestone'
  })
  const list = strayBlocks(r.bot, r.state)
  assert.deepEqual(list.map((p) => `${p.x},${p.y},${p.z}`).sort(), ['1,70,1', '2,70,2', '3,70,3', '3,72,1'].sort())
  assert.match(describeStrays(r.bot, list), /cobblestone x1/)
})

test('計画が置くと決めた正しいブロックは残す。計画の場所でも、違うブロックなら片付ける', () => {
  const plan = {
    origin: v(0, 70, 0),
    blocks: [{ x: 1, y: 0, z: 1, block: 'planks' }, { x: 2, y: 0, z: 2, block: 'planks' }],
    worldPos (b) { return this.origin.offset(b.x, b.y, b.z) },
    looksPlaced (b, block) { return /_planks$/.test(block.name) }
  }
  const r = room({ '1,70,1': 'oak_planks', '2,70,2': 'cobblestone' }, { plan })
  assert.deepEqual(strayBlocks(r.bot, r.state).map((p) => `${p.x},${p.y},${p.z}`), ['2,70,2'])
})

test('clear_room: 近い順に全部掘る。掘る物がなければ失敗にして、しばらく出さない', async () => {
  const r = room({ '1,70,1': 'cobblestone', '3,70,1': 'dirt', '2,71,2': 'oak_planks' })
  const out = await PRIMITIVES.clear_room(r.bot, r.state, {}, new AbortController().signal)
  assert.match(out, /cleared 3 blocks out of the room \(.*cobblestone x1.*\)/)
  assert.deepEqual(r.dug.sort(), ['cobblestone', 'dirt', 'oak_planks'])
  assert.deepEqual(strayBlocks(r.bot, r.state), [])
  await assert.rejects(PRIMITIVES.clear_room(r.bot, r.state, {}, new AbortController().signal), /nothing to clear/)
  assert.ok(r.state.clearRoomBlockedUntil > 0)
})

test('候補: 部屋にいらないブロックがあれば、家の中で出す。なければ出さない', () => {
  const snap = { dig: () => [], hunt: () => [] }
  const ids = (r) => ground(r.bot, r.state, knowledge, snap, { leaves: [] }).candidates.map((c) => c.id)
  const dirty = room({ '1,70,1': 'cobblestone', '2,70,2': 'cobblestone' })
  assert.ok(ids(dirty).some((id) => /^clear the stray blocks out of the home \(2: cobblestone x2\)/.test(id)), ids(dirty).join(' | '))
  assert.ok(!ids(room()).some((id) => /stray/.test(id)))
})
