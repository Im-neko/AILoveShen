// 半壊・廃屋になった前の家を更地にする（設計書 166）
import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import prismarineBlock from 'prismarine-block'
import { PRIMITIVES } from '../src/primitives.mjs'
import { createTools } from '../src/tools.mjs'
import { Knowledge } from '../src/knowledge.mjs'
import { BuildPlan } from '../src/build.mjs'

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

test('clear_site 道具（名前なし）: 前の家がなければ断る。一番近い前の家の手になる', async () => {
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
  assert.match((await tools([])('clear_site', {})).result, /no former home on record/)
  assert.match((await tools([far, home])('clear_site', {})).result, /"verb":"clear_former_home","door":\{"x":0,"y":64,"z":-2\}/)
})

// ---- 街の再設計で印のついた建物・畑・植林場（設計書 167） ----

function village () {
  // 建物 hut（底面 x,z -2..2、床の層 y 64）と畑 field（x,z 10..13、耕地 y 63）、植林場 grove
  const blocks = {}
  for (let x = -2; x <= 2; x++) for (let z = -2; z <= 2; z++) {
    blocks[`${x},64,${z}`] = 'oak_planks'
    if (Math.abs(x) === 2 || Math.abs(z) === 2) blocks[`${x},65,${z}`] = 'oak_planks'
  }
  blocks['0,65,0'] = 'chest'
  for (let x = 10; x <= 13; x++) for (let z = 10; z <= 13; z++) { blocks[`${x},63,${z}`] = 'farmland'; blocks[`${x},64,${z}`] = 'wheat' }
  for (const [x, z] of [[30, 30], [33, 30], [30, 33]]) blocks[`${x},64,${z}`] = 'oak_sapling'
  const cells = []
  for (let x = 0; x < 5; x++) for (let z = 0; z < 5; z++) cells.push({ x, y: 0, z, block: 'planks' })
  const plan = new BuildPlan({ blocks: cells, width: 5, depth: 5, height: 3, kind: 'build', design: { name: 'hut' } })
  plan.origin = new Vec3(-2, 64, -2)
  plan.seen.add('0,64,0')
  const facilities = {
    hut: { name: 'hut', kind: 'build', box: { min: { x: -2, y: 64, z: -2 }, max: { x: 2, y: 66, z: 2 } } },
    field: { name: 'field', kind: 'farm', box: { min: { x: 10, y: 63, z: 10 }, max: { x: 13, y: 63, z: 13 } }, layout: { y: 63, crop: 'wheat', cells: [] } },
    grove: { name: 'grove', kind: 'tree_farm', box: { min: { x: 30, y: 64, z: 30 }, max: { x: 33, y: 64, z: 33 } }, layout: { cells: [{ x: 30, y: 64, z: 30 }] } }
  }
  return { blocks, plan, facilities }
}

test('clear_facility: 建物を更地にして、施設・建物・チェストの記憶・印を外す。底面の外は残す', async () => {
  const { blocks, plan, facilities } = village()
  const w = world({ ...blocks, '3,64,0': 'cobblestone' })
  const state = { home: null, formerHomes: [], builds: { hut: plan }, facilities, clearMarks: ['hut', 'field'], plantings: [], unreachableDrops: new Set(), memory: { chests: { '0,65,0': { x: 0, y: 65, z: 0, contents: {} } } } }
  const r = await PRIMITIVES.clear_facility(fakeBot(w), state, { name: 'hut' }, signal)
  assert.match(r, /cleared the build hut down to bare ground/)
  for (let x = -2; x <= 2; x++) for (let z = -2; z <= 2; z++) for (const y of [64, 65]) assert.equal(w.blockAt(new Vec3(x, y, z)).name, 'air', `${x},${y},${z}`)
  assert.equal(w.blockAt(new Vec3(3, 64, 0)).name, 'cobblestone')
  assert.equal(state.facilities.hut, undefined)
  assert.equal(state.builds.hut, undefined)
  assert.deepEqual(state.clearMarks, ['field'])
  assert.deepEqual(state.memory.chests, {})
})

test('clear_facility: 畑は作物を、植林場は苗木と植えた記録を外す。ない・対象外の施設は断る', async () => {
  const { blocks, facilities } = village()
  const w = world(blocks)
  const state = { home: null, formerHomes: [], builds: {}, facilities, clearMarks: ['field', 'grove'], plantings: [{ x: 30, y: 64, z: 30 }, { x: 90, y: 64, z: 90 }], unreachableDrops: new Set() }
  const bot = fakeBot(w)
  bot.entity.position = new Vec3(11.5, 64, 11.5) // 手が届く所に立つ（経路探索の模擬はない）
  assert.match(await PRIMITIVES.clear_facility(bot, state, { name: 'field' }, signal), /cleared the farm field/)
  assert.equal(w.blockAt(new Vec3(11, 64, 11)).name, 'air')
  assert.equal(w.blockAt(new Vec3(11, 63, 11)).name, 'farmland', '耕地そのものは地面の高さなので残る（水がなければ土に戻る）')
  bot.entity.position = new Vec3(31.5, 64, 31.5)
  assert.match(await PRIMITIVES.clear_facility(bot, state, { name: 'grove' }, signal), /cleared the tree_farm grove/)
  assert.deepEqual(state.plantings, [{ x: 90, y: 64, z: 90 }], '外の植えた記録は残す')
  await assert.rejects(PRIMITIVES.clear_facility(bot, state, { name: 'field' }, signal), /there is no facility named field/)
  state.facilities.shaft = { name: 'shaft', kind: 'mine', box: { min: { x: 0, y: 60, z: 0 }, max: { x: 1, y: 60, z: 1 } } }
  await assert.rejects(PRIMITIVES.clear_facility(bot, state, { name: 'shaft' }, signal), /a mine is not taken down this way/)
})

test('clear_site 道具（名前つき）: 印のついた施設だけ。印がなければ断る', async () => {
  const { plan, facilities } = village()
  const tools = (clearMarks) => createTools({
    bot: { ...fakeBot(world()), time: { timeOfDay: 1000 } },
    state: { home: null, formerHomes: [], builds: { hut: plan }, facilities, clearMarks, memory: {}, busy: false, reflex: false },
    knowledge: new Knowledge(md),
    run: async (c) => ({ ok: true, result: JSON.stringify({ verb: c.verb, name: c.name }), seconds: 0 }),
    candidates: () => [],
    check: () => []
  })
  assert.match((await tools(['hut'])('clear_site', { name: 'hut' })).result, /"verb":"clear_facility","name":"hut"/)
  assert.match((await tools(['field'])('clear_site', { name: 'hut' })).result, /hut is not marked for taking down.*marked: field/)
  assert.match((await tools([])('clear_site', { name: 'hut' })).result, /nothing is marked/)
  assert.match((await tools(['ghost'])('clear_site', { name: 'ghost' })).result, /there is no facility named ghost/)
})
