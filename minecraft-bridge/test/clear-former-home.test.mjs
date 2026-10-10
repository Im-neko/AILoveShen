// 半壊・廃屋になった前の家を更地にする（設計書 166）
import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import prismarineBlock from 'prismarine-block'
import { PRIMITIVES } from '../src/primitives.mjs'
import { createTools } from '../src/tools.mjs'
import { Knowledge } from '../src/knowledge.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')
const Block = prismarineBlock('1.21.4')
const signal = new AbortController().signal

// 地面は y 62 まで土、63 が草。前の家の底面は x, z が -2..2（部屋は -1..1）、床の層は y 64
function world (blocks = {}) {
  const set = new Map(Object.entries(blocks))
  const blockAt = (p) => {
    const q = new Vec3(Math.floor(p.x), Math.floor(p.y), Math.floor(p.z))
    const name = set.get(`${q.x},${q.y},${q.z}`) ?? (q.y < 63 ? 'dirt' : q.y === 63 ? 'grass_block' : 'air')
    const b = Block.fromProperties(name, {}, 0)
    b.position = q
    return b
  }
  return { set, blockAt }
}

function fakeBot (w) {
  const inv = [{ name: 'dirt', count: 8, type: md.itemsByName.dirt.id }]
  return {
    registry: md,
    entity: { position: new Vec3(0.5, 64, 0.5), onGround: true, effects: {} },
    entities: {},
    blockAt: w.blockAt,
    inventory: { items: () => inv.filter((i) => i.count > 0), emptySlotCount: () => 30 },
    heldItem: null,
    equip: async () => {},
    waitForTicks: async () => {},
    time: { timeOfDay: 1000 },
    dig: async (b) => { w.set.set(`${b.position.x},${b.position.y},${b.position.z}`, 'air') },
    placeBlock: async (below, v) => {
      const p = below.position.plus(v)
      inv.find((i) => i.name === 'dirt').count--
      w.set.set(`${p.x},${p.y},${p.z}`, 'dirt')
    }
  }
}

// 半壊: 北の壁の半分と屋根の半分がない。床に穴。外のとなりの丸石は別の物
function ruin () {
  const blocks = {}
  for (let x = -2; x <= 2; x++) {
    for (let z = -2; z <= 2; z++) {
      blocks[`${x},64,${z}`] = 'oak_planks'
      if (Math.abs(x) === 2 || Math.abs(z) === 2) for (const y of [65, 66]) blocks[`${x},${y},${z}`] = 'oak_planks'
      if (x >= 0) blocks[`${x},67,${z}`] = 'oak_planks'
    }
  }
  for (let x = -2; x <= 0; x++) for (const y of [65, 66]) blocks[`${x},${y},2`] = 'air'
  blocks['0,64,0'] = 'air'
  blocks['0,63,0'] = 'air'
  blocks['-1,65,-1'] = 'chest'
  blocks['3,64,0'] = 'cobblestone'
  const home = { door: new Vec3(0, 64, -2), inside: new Vec3(0, 64, -1), outside: new Vec3(0, 64, -3), min: new Vec3(-1, 64, -1), max: new Vec3(1, 64, 1), bed: null, breach: [] }
  return { blocks, home }
}

const newState = (home, extra = {}) => ({ home: null, formerHomes: [home], builds: {}, plantings: [], unreachableDrops: new Set(), memory: { chests: { '-1,65,-1': { x: -1, y: 65, z: -1, contents: { oak_log: 3 } } } }, ...extra })

test('clear_former_home: 半壊の家（壁・屋根・床・チェスト）を壊して地面にそろえ、底面の外は残す。前の家の記録とチェストの記憶を外す', async () => {
  const { blocks, home } = ruin()
  const w = world(blocks)
  const state = newState(home)
  const r = await PRIMITIVES.clear_former_home(fakeBot(w), state, { door: home.door }, signal)
  assert.match(r, /cleared the old house at 0,64,-2 down to bare ground/)
  for (let x = -2; x <= 2; x++) for (let z = -2; z <= 2; z++) {
    for (let y = 64; y <= 68; y++) assert.equal(w.blockAt(new Vec3(x, y, z)).name, 'air', `${x},${y},${z}`)
    assert.equal(w.blockAt(new Vec3(x, 63, z)).boundingBox, 'block', `${x},63,${z} は地面（穴は埋める）`)
  }
  assert.equal(w.blockAt(new Vec3(3, 64, 0)).name, 'cobblestone')
  assert.deepEqual(state.formerHomes, [])
  assert.deepEqual(state.memory.chests, {})
})

test('clear_former_home: 記録にない家は断る。もう更地なら、掘らずに記録だけ外す', async () => {
  const { home } = ruin()
  const state = newState(home)
  await assert.rejects(PRIMITIVES.clear_former_home(fakeBot(world()), state, { door: new Vec3(9, 64, 9) }, signal), /not in the record of former homes/)
  assert.match(await PRIMITIVES.clear_former_home(fakeBot(world()), state, { door: home.door }, signal), /the site was already bare/)
  assert.deepEqual(state.formerHomes, [])
})

test('clear_old_home 道具: 前の家がなければ断る。一番近い前の家の手になる', async () => {
  const { home } = ruin()
  const far = { door: new Vec3(100, 64, 100), inside: new Vec3(100, 64, 101), outside: new Vec3(100, 64, 99), min: new Vec3(99, 64, 101), max: new Vec3(101, 64, 103), bed: null, breach: [] }
  const tools = (formerHomes) => createTools({
    bot: { ...fakeBot(world()), time: { timeOfDay: 1000 } },
    state: { home: null, formerHomes, builds: {}, memory: {}, busy: false, reflex: false },
    knowledge: new Knowledge(md),
    run: async (c) => ({ ok: true, result: JSON.stringify({ verb: c.verb, door: c.door }), seconds: 0 }),
    candidates: () => [],
    check: () => []
  })
  assert.match((await tools([])('clear_old_home', {})).result, /no former home on record/)
  assert.match((await tools([far, home])('clear_old_home', {})).result, /"verb":"clear_former_home","door":\{"x":0,"y":64,"z":-2\}/)
})
