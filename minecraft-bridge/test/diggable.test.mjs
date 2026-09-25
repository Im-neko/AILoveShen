import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { sightings, digTargets } from '../src/world.mjs'

const { Vec3 } = vec3Pkg
const v = (x, y, z) => new Vec3(x, y, z)
const md = minecraftData('1.21.4')

// Stone below y 70, air above; coal ore at the surface (70 - 1) and buried (65)
const ORES = [v(3, 69, 0), v(5, 65, 0)]
const bot = {
  registry: md,
  entity: { position: v(0, 70, 0) },
  entities: {},
  findBlocks: () => ORES,
  blockAt: (p) => {
    if (ORES.some((o) => o.equals(p))) return { name: 'coal_ore', boundingBox: 'block' }
    return p.y < 70 ? { name: 'stone', boundingBox: 'block' } : { name: 'air', boundingBox: 'empty' }
  }
}

test('only ore touching air is remembered (town2: buried coal sent it off exploring)', () => {
  assert.deepEqual(sightings(bot).map((s) => s.pos), [v(3, 69, 0)])
})

test('ore under water is neither offered nor remembered (town2 drowned digging it)', () => {
  const wet = { ...bot, blockAt: (p) => (bot.blockAt(p).name === 'air' ? { name: 'water', boundingBox: 'empty' } : bot.blockAt(p)) }
  assert.deepEqual(sightings(wet), [])
  assert.deepEqual(digTargets(wet, { home: null, plan: null, unreachableBlocks: new Set() }, 'coal_ore'), [])
})

test('a block no path reached is not offered again', () => {
  const state = { home: null, plan: null, unreachableBlocks: new Set() }
  assert.deepEqual(digTargets(bot, state, 'coal_ore'), [v(3, 69, 0)])
  state.unreachableBlocks.add('3,69,0')
  assert.deepEqual(digTargets(bot, state, 'coal_ore'), [])
})
