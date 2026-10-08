// 置く先がふさがっているとき、隣の置ける所を教える。家の中のセルに置くときは、先に家に入る（2026-10-08）
import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { createTools } from '../src/tools.mjs'
import { PRIMITIVES } from '../src/primitives.mjs'
import { Knowledge } from '../src/knowledge.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')
const k = new Knowledge(md)

// 3x3 の室内（x 1..3、z 1..3、y 70）、ドアは (2, 70, 4)。ベッドへの列は x = 2。チェストは (1,70,1)
const home = { door: new Vec3(2, 70, 4), inside: new Vec3(2, 70, 3), outside: new Vec3(2, 70, 5), min: new Vec3(1, 70, 1), max: new Vec3(3, 70, 3), bed: null, breach: [] }

function botAt (position, blocks = {}) {
  const at = (p) => blocks[`${p.x},${p.y},${p.z}`] ?? (p.y < 70 ? 'stone' : 'air')
  return {
    registry: md,
    entity: { position, onGround: true, yaw: 0 },
    entities: {},
    time: { timeOfDay: 1000, age: 100 },
    health: 20,
    food: 20,
    heldItem: null,
    inventory: { items: () => [{ name: 'chest', count: 1, type: md.itemsByName.chest.id }], emptySlotCount: () => 30 },
    blockAt: (p) => { const name = at(p); return { name, boundingBox: name === 'air' ? 'empty' : 'block', shapes: name === 'air' ? [] : [[0, 0, 0, 1, 1, 1]], position: new Vec3(Math.floor(p.x), Math.floor(p.y), Math.floor(p.z)) } },
    get world () { return { getBlock: this.blockAt } },
    pathfinder: { goto: async () => { throw new Error('No path to the goal!') }, setGoal: () => {} },
    on: () => {},
    off: () => {},
    waitForTicks: async () => {}
  }
}

const toolsOf = (bot, state) => createTools({ bot, state, knowledge: k, run: async (c) => ({ ok: true, result: c.verb, seconds: 0 }), candidates: () => [], check: async () => [] })

test('既存のチェストの位置に置こうとして断られたら、隣の置ける所を教える', async () => {
  const bot = botAt(new Vec3(2.5, 70, 3.5), { '1,70,1': 'chest' })
  const callTool = toolsOf(bot, { home, plan: null, memory: {}, busy: false, reflex: false })
  const r = await callTool('place', { item: 'chest', x: 1, y: 70, z: 1, intent: 'x' })
  assert.equal(r.ok, false)
  assert.match(r.result, /1,70,1 is taken by chest\. Free spots next to it: /)
  const spots = r.result.split('Free spots next to it: ')[1].split(' (place there')[0].split('; ')
  assert.ok(spots.length >= 1 && spots.length <= 4)
  // 教える所はどれも空いていて、そのまま置ける（もう一度呼べば通る）
  for (const s of spots) {
    const [x, y, z] = s.split(',').map(Number)
    assert.equal(bot.blockAt(new Vec3(x, y, z)).name, 'air', s)
    assert.equal((await callTool('place', { item: 'chest', x, y, z, intent: 'x' })).ok, true, s)
  }
  // 近い順: 1 マス隣が先
  const first = spots[0].split(',').map(Number)
  assert.ok(Math.abs(first[0] - 1) + Math.abs(first[2] - 1) <= 2, spots[0])
})

test('隣の候補は、家の中の置いてよい所だけ（ドアからベッドへの列と、立っている所は出さない）', async () => {
  const bot = botAt(new Vec3(3.5, 70, 3.5), { '1,70,1': 'chest' })
  const callTool = toolsOf(bot, { home, plan: null, memory: {}, busy: false, reflex: false })
  const r = await callTool('place', { item: 'chest', x: 1, y: 70, z: 1, intent: 'x' })
  const spots = r.result.split('Free spots next to it: ')[1].split(' (place there')[0].split('; ')
  assert.ok(!spots.includes('3,70,3'), '自分の足元は出さない')
  assert.ok(!spots.includes('2,70,3'), '内側のドアの前は出さない')
})

test('隣の候補から、自分の足元と、面を借りる隣がない所（下が穴）は外す', async () => {
  const bot = botAt(new Vec3(2.5, 70, 1.5), { '1,70,1': 'chest', '2,69,2': 'air' })
  const callTool = toolsOf(bot, { home, plan: null, memory: {}, busy: false, reflex: false })
  const r = await callTool('place', { item: 'chest', x: 1, y: 70, z: 1, intent: 'x' })
  const spots = r.result.split('Free spots next to it: ')[1].split(' (place there')[0].split('; ')
  assert.ok(!spots.includes('2,70,1'), '足元')
  assert.ok(!spots.includes('2,70,2'), '下が穴で、置く面がない')
  assert.ok(spots.length >= 1)
})

test('place_at: 家の中のセルに置くなら、外からでも先に家に入る（入れなければ家へ入れないことを言う）', async () => {
  const c = { item: 'chest', pos: new Vec3(1, 70, 2), against: new Vec3(1, 69, 2) }
  const signal = new AbortController().signal
  const outside = botAt(new Vec3(20.5, 70, 20.5))
  await assert.rejects(PRIMITIVES.place_at(outside, { home }, c, signal), /home's door/)
  // 家の外のセルなら、家には入らない
  const far = { item: 'chest', pos: new Vec3(20, 70, 22), against: new Vec3(20, 69, 22) }
  await assert.rejects(PRIMITIVES.place_at(botAt(new Vec3(20.5, 70, 20.5)), { home }, far, signal), /No path to the goal/)
})
