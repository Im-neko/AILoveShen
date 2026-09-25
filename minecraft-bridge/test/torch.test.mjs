import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { Knowledge } from '../src/knowledge.mjs'
import { ground } from '../src/candidates.mjs'
import { needs } from '../src/goals.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')
const k = new Knowledge(md)

// ボットは石の上の 0,70,0 に立つ。`roof` は頭上のブロックの高さ（null: 空が開けている）、
// `torchNear` は光が届く範囲に光源があるか
function botIn ({ roof = 73, torchNear = false } = {}, items = ['torch'], timeOfDay = 1000) {
  return {
    entity: { position: new Vec3(0.5, 70, 0.5) },
    entities: {},
    time: { timeOfDay },
    health: 20,
    food: 20,
    heldItem: null,
    registry: md,
    inventory: { items: () => items.map((name) => ({ name, count: 4 })) },
    blockAt: (p) => p.y < 70 || p.y === roof
      ? { name: 'stone', boundingBox: 'block' }
      : { name: 'cave_air', boundingBox: 'empty' },
    findBlock: () => (torchNear ? { name: 'wall_torch' } : null)
  }
}

const offered = (bot) => {
  const state = { home: null, plan: null, unreachableDrops: new Set() }
  return ground(bot, state, k, { dig: () => [], hunt: () => [] }, { leaves: [] }).candidates.some((c) => c.verb === 'place_torch')
}

test('暗い所（洞窟）では松明を候補に出し、危険を体の必要として示す', () => {
  const bot = botIn()
  assert.ok(offered(bot))
  assert.ok(needs(bot, { home: null }).some((n) => n.startsWith('dark here')))
})

test('明るい所、空の下、持っていないときは松明を出さない', () => {
  assert.ok(!offered(botIn({ torchNear: true })), '前に置いた松明で明るい')
  assert.ok(!offered(botIn({ roof: null }, ['torch'], 18000)), '夜の空の下: 地上は照らさない')
  assert.ok(!offered(botIn({}, [])), '松明を持っていない')
})
