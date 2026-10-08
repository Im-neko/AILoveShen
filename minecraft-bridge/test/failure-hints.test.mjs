// 失敗と断りに、邪魔なものの名前と次の手を添える（2026-10-08 の点検）
import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { PRIMITIVES } from '../src/primitives.mjs'
import { explainPathFailure } from '../src/move.mjs'
import { createTools } from '../src/tools.mjs'
import { Knowledge } from '../src/knowledge.mjs'
import { newMemory, rememberChest } from '../src/memory.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')
const k = new Knowledge(md)
const SOLID = new Set(['grass_block', 'dirt', 'stone', 'oak_planks', 'farmland'])

function world (blocks = {}, items = []) {
  const dug = []
  const inv = items.map(([name, count]) => ({ name, count, type: md.itemsByName[name].id }))
  const bot = {
    registry: md,
    entity: { position: new Vec3(10.5, 68, 0.5), onGround: true, yaw: 0 },
    entities: {},
    time: { timeOfDay: 1000, age: 100 },
    heldItem: null,
    health: 20,
    food: 20,
    inventory: { items: () => inv, emptySlotCount: () => 30 },
    blockAt: (p) => {
      const q = new Vec3(Math.floor(p.x), Math.floor(p.y), Math.floor(p.z))
      const name = blocks[`${q.x},${q.y},${q.z}`] ?? (q.y === 67 ? 'grass_block' : q.y < 67 ? 'dirt' : 'air')
      const solid = SOLID.has(name)
      return { name, boundingBox: solid ? 'block' : 'empty', shapes: solid ? [[0, 0, 0, 1, 1, 1]] : [], position: q, getProperties: () => ({}) }
    },
    world: { getBlock: (p) => bot.blockAt(p) },
    pathfinder: { goto: async () => {}, setGoal: () => {} },
    equip: async () => {},
    lookAt: async () => {},
    activateBlock: async () => {},
    waitForTicks: async () => {},
    on: () => {},
    off: () => {},
    dig: async (b) => { dug.push(b.name); blocks[`${b.position.x},${b.position.y},${b.position.z}`] = 'air' },
    placeBlock: async (against, v) => { const p = against.position.plus(v); blocks[`${p.x},${p.y},${p.z}`] = 'oak_fence' }
  }
  return { bot, dug, blocks }
}
const signal = () => new AbortController().signal

test('置くセルに草花が乗っていたら、先にどけて置く（柵・松明が「the block is still white_tulip」で断られた）', async () => {
  const { bot, dug } = world({ '10,68,1': 'white_tulip' }, [['oak_fence', 3]])
  const c = { item: 'oak_fence', pos: new Vec3(10, 68, 1), against: new Vec3(10, 67, 1) }
  assert.match(await PRIMITIVES.place_at(bot, {}, c, signal()), /^placed oak_fence at 10,68,1/)
  assert.deepEqual(dug, ['white_tulip'])
  // 空気のセルは掘らない
  const plain = world({}, [['oak_fence', 3]])
  await PRIMITIVES.place_at(plain.bot, {}, { item: 'oak_fence', pos: new Vec3(11, 68, 1), against: new Vec3(11, 67, 1) }, signal())
  assert.deepEqual(plain.dug, [])
})

test('耕せない理由に、次の手を添える: 穴は土で埋める、上の固いブロックは掘る', async () => {
  const till = (blocks) => {
    const { bot } = world(blocks, [['stone_hoe', 1]])
    return PRIMITIVES.till(bot, {}, { pos: new Vec3(10, 67, 1) }, signal())
  }
  await assert.rejects(till({ '10,67,1': 'air' }), /the ground is air: a hole in the farm, fill it with dirt first/)
  await assert.rejects(till({ '10,67,1': 'stone' }), /the ground is stone: the farm level step rebuilds it with dirt/)
  await assert.rejects(till({ '10,68,1': 'oak_planks' }), /oak_planks is on top of the grass_block: dig it first/)
})

test('預けられない理由に、知っているチェストの数と空き、次の手を添える。預ける物がもう手元にないときは、それを言う', async () => {
  const home = { door: new Vec3(2, 70, 4), inside: new Vec3(2, 70, 3), outside: new Vec3(2, 70, 5), min: new Vec3(1, 70, 1), max: new Vec3(3, 70, 3), bed: null, breach: [] }
  const memory = newMemory()
  rememberChest(memory, new Vec3(1, 70, 1), { oak_log: 99 }, 0, { used: 27, size: 27 })
  const state = { home, memory }
  const { bot } = world({}, [['dirt', 10]])
  bot.entity.position = new Vec3(2.5, 70, 2.5)
  // 開けるチェストがない世界: withOpenChest が「no chest」で家のチェストを忘れる前に、理由の文を直接見る
  const { noRoomReason } = await import('../src/primitives.mjs')
  assert.match(noRoomReason(bot, state, [{ item: 'dirt' }]), /none of the 1 known chests has room for dirt \(0 free slots by the records.*place another chest in the home.*toss\)/)
  assert.match(noRoomReason(bot, state, [{ item: 'cobblestone' }]), /no longer in the inventory/)
})

test('経路探しの時間切れに、届かない目標だという見立てと次の手を添える（元の文は先頭に残る）。ほかの失敗はそのまま', () => {
  const bot = { entity: { position: new Vec3(0.5, 68, 0.5) } }
  const goal = { x: 43, y: 68, z: -200 }
  const timeout = Object.assign(new Error('Took to long to decide path to goal!'), { name: 'Timeout' })
  const e = explainPathFailure(bot, goal, timeout)
  assert.match(e.message, /^Took to long to decide path to goal! The goal 43,68,-200 \(\d+m away\) is probably unreachable from here \(behind a closed door/)
  assert.match(e.message, /open the door, or choose another place$/)
  assert.equal(e.name, 'Timeout')
  // 二重に添えない
  assert.equal(explainPathFailure(bot, goal, e), e)
  const other = new Error('No path to the goal!')
  assert.equal(explainPathFailure(bot, goal, other), other)
})

test('plant / till の断りに、次に作業するマスを添える。草花の乗ったマスは受ける', async (t) => {
  process.env.BRIDGE_GUARDS = 'standard' // 標準の断り（設計書 125）
  t.after(() => { delete process.env.BRIDGE_GUARDS })
  const fac = (cells) => ({ kind: 'tree_farm', name: 'grove', layout: { sapling: 'birch_sapling', spacing: 5, cells } })
  const { bot } = world({ '10,68,1': 'poppy' }, [['birch_sapling', 2]])
  const home = { door: new Vec3(0, 68, 0), inside: new Vec3(0, 68, 1), outside: new Vec3(0, 68, -1), min: new Vec3(-1, 68, 1), max: new Vec3(1, 68, 3), bed: null, breach: [] }
  const state = { home, facilities: { grove: fac([{ x: 10, y: 68, z: 1 }]) }, goal: { spec: { predicate: 'facility', name: 'grove' } }, memory: {}, plan: null, busy: false, reflex: false, plantings: [] }
  const runs = []
  const call = createTools({ bot, state, knowledge: k, run: async (c) => { runs.push(c); return { ok: true, result: 'done', seconds: 0 } }, candidates: () => [], check: async () => [] })
  const wrong = await call('plant', { x: 12, y: 68, z: 3, intent: 'x' })
  assert.equal(wrong.refused, true)
  assert.match(wrong.result, /will not plant at 12,68,3: .*The next cell to work on is 10,68,1 \(leave x, y, z out to use it\)/)
  // 草花の乗ったマスは、植える手に渡る（plant の手がどける）
  const ok = await call('plant', { x: 10, y: 68, z: 1, intent: 'x' })
  assert.equal(ok.refused, undefined, ok.result)
  assert.equal(runs[0].verb, 'plant')
})

test('まく・松明・置く土台の失敗にも、邪魔なブロックを名指しする', async () => {
  const sow = (blocks) => PRIMITIVES.sow(world(blocks, [['wheat_seeds', 1]]).bot, {}, { item: 'wheat_seeds', pos: new Vec3(10, 67, 1) }, signal())
  await assert.rejects(sow({}), /no free farmland at 10,67,1 any more \(it is grass_block, not farmland: till it first\)/)
  await assert.rejects(sow({ '10,67,1': 'farmland', '10,68,1': 'wheat' }), /\(wheat is on it already\)/)
  const torch = (blocks) => PRIMITIVES.place_torch_at(world(blocks, [['torch', 1]]).bot, {}, { pos: new Vec3(10, 68, 1) }, signal())
  await assert.rejects(torch({ '10,67,1': 'air' }), /no ground for a torch at 10,1 any more \(the floor under it is air\)/)
  await assert.rejects(torch({ '10,68,1': 'oak_planks' }), /\(oak_planks is in the cell\)/)
  const placeAt = world({ '10,67,1': 'air' }, [['oak_fence', 1]])
  await assert.rejects(PRIMITIVES.place_at(placeAt.bot, {}, { item: 'oak_fence', pos: new Vec3(10, 68, 1), against: new Vec3(10, 67, 1) }, signal()), /nothing to place against at 10,67,1 any more \(it is air; place the block next to a solid one\)/)
})
