import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { PRIMITIVES } from '../src/primitives.mjs'

const { Vec3 } = vec3Pkg

// すぐ掘り終えるボット。ドロップは 3 ブロック下の落ちた所にあり、そこへの経路はない
function digger ({ drop, full = false }) {
  const bot = {
    entity: { position: new Vec3(0, 70, 0), onGround: true },
    entities: drop ? { 7: { id: 7, name: 'item', position: drop } } : {},
    blockAt: (p) => ({ name: 'coal_ore', boundingBox: 'block', position: p }),
    dig: async () => {},
    equip: async () => {},
    waitForTicks: async () => {},
    on: () => {},
    off: () => {},
    inventory: { items: () => [], emptySlotCount: () => (full ? 0 : 30) },
    pathfinder: {
      bestHarvestTool: () => null,
      goto: async (goal) => { if (drop && goal.x === Math.floor(drop.x)) throw new Error('No path to the goal!') },
      setGoal: () => {}
    }
  }
  return bot
}
const state = { unreachableDrops: new Set() }
const c = { block: 'coal_ore', pos: new Vec3(1, 69, 0) }
const controller = new AbortController()

test('拾えなかったドロップは、どこにあるかと、なぜ届かなかったかを説明する', async () => {
  await assert.rejects(PRIMITIVES.dig(digger({ drop: new Vec3(1.5, 67, 0.5) }), state, c, controller.signal),
    /dug coal_ore but picked nothing up \(item 3\.4m away, -3 up; path: No path to the goal!\)/)
  await assert.rejects(PRIMITIVES.dig(digger({ drop: null, full: true }), state, c, controller.signal),
    /\(no item on the ground within 8m; the inventory is full\)/)
})
