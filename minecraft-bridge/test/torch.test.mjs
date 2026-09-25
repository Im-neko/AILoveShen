import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { Knowledge } from '../src/knowledge.mjs'
import { ground } from '../src/candidates.mjs'
import { needs } from '../src/goals.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')
const k = new Knowledge(md)

// The bot stands at 0,70,0 on stone; `roof` is the height of the block over it (null: open sky);
// `torchNear` a light source within reach of its light
function botIn ({ roof = 73, torchNear = false } = {}, items = ['torch'], timeOfDay = 1000) {
  return {
    entity: { position: new Vec3(0.5, 70, 0.5) },
    entities: {},
    time: { timeOfDay },
    health: 20,
    food: 20,
    heldItem: null,
    registry: md,
    inventory: { items: () => items.map((name) => ({ name, count: 4 })) },
    blockAt: (p) => p.y < 70 || p.y === roof
      ? { name: 'stone', boundingBox: 'block' }
      : { name: 'cave_air', boundingBox: 'empty' },
    findBlock: () => (torchNear ? { name: 'wall_torch' } : null)
  }
}

const offered = (bot) => {
  const state = { home: null, plan: null, unreachableDrops: new Set() }
  return ground(bot, state, k, { dig: () => [], hunt: () => [] }, { leaves: [] }).candidates.some((c) => c.verb === 'place_torch')
}

test('in the dark (a cave) a torch is offered, and the danger is stated as a need', () => {
  const bot = botIn()
  assert.ok(offered(bot))
  assert.ok(needs(bot, { home: null }).some((n) => n.startsWith('dark here')))
})

test('no torch where it is lit, under the open sky, or with none held', () => {
  assert.ok(!offered(botIn({ torchNear: true })), 'lit by a torch placed before')
  assert.ok(!offered(botIn({ roof: null }, ['torch'], 18000)), 'open sky at night: the surface is not lit up')
  assert.ok(!offered(botIn({}, [])), 'no torch held')
})
