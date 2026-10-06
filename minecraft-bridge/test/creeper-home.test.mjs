import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { ground } from '../src/candidates.mjs'

const { Vec3 } = vec3Pkg
const v = (x, y, z) => new Vec3(x, y, z)

// y 70 の石の上の 5x5 の家（ドアは (2, 70, 4)）。ボットは家の外（南 8 m）にいる
const home = { door: v(2, 70, 4), inside: v(2, 70, 3), outside: v(2, 70, 5), min: v(1, 70, 1), max: v(3, 70, 3), bed: null, breach: [] }
const blockAt = (p) => (p.y < 70 ? { name: 'stone', boundingBox: 'block' } : { name: 'air', boundingBox: 'empty' })

function run (mobs) {
  const entity = { position: v(2.5, 70, 12.5), eyeHeight: 1.62 }
  const entities = { 0: entity }
  mobs.forEach((m, i) => { entities[i + 1] = { id: i + 1, type: 'hostile', height: 1.7, ...m } })
  const bot = {
    entity, entities, time: { timeOfDay: 1000 }, health: 20, food: 20, heldItem: null, blockAt,
    world: { raycast: () => null }, inventory: { items: () => [] }
  }
  const state = { home, plan: null, unreachableDrops: new Set() }
  const status = { leaves: [{ kind: 'go_home' }] }
  return ground(bot, state, null, { dig: () => [], hunt: () => [] }, status)
}

test('クリーパーが 16 m 以内にいる間は、家へ帰る手を出さない（連れて帰ると家を吹き飛ばされる）', () => {
  const { candidates, withheld } = run([{ name: 'creeper', position: v(2.5, 70, 22.5) }])
  assert.ok(!candidates.some((c) => c.verb === 'go_home'))
  assert.match(withheld, /a creeper is within 16 m: it follows you and blows up the walls.*do not lead it to the house/)
})

test('クリーパーが遠いか、ほかのモブなら、家へ帰る手は出る', () => {
  assert.ok(run([{ name: 'creeper', position: v(2.5, 70, 40.5) }]).candidates.some((c) => c.verb === 'go_home'))
  assert.ok(run([{ name: 'zombie', position: v(2.5, 70, 18.5) }]).candidates.some((c) => c.verb === 'go_home'))
})
