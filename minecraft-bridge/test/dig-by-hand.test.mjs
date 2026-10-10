// 見える面に立てる経路がなくても、手が届く距離なら、その場で掘る（2026-10-11）
import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { PRIMITIVES } from '../src/primitives.mjs'

const { Vec3 } = vec3Pkg

function bot ({ reach }) {
  const dug = []
  const b = {
    entity: { position: new Vec3(0, 70, 0), onGround: true },
    entities: {},
    blockAt: (p) => ({ name: 'cobblestone', boundingBox: 'block', position: p }),
    canDigBlock: () => reach,
    dig: async (block) => { dug.push(`${block.position.x},${block.position.y},${block.position.z}`) },
    equip: async () => {},
    waitForTicks: async () => {},
    on: () => {},
    off: () => {},
    inventory: { items: () => [], emptySlotCount: () => 30 },
    pathfinder: { bestHarvestTool: () => null, goto: async () => { throw new Error('No path to the goal!') }, setGoal: () => {} }
  }
  return { b, dug }
}
const state = { unreachableDrops: new Set() }
const c = { block: 'cobblestone', pos: new Vec3(1, 69, 0) }

test('立つ場所への経路がなくても、手が届けば掘る。届かなければ今までどおり失敗し、届かない印を付ける', async () => {
  const near = bot({ reach: true })
  await PRIMITIVES.dig(near.b, state, c, new AbortController().signal).catch(() => {})
  assert.deepEqual(near.dug, ['1,69,0'])
  const far = bot({ reach: false })
  await assert.rejects(PRIMITIVES.dig(far.b, state, c, new AbortController().signal), /No path to the goal/)
  assert.deepEqual(far.dug, [])
})
