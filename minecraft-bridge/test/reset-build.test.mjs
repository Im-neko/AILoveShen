// 壊して作り直すときは、一度更地にしてから（2026-10-10: 壊した跡の上に作り直して、跡と新しい壁が混ざった）。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import prismarineBlock from 'prismarine-block'
import { PRIMITIVES } from '../src/primitives.mjs'
import { createTools } from '../src/tools.mjs'
import { BuildPlan } from '../src/build.mjs'
import { Knowledge } from '../src/knowledge.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')
const Block = prismarineBlock('1.21.4')
const signal = new AbortController().signal

// 地面は y 62 まで土、63 が草。建物の底面は x, z が -2..2、床の層は y 64（地面の上）
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

function site () {
  const blocks = {}
  const cells = []
  for (let x = -2; x <= 2; x++) {
    for (let z = -2; z <= 2; z++) {
      blocks[`${x},64,${z}`] = 'oak_planks' // 床
      cells.push({ x: x + 2, y: 0, z: z + 2, block: 'planks' })
      if (Math.abs(x) === 2 || Math.abs(z) === 2) for (const y of [65, 66]) blocks[`${x},${y},${z}`] = 'oak_planks'
    }
  }
  blocks['0,65,0'] = 'chest'
  blocks['3,64,0'] = 'cobblestone' // 底面の外（となりの物）
  blocks['0,63,1'] = 'air' // 底面の中の穴
  const plan = new BuildPlan({ blocks: cells, width: 5, depth: 5, height: 4, kind: 'build', design: { name: 'hut' } })
  plan.origin = new Vec3(-2, 64, -2)
  return { blocks, plan }
}

test('reset_build: 底面の中の建物（保護された壁・床・チェスト）を壊して地面にそろえ、底面の外には触らない。置いた記録を捨てる', async () => {
  const { blocks, plan } = site()
  const w = world(blocks)
  plan.seen.add('0,64,0')
  const state = { home: null, formerHomes: [], builds: { hut: plan }, plantings: [], unreachableDrops: new Set() }
  const r = await PRIMITIVES.reset_build(fakeBot(w), state, { build: 'hut' }, signal)
  assert.match(r, /cleared the site of the build hut down to bare ground/)
  for (let x = -2; x <= 2; x++) for (let z = -2; z <= 2; z++) {
    for (let y = 64; y <= 67; y++) assert.equal(w.blockAt(new Vec3(x, y, z)).name, 'air', `${x},${y},${z}`)
    assert.ok(w.blockAt(new Vec3(x, 63, z)).boundingBox === 'block', `${x},63,${z} は地面`)
  }
  assert.equal(w.blockAt(new Vec3(3, 64, 0)).name, 'cobblestone', '底面の外は残る')
  assert.equal(plan.seen.size, 0)
})

test('reset_build: もう更地なら、掘らずに記録だけ捨てる。置いた敷地のない建物は断る', async () => {
  const { plan } = site()
  const w = world()
  plan.seen.add('0,64,0')
  const state = { home: null, formerHomes: [], builds: { hut: plan }, plantings: [], unreachableDrops: new Set() }
  assert.match(await PRIMITIVES.reset_build(fakeBot(w), state, { build: 'hut' }, signal), /the site was already bare/)
  assert.equal(plan.seen.size, 0)
  await assert.rejects(PRIMITIVES.reset_build(fakeBot(w), state, { build: 'none' }, signal), /no placed build named none/)
})

test('build_next {name, reset: true}: 名前が要る。敷地のない建物は断る。ある建物は reset_build の手になる', async () => {
  const { plan } = site()
  const unplaced = new BuildPlan({ blocks: [{ x: 0, y: 0, z: 0, block: 'planks' }], width: 1, depth: 1, height: 1, kind: 'build' })
  const w = world()
  const callTool = createTools({
    bot: { ...fakeBot(w), time: { timeOfDay: 1000 } },
    state: { home: null, formerHomes: [], builds: { hut: plan, empty: unplaced }, memory: {}, busy: false, reflex: false },
    knowledge: new Knowledge(md),
    run: async (c) => ({ ok: true, result: JSON.stringify(c), seconds: 0 }),
    candidates: () => [],
    check: () => []
  })
  assert.match((await callTool('build_next', { reset: true })).result, /reset needs the build name/)
  assert.match((await callTool('build_next', { name: 'empty', reset: true })).result, /has no site yet/)
  assert.match((await callTool('build_next', { name: 'hut', reset: true })).result, /"verb":"reset_build","build":"hut"/)
})

test('underBuild: 建物（名前付き・建てている家）の真下は、経路探索が掘らない。外と上は掘ってよい', async () => {
  const { underBuild } = await import('../src/builds.mjs')
  const { configureMovements } = await import('../src/primitives.mjs')
  const { plan } = site()
  const state = { home: null, formerHomes: [], builds: { hut: plan }, plan: null }
  assert.equal(underBuild(state, new Vec3(0, 63, 0)), true)
  assert.equal(underBuild(state, new Vec3(3, 60, 0)), true, '底面のまわり 1 マスまで')
  assert.equal(underBuild(state, new Vec3(4, 60, 0)), false)
  assert.equal(underBuild(state, new Vec3(0, 64, 0)), false, '床の層とその上は別の決まり')
  assert.equal(underBuild(state, new Vec3(0, 57, 0)), false, '深さの上限')
  let m
  const bot = { registry: md, entities: {}, entity: { position: new Vec3(0, 70, 0) }, blockAt: () => null, pathfinder: { setMovements: (set) => { m = set } } }
  configureMovements(bot, state)
  assert.ok(m.exclusionBreak({ name: 'dirt', position: new Vec3(0, 62, 0) }) >= 100, '建物の下の土は掘らない')
  assert.equal(m.exclusionBreak({ name: 'dirt', position: new Vec3(8, 62, 0) }), 0, '離れた所は掘ってよい')
  // 出る・帰る行動は、閉じた所から出られなくならないように除く
  state.current = { verb: 'climb_out' }
  assert.equal(m.exclusionBreak({ name: 'dirt', position: new Vec3(0, 62, 0) }), 0)
})

test('建物の中のセルへ着けない失敗には、閉じているかもしれないと、開ける・更地にする手を添える', async () => {
  const { placeOne } = await import('../src/build.mjs')
  const { plan } = site()
  const w = world()
  const bot = fakeBot(w)
  bot.world = { getBlock: w.blockAt }
  bot.pathfinder = { setGoal: () => {}, setMovements: () => {}, goto: async () => { throw new Error('Took to long to decide path to goal!') } }
  bot.clearControlStates = () => {}
  await assert.rejects(placeOne(bot, plan, { x: 2, y: 0, z: 2, block: 'planks' }, signal), /Took to long to decide path to goal!.*inside the build.*build_next \{name, reset: true\}/)
})
