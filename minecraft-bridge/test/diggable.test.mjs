import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { sightings, digTargets } from '../src/world.mjs'

const { Vec3 } = vec3Pkg
const v = (x, y, z) => new Vec3(x, y, z)
const md = minecraftData('1.21.4')

// y 70 より下は石、上は空気。石炭鉱石は地表（70 - 1）と地中（65）
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

test('空気に触れている鉱石だけを覚える（town2: 地中の石炭を探して探索に出ていった）', () => {
  assert.deepEqual(sightings(bot).map((s) => s.pos), [v(3, 69, 0)])
})

test('水の下の鉱石は候補に出さず、覚えもしない（town2 では掘りに行って溺れた）', () => {
  const wet = { ...bot, blockAt: (p) => (bot.blockAt(p).name === 'air' ? { name: 'water', boundingBox: 'empty' } : bot.blockAt(p)) }
  assert.deepEqual(sightings(wet), [])
  assert.deepEqual(digTargets(wet, { home: null, plan: null, unreachableBlocks: new Set() }, 'coal_ore'), [])
})

test('経路が届かなかったブロックは、もう候補に出さない', () => {
  const state = { home: null, plan: null, unreachableBlocks: new Set() }
  assert.deepEqual(digTargets(bot, state, 'coal_ore'), [v(3, 69, 0)])
  state.unreachableBlocks.add('3,69,0')
  assert.deepEqual(digTargets(bot, state, 'coal_ore'), [])
})
