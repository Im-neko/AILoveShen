import { test } from 'node:test'
import assert from 'node:assert/strict'
import minecraftData from 'minecraft-data'
import vec3Pkg from 'vec3'
import { configureMovements } from '../src/primitives.mjs'

const { Vec3 } = vec3Pkg

test('a path reaching chunks not loaded yet does not throw (stand-in blocks have no position)', () => {
  let m
  const bot = { registry: minecraftData('1.21.4'), entities: {}, entity: { position: new Vec3(0, 70, 0) }, blockAt: () => null, pathfinder: { setMovements: (set) => { m = set } } }
  const state = { plan: { origin: new Vec3(10, 70, 10), size: { width: 5, depth: 5 } } }
  configureMovements(bot, state)
  const standIn = m.getBlock(new Vec3(500, 70, 500), 0, 0, 0)
  assert.equal(standIn.position, undefined)
  assert.equal(m.exclusionStep(standIn), 0)
})
