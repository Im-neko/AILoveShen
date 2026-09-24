import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { exitSpots } from '../src/home.mjs'
import { ground } from '../src/candidates.mjs'

const { Vec3 } = vec3Pkg
const v = (x, y, z) => new Vec3(x, y, z)
const DAY = 1000
const NIGHT = 18000

// A 5x5 house on stone at y 70: walls x 0..4, z 0..4 (planks, 2 high), interior 1..3, door at (2, 70, 4)
function world ({ blocked = [] } = {}) {
  const home = { door: v(2, 70, 4), inside: v(2, 70, 3), outside: v(2, 70, 5), min: v(1, 70, 1), max: v(3, 70, 3), bed: null, breach: [] }
  const blockAt = (p) => {
    const key = `${p.x},${p.y},${p.z}`
    if (p.y < 70) return { name: 'stone', boundingBox: 'block' }
    if (blocked.includes(key)) return { name: 'stone', boundingBox: 'block' }
    if (p.x === 2 && p.z === 4 && p.y <= 71) return { name: 'oak_door', boundingBox: 'block', getProperties: () => ({ open: false }) }
    const wall = (p.x === 0 || p.x === 4 || p.z === 0 || p.z === 4) && p.x >= 0 && p.x <= 4 && p.z >= 0 && p.z <= 4
    if (wall && p.y <= 71 && !(p.x === 2 && p.z === 4)) return { name: 'oak_planks', boundingBox: 'block' }
    return { name: 'air', boundingBox: 'empty' }
  }
  return { home, blockAt }
}

function fakeBot ({ time, at, mobs = [], blockAt }) {
  const entity = { position: at, eyeHeight: 1.62 }
  const entities = { 0: entity }
  mobs.forEach((m, i) => { entities[i + 1] = { id: i + 1, type: 'hostile', height: 1.99, ...m } })
  return {
    entity,
    entities,
    time: { timeOfDay: time },
    health: 20,
    food: 20,
    heldItem: null,
    blockAt,
    world: { raycast: () => ({ name: 'oak_planks' }) }, // the walls hide everything outside
    inventory: { items: () => [] }
  }
}

const skeletonAtDoor = { name: 'skeleton', position: v(2.5, 70, 6.5) }
const digOutside = { leaves: [{ kind: 'dig', sources: ['oak_log'] }] }
const snap = { dig: () => [v(10, 70, 10)], hunt: () => [] }

function run ({ time = DAY, mobs = [skeletonAtDoor], status = digOutside, blocked } = {}) {
  const { home, blockAt } = world({ blocked })
  const bot = fakeBot({ time, at: v(2.5, 70, 2.5), mobs, blockAt })
  const state = { home, plan: null, unreachableDrops: new Set() }
  return ground(bot, state, null, snap, status)
}

test('exits are offered on each free side, farthest from the hostiles first', () => {
  const { home, blockAt } = world()
  const spots = exitSpots({ blockAt }, home, [skeletonAtDoor])
  assert.deepEqual(spots.map((s) => s.side), ['north', 'west', 'east', 'south'])
  assert.deepEqual(spots[0].wall, v(1, 70, 0)) // the corner-side cell is a little farther
  assert.deepEqual(spots[0].step, v(1, 70, -1))
  // Never the door itself
  assert.ok(spots.every((s) => !(s.wall.x === 2 && s.wall.z === 4)))
})

test('no exit where there is no room to stand outside', () => {
  const blocked = [1, 2, 3].map((x) => `${x},70,-1`)
  const { home, blockAt } = world({ blocked })
  const spots = exitSpots({ blockAt }, home, [skeletonAtDoor])
  assert.ok(!spots.some((s) => s.side === 'north'))
})

test('by day with a hostile at the door, outside work is held back and exits are offered', () => {
  const { candidates, withheld } = run()
  const verbs = candidates.map((c) => c.verb)
  assert.ok(!verbs.includes('dig'))
  assert.ok(verbs.includes('exit_wall'))
  assert.ok(verbs.includes('wait'))
  assert.match(withheld, /skeleton wait near the door: 1 actions outside are held back/)
})

test('the cleared goal may go out to fight what waits at the door, unarmed too', () => {
  const mob = { ...skeletonAtDoor, id: 1 }
  const { candidates } = run({ status: { leaves: [{ kind: 'clear', mobs: [mob] }] } })
  const attack = candidates.find((c) => c.verb === 'attack')
  assert.ok(attack)
  assert.equal(attack.weapon, 'none (fist)')
})

test('at night nothing outside and no exit, only waiting', () => {
  const { candidates, withheld } = run({ time: NIGHT, mobs: [] })
  assert.deepEqual(candidates.map((c) => c.verb), ['wait'])
  assert.match(withheld, /staying inside for the night/)
})

test('nothing is held back when nothing outside is wanted', () => {
  const { withheld } = run({ status: { leaves: [] } })
  assert.equal(withheld, null)
})
