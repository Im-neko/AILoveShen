import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { createTools } from '../src/tools.mjs'
import { lookAround } from '../src/state.mjs'
import { Knowledge } from '../src/knowledge.mjs'

const { Vec3 } = vec3Pkg
const v = (x, y, z) => new Vec3(x, y, z)
const md = minecraftData('1.21.4')
const knowledge = new Knowledge(md)
const DAY = 1000
const NIGHT = 18000

// y 70 の石の上の 5x5 の家（shelter.test.mjs と同じ）: 壁は x 0..4、z 0..4、室内は 1..3、ドアは (2, 70, 4)。
// 前の家は x 20..24 に同じ形で建っていて、中にベッドがある
const home = { door: v(2, 70, 4), inside: v(2, 70, 3), outside: v(2, 70, 5), min: v(1, 70, 1), max: v(3, 70, 3), bed: null, breach: [] }
const former = { door: v(22, 70, 4), inside: v(22, 70, 3), outside: v(22, 70, 5), min: v(21, 70, 1), max: v(23, 70, 3), bed: v(22, 70, 2), breach: [] }

function blockAt (p, extra = {}) {
  const key = `${p.x},${p.y},${p.z}`
  if (extra[key]) return { name: extra[key], boundingBox: 'block', position: p }
  if (p.y < 70) return { name: 'stone', boundingBox: 'block', position: p }
  for (const h of [home, former]) {
    const [x0, z0] = [h.min.x - 1, h.min.z - 1]
    const [lx, lz] = [p.x - x0, p.z - z0]
    if (lx < 0 || lx > 4 || lz < 0 || lz > 4 || p.y > 71) continue
    if (p.x === h.door.x && p.z === h.door.z) return { name: 'oak_door', boundingBox: 'block', position: p, getProperties: () => ({ open: false }) }
    if (lx === 0 || lx === 4 || lz === 0 || lz === 4) return { name: 'oak_planks', boundingBox: 'block', position: p }
    if (h.bed && p.equals(h.bed)) return { name: 'white_bed', boundingBox: 'block', position: p }
  }
  return { name: 'air', boundingBox: 'empty', position: p }
}

function setup ({ time = DAY, at = v(2.5, 70, 2.5), items = [], mobs = [], extra = {}, candidates = [] } = {}) {
  const entity = { position: at, eyeHeight: 1.62 }
  const entities = { 0: entity }
  mobs.forEach((m, i) => { entities[i + 1] = { id: i + 1, type: 'hostile', height: 1.99, ...m } })
  const bot = {
    entity,
    entities,
    registry: md,
    time: { timeOfDay: time },
    health: 20,
    food: 20,
    blockAt: (p) => blockAt(p, extra),
    findBlock: () => null,
    findBlocks: () => [],
    world: { raycast: () => null },
    inventory: { items: () => items.map(([name, count]) => ({ name, count })) }
  }
  const state = { home, formerHomes: [former], plan: null, busy: false, reflex: false, memory: {}, unreachableDrops: new Set() }
  const runs = []
  const checks = []
  const callTool = createTools({
    bot,
    state,
    knowledge,
    run: async (c, label) => { runs.push({ c, label }); return { ok: true, result: 'done', seconds: 0 } },
    candidates: () => candidates,
    check: (specs) => { checks.push(specs); return specs.map(() => ({ met: false, lines: ['have 1 stick (0/1)'], impossible: [] })) }
  })
  return { callTool, runs, checks, bot, state }
}

test('夜に家の中にいれば、外に出る道具は理由をつけて断り、実行しない', async () => {
  const { callTool, runs } = setup({ time: NIGHT })
  const r = await callTool('goto', { x: 10, y: 70, z: 10 })
  assert.equal(r.refused, true)
  assert.match(r.result, /staying inside for the night/)
  assert.equal(runs.length, 0)
  // 家の中での道具は夜でも使える
  assert.equal((await callTool('wait')).ok, true)
  assert.equal(runs[0].c.inside, true)
})

test('昼にドアの前に敵がいれば外に出る道具は断るが、その敵と戦うことはできる（cleared と同じ）', async () => {
  const { callTool, runs } = setup({ mobs: [{ name: 'skeleton', position: v(2.5, 70, 6.5) }] })
  assert.match((await callTool('goto', { x: 10, y: 70, z: 10 })).result, /hostile mobs wait near the door/)
  const r = await callTool('attack', { entity: 1 })
  assert.equal(r.ok, true)
  assert.equal(runs[0].c.confront, true)
})

test('今の家は掘らないが、前の家（置いてきたベッド）は名指しすれば掘れる（town4d）', async () => {
  const { callTool, runs } = setup({ at: v(10.5, 70, 10.5) })
  assert.match((await callTool('dig', { x: 0, y: 70, z: 2 })).result, /part of the current home/)
  const r = await callTool('dig', { x: 22, y: 70, z: 2 })
  assert.equal(r.ok, true)
  assert.deepEqual([runs[0].c.verb, runs[0].c.block], ['dig', 'white_bed'])
})

test('掘る道具はブロックの名前を世界から決め、道具がなくて何も落ちないものは断る', async () => {
  const { callTool, runs } = setup({ at: v(10.5, 70, 10.5) })
  assert.match((await callTool('dig', { x: 10, y: 69, z: 10 })).result, /stone drops nothing without one of: .*pickaxe/)
  assert.match((await callTool('dig', { x: 10, y: 71, z: 10 })).result, /there is air/)
  const { callTool: withPick } = setup({ at: v(10.5, 70, 10.5), items: [['wooden_pickaxe', 1]] })
  assert.equal((await withPick('dig', { x: 10, y: 69, z: 10 })).ok, true)
  assert.equal(runs.length, 0)
})

test('置く道具は空いたセルに、隣のブロックの面を借りて置く。自分が立っている所には置かない', async () => {
  const { callTool, runs } = setup({ at: v(10.5, 70, 10.5), items: [['dirt', 4]] })
  assert.match((await callTool('place', { item: 'dirt', x: 10, y: 70, z: 10 })).result, /you are standing in/)
  assert.match((await callTool('place', { item: 'dirt', x: 10, y: 69, z: 10 })).result, /taken by stone/)
  assert.match((await callTool('place', { item: 'cobblestone', x: 11, y: 70, z: 10 })).result, /you have no cobblestone/)
  assert.equal((await callTool('place', { item: 'dirt', x: 11, y: 70, z: 10 })).ok, true)
  assert.deepEqual(runs[0].c.against, v(11, 69, 10))
})

test('クラフトはレシピから作業台が要るかを決め、近くに作業台がなければ断る', async () => {
  const { callTool, runs } = setup({ at: v(10.5, 70, 10.5) })
  assert.match((await callTool('craft', { item: 'wooden_pickaxe' })).result, /needs a crafting table within reach/)
  assert.equal((await callTool('craft', { item: 'stick', times: 2 })).ok, true)
  assert.deepEqual([runs[0].c.needsTable, runs[0].c.times], [false, 2])
  assert.match((await callTool('craft', { item: 'dirt' })).result, /has no crafting recipe/)
})

test('ソルバーの提案は、今ある候補の id だけ実行する', async () => {
  const suggestion = { id: 'dig oak_log at 5,70,5', verb: 'dig', pos: v(5, 70, 5), block: 'oak_log' }
  const { callTool, runs } = setup({ at: v(10.5, 70, 10.5), candidates: [suggestion] })
  assert.match((await callTool('do_suggestion', { id: 'dig oak_log at 9,70,9' })).result, /not a current suggestion/)
  assert.equal((await callTool('do_suggestion', { id: suggestion.id })).ok, true)
  assert.equal(runs[0].label, suggestion.id)
})

test('調べものの道具はすぐ返り、行動を実行しない。how_to_get はソルバーの木を返す', async () => {
  const { callTool, runs, checks } = setup()
  const recipe = await callTool('recipe_of', { item: 'stick' })
  assert.ok(recipe.result.crafting.some((r) => r.ingredients.oak_planks === 2 || Object.keys(r.ingredients).some((n) => n.endsWith('_planks'))))
  const how = await callTool('how_to_get', { item: 'stick', count: 1 })
  assert.deepEqual(how.result.steps, ['have 1 stick (0/1)'])
  assert.deepEqual(checks[0], [{ predicate: 'have', item: 'stick', count: 1 }])
  assert.equal(runs.length, 0)
})

test('実行中と反射の間は、道具を断る', async () => {
  const { callTool, state } = setup()
  state.busy = true
  assert.match((await callTool('wait')).result, /another action is running/)
  state.busy = false
  state.reflex = true
  assert.match((await callTool('wait')).result, /reflex/)
})

test('まわりの形: 3 段の縦穴の底では、隣がすべて歩いて上がれない壁になる（town4c）', () => {
  // (10, 67..69, 10) の 1 マスを掘った縦穴。まわりは y 69 まで石、その上は空気。東に水たまり
  const pit = (p) => {
    if (p.x === 12 && p.z === 10 && p.y === 69) return { name: 'water', boundingBox: 'empty' }
    if (p.y >= 70) return { name: 'air', boundingBox: 'empty' }
    if (p.x === 10 && p.z === 10 && p.y >= 67) return { name: 'air', boundingBox: 'empty' }
    return { name: 'stone', boundingBox: 'block' }
  }
  const bot = { entity: { position: v(10.5, 67, 10.5) }, blockAt: pit }
  const view = lookAround(bot, 2)
  assert.equal(view.walls_around, 8)
  assert.deepEqual(view.grid, [
    '+3 +3 +3 +3 +3',
    '+3 +3 +3 +3 +3',
    '+3 +3  @ +3  ~',
    '+3 +3 +3 +3 +3',
    '+3 +3 +3 +3 +3'
  ])
  assert.equal(view.head_blocked, false)
  assert.equal(view.standing_on, 'stone')
})
