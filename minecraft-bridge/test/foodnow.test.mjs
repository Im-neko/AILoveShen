import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { Knowledge } from '../src/knowledge.mjs'
import { foodNow } from '../src/foodnow.mjs'
import { newMemory, rememberChest } from '../src/memory.mjs'

const { Vec3 } = vec3Pkg
const v = (x, y, z) => new Vec3(x, y, z)
const md = minecraftData('1.21.4')
const k = new Knowledge(md)
const home = { door: v(2, 70, 4), inside: v(2, 70, 3), outside: v(2, 70, 5), min: v(1, 70, 1), max: v(3, 70, 3), bed: null, breach: [] }

function setup ({ items = {}, chest = null, animals = [], food = 10, health = 12 } = {}) {
  const memory = newMemory()
  if (chest) rememberChest(memory, v(3, 70, 1), chest, 1)
  const entities = {}
  animals.forEach((a, i) => { entities[i + 1] = { id: i + 1, name: a.name, type: 'animal', position: a.at } })
  const bot = {
    registry: md,
    entity: { position: v(2, 70, 6) },
    entities,
    time: { timeOfDay: 1000, age: 100000 },
    health,
    food,
    heldItem: null,
    blockAt: () => null,
    findBlock: () => null,
    findBlocks: () => [],
    inventory: { items: () => Object.entries(items).map(([name, count]) => ({ name, count })), emptySlotCount: () => 20 }
  }
  return { bot, state: { home, memory, plan: null, facilities: {} } }
}

test('食べ物の今: 足りない満腹度、持ち物と家のチェストの満腹度、見えている動物とそれが落とす食べ物', () => {
  const { bot, state } = setup({
    items: { bread: 2, potato: 3, cobblestone: 9 },
    chest: { cooked_beef: 4, beef: 2, rotten_flesh: 5 },
    animals: [{ name: 'cow', at: v(12, 70, 6) }, { name: 'cow', at: v(30, 70, 6) }, { name: 'zombie', at: v(5, 70, 6) }],
    food: 10, health: 12
  })
  const now = foodNow(bot, state, k)
  assert.deepEqual(
    [now.status.points_to_full, now.status.points_to_heal, now.status.furnace_known],
    [10, 8, false]
  )
  assert.equal(now.carried.points, 2 * 5 + 3 * 1)
  assert.deepEqual(now.carried.items.map((i) => i.name), ['bread', 'potato'])
  // チェストは、腐った肉を数えない。生肉は、かまどが分からないので生の値（2 × 3）
  assert.equal(now.home_chest.points, 4 * 8 + 2 * 3)
  assert.equal(now.home_chest.items.find((i) => i.name === 'beef').cooked_points_each, 8)
  assert.ok(!now.home_chest.items.some((i) => i.name === 'rotten_flesh'))
  // 牛は 2 頭（敵は数えない）。最も近いのは 10 m。落とす食べ物は生と焼いた値
  assert.equal(now.in_sight.length, 1)
  assert.deepEqual([now.in_sight[0].mob, now.in_sight[0].count, now.in_sight[0].nearest_m], ['cow', 2, 10])
  assert.ok(now.in_sight[0].gives.some((g) => g.startsWith('beef 3 (cooked 8)')))
})

test('食べ物の今: 家にチェストがなければ home_chest は出さない。何もなければ空', () => {
  const { bot, state } = setup({ food: 20, health: 20 })
  const now = foodNow(bot, state, k)
  assert.equal(now.home_chest, undefined)
  assert.deepEqual([now.carried.points, now.in_sight, now.ripe_crops, now.remembered_animals], [0, [], [], []])
  assert.deepEqual([now.status.points_to_full, now.status.points_to_heal], [0, 0])
})
