import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { BuildPlan, placeOne } from '../src/build.mjs'

const { Vec3 } = vec3Pkg
const v = (x, y, z) => new Vec3(x, y, z)

// 3x3 の敷地、南の壁（z = 2）の真ん中にドア（下と上）
const plan = new BuildPlan({
  blocks: [{ x: 1, y: 0, z: 2, block: 'door' }, { x: 1, y: 1, z: 2, block: 'door' }, { x: 0, y: 0, z: 0, block: 'planks' }],
  width: 3,
  depth: 3,
  height: 2
})
plan.origin = v(0, 70, 0)
const door = (facing) => ({ name: 'spruce_door', boundingBox: 'block', getProperties: () => ({ facing }) })

test('ドアは壁と直角に向いていないと置けたことにしない（town4: 西向きのドアで家に入れなかった）', () => {
  const bot = (facing) => ({ blockAt: (p) => (p.x === 1 && p.z === 2 ? door(facing) : { name: 'air' }) })
  assert.ok(plan.isPlaced(bot('south'), plan.blocks[0]))
  assert.ok(plan.isPlaced(bot('north'), plan.blocks[0]))
  assert.ok(!plan.isPlaced(bot('west'), plan.blocks[0]))
  assert.ok(!plan.isPlaced(bot('east'), plan.blocks[1]))
})

test('ドアは壁の外に立ってから、向きの違うドアを壊し、床を見て置き直す（上のマスからでも下に置く）', async () => {
  const log = []
  const world = new Map([['1,70,2', 'spruce_door']])
  const bot = {
    entity: { position: v(0.5, 70, 20.5) },
    blockAt: (p) => {
      const name = world.get(`${p.x},${p.y},${p.z}`) ?? (p.y < 70 ? 'dirt' : 'air')
      return { name, position: p, boundingBox: name === 'air' ? 'empty' : 'block', getProperties: () => ({ facing: 'west' }) }
    },
    // サーバーは届かない所の掘りを断る（town4c: 30m 先からドアを掘り、手元の世界だけが空気になった）
    dig: async (b) => {
      if (bot.entity.position.distanceTo(b.position.offset(0.5, 0.5, 0.5)) > 4.5) throw new Error(`too far to dig ${b.position}`)
      log.push(`dig ${b.position}`)
      world.delete(`${b.position.x},${b.position.y},${b.position.z}`)
    },
    equip: async () => {},
    lookAt: async (p) => log.push(`look ${p}`),
    placeBlock: async (ref, face) => log.push(`place on ${ref.position} ${face}`),
    inventory: { items: () => [{ name: 'spruce_door', count: 1 }] },
    pathfinder: {
      goto: async (g) => { log.push(`stand ${g.x},${g.y},${g.z}`); bot.entity.position = v(g.x + 0.5, g.y, g.z + 0.5) },
      setGoal: () => {}
    }
  }
  await placeOne(bot, plan, plan.blocks[1], new AbortController().signal)
  assert.deepEqual(log, [
    'stand 1,70,3', // 南の壁の外。遠くからは掘らない
    'dig (1, 70, 2)',
    'look (1.5, 70, 2.5)',
    'place on (1, 69, 2) (0, 1, 0)'
  ])
})

test('家に入る目標は、向きの違うドアを先に置き直す（今のドアを壊して使う）', async () => {
  const { evaluate } = await import('../src/goals.mjs')
  const home = { door: v(1, 70, 2), inside: v(1, 70, 1), outside: v(1, 70, 3), min: v(1, 70, 1), max: v(1, 70, 1), bed: null, breach: [] }
  const bot = {
    entity: { position: v(1.5, 70, 5.5) },
    time: { timeOfDay: 13000 },
    inventory: { items: () => [] },
    blockAt: (p) => (p.x === 1 && p.z === 2 && p.y >= 70 ? door('west') : { name: p.y < 70 ? 'dirt' : 'air', boundingBox: p.y < 70 ? 'block' : 'empty' })
  }
  const state = { home, plan, goal: { spec: { predicate: 'at_home' } } }
  const r = evaluate(bot, state, null, {})
  assert.deepEqual(r.leaves.map((l) => [l.kind, l.block.y]), [['place_plan', 0]])
  assert.match(r.blocked[0], /turned the wrong way/)
})
