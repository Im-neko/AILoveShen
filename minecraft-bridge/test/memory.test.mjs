import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { newMemory, remember, recall, rememberDeath, summarizeMemory, visited, MAX_PLACES_PER_KIND, ANIMAL_STALE_TICKS } from '../src/memory.mjs'
import { bearing } from '../src/observe.mjs'
import { ground } from '../src/candidates.mjs'

const { Vec3 } = vec3Pkg
const at = (x, z) => ({ x, y: 70, z })
const sheep = (x, z) => ({ kind: 'sheep', pos: at(x, z) })

test('sightings are kept per 16x16 region with a count and when', () => {
  const m = newMemory()
  remember(m, [sheep(100, 100), sheep(101, 102), sheep(140, 100)], at(0, 0), 500)
  assert.deepEqual(m.places.sheep.map((p) => [p.region, p.count, p.seen]), [['6,6', 2, 500], ['8,6', 1, 500]])
  assert.ok(visited(m, at(3, 3)))
  assert.ok(!visited(m, at(40, 3)))
})

test('a place remembered nearby and not seen now is forgotten; far ones stay', () => {
  const m = newMemory()
  remember(m, [sheep(10, 10), sheep(200, 200)], at(0, 0), 0)
  remember(m, [], at(12, 12), 100) // came back: nothing there
  assert.deepEqual(m.places.sheep.map((p) => p.region), ['12,12'])
})

test('each kind keeps the most recent places only', () => {
  const m = newMemory()
  for (let i = 0; i < MAX_PLACES_PER_KIND + 3; i++) remember(m, [sheep(1000 + i * 32, 0)], at(1000 + i * 32, 0), i)
  assert.equal(m.places.sheep.length, MAX_PLACES_PER_KIND)
  assert.equal(m.places.sheep[0].seen, MAX_PLACES_PER_KIND + 2)
})

test('recall: out of view, fresh, nearest first; animals go stale after a day, logs do not', () => {
  const m = newMemory()
  remember(m, [sheep(100, 0), sheep(300, 0), { kind: 'oak_log', pos: at(0, 200) }, sheep(10, 0)], at(500, 500), 0)
  const me = at(0, 0)
  assert.deepEqual(recall(m, ['sheep'], me, 1200).map((p) => [p.x, p.distance, p.minutesAgo]), [[100, 100, 1], [300, 300, 1]])
  assert.deepEqual(recall(m, ['sheep'], me, ANIMAL_STALE_TICKS + 1), [])
  assert.equal(recall(m, ['oak_log'], me, ANIMAL_STALE_TICKS * 5).length, 1)
})

test('the summary shows the nearest place of each kind and deaths', () => {
  const m = newMemory()
  remember(m, [sheep(0, -100), sheep(0, -300), { kind: 'iron_ore', pos: at(50, 0) }], at(1000, 1000), 0)
  rememberDeath(m, at(0, 40), 0)
  const s = summarizeMemory(m, new Vec3(0, 70, 0), 2400, bearing)
  assert.deepEqual(s.places, [
    { kind: 'iron_ore', count: 1, direction: 'E', distance_m: 50, minutes_ago: 2 },
    { kind: 'sheep', count: 1, direction: 'N', distance_m: 100, minutes_ago: 2 }
  ])
  assert.deepEqual(s.deaths, [{ direction: 'S', distance_m: 40, minutes_ago: 2 }])
})

test('searching offers the places remembered first, then directions not covered yet', () => {
  const memory = newMemory()
  remember(memory, [sheep(0, -100)], at(0, 0), 0) // the start region is visited
  remember(memory, [], at(24, 0), 0) // so is the one east
  const bot = {
    entity: { position: new Vec3(0, 70, 0) },
    entities: {},
    time: { timeOfDay: 1000, age: 1200 },
    health: 20,
    food: 20,
    heldItem: null,
    inventory: { items: () => [] }
  }
  const state = { home: null, plan: null, unreachableDrops: new Set(), memory }
  const leaf = { kind: 'explore', item: 'wool', sources: ['sheep'] }
  const { candidates } = ground(bot, state, null, { dig: () => [], hunt: () => [] }, { leaves: [leaf] })
  assert.equal(candidates[0].id, 'go to sheep seen at 0,-100')
  assert.equal(candidates[0].verb, 'goto_memory')
  const east = candidates.find((c) => c.id === 'explore east')
  assert.equal(east.been_there, true)
  assert.equal(candidates.at(-1).id, 'explore east')
})
