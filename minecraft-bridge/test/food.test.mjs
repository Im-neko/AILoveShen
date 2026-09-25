import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { Knowledge } from '../src/knowledge.mjs'
import { ground } from '../src/candidates.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')
const k = new Knowledge(md)

function eats (food, items) {
  const bot = {
    entity: { position: new Vec3(0, 70, 0) },
    entities: {},
    time: { timeOfDay: 1000 },
    health: 20,
    food,
    heldItem: null,
    registry: md,
    inventory: { items: () => items.map((name) => ({ name, count: 1 })) }
  }
  const state = { home: null, plan: null, unreachableDrops: new Set() }
  const { candidates } = ground(bot, state, k, { dig: () => [], hunt: () => [] }, { leaves: [] })
  return candidates.filter((c) => c.verb === 'eat').map((c) => c.item)
}

test('rotten flesh is eaten only when starving and nothing better is held', () => {
  assert.deepEqual(eats(3, ['rotten_flesh']), ['rotten_flesh'])
  assert.deepEqual(eats(10, ['rotten_flesh']), [])
  assert.deepEqual(eats(3, ['rotten_flesh', 'bread']), ['bread'])
})
