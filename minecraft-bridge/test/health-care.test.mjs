import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { Knowledge } from '../src/knowledge.mjs'
import { ground } from '../src/candidates.mjs'
import { needs } from '../src/goals.mjs'
import { newMemory } from '../src/memory.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')
const k = new Knowledge(md)
const v = (x, y, z) => new Vec3(x, y, z)

const home = { door: v(100, 70, 104), inside: v(100, 70, 103), outside: v(100, 70, 105), min: v(99, 70, 101), max: v(101, 70, 103), bed: null, breach: [] }

function setup ({ health = 4, food = 12, held = { beef: 2, stone_pickaxe: 1 }, at = v(0.5, 70, 0.5), time = 18000 } = {}) {
  const items = Object.entries(held).map(([name, count]) => ({ name, count }))
  const bot = {
    entity: { position: at, yaw: 0 },
    entities: {},
    time: { timeOfDay: time, age: 100000 },
    health, food,
    registry: md,
    heldItem: null,
    isSleeping: false,
    inventory: { items: () => items, emptySlotCount: () => 20 },
    blockAt: (p) => (p.y < 70 ? { name: 'dirt', boundingBox: 'block', position: p } : { name: 'air', boundingBox: 'empty', position: p }),
    findBlock: () => null,
    findBlocks: () => []
  }
  const state = { home, formerHomes: [], plan: null, unreachableDrops: new Set(), memory: newMemory(), goal: null }
  const candidates = () => ground(bot, state, k, { dig: () => [], hunt: () => [] }, { leaves: [] }).candidates
  return { bot, state, candidates }
}

test('体力が危険で満腹度が足りなければ、生肉でも食べる（urgent）。体力が十分で満腹度 16 なら、生肉は食べない', () => {
  const eat = setup().candidates().find((c) => c.verb === 'eat')
  assert.ok(eat && eat.urgent && eat.item === 'beef')
  const fine = setup({ health: 18, food: 16 }).candidates().find((c) => c.verb === 'eat')
  assert.equal(fine, undefined)
})

test('体力が危険で家が遠い夜は、遠い家へ歩かず、その場で穴に入る（urgent）。家が近ければ家へ', () => {
  const far = setup({ held: { stone_pickaxe: 1 } }).candidates()
  const dig = far.find((c) => c.verb === 'dig_in')
  assert.ok(dig && dig.urgent, far.map((c) => c.id).join('; '))
  assert.ok(!far.some((c) => c.verb === 'go_home'))
  const near = setup({ held: { stone_pickaxe: 1 }, at: v(100.5, 70, 110.5) }).candidates()
  assert.ok(near.some((c) => c.verb === 'go_home') && !near.some((c) => c.verb === 'dig_in'))
  // 体力が十分なら、穴は出さない
  assert.ok(!setup({ health: 18, held: { stone_pickaxe: 1 } }).candidates().some((c) => c.verb === 'dig_in' && c.urgent))
})

test('体力が危険だと、自然回復は満腹度 18 以上のときだけと知らせる', () => {
  const st = { home: null, plan: null, formerHomes: [] }
  const bot = (food) => Object.assign(setup({ health: 3, food }).bot, { pathfinder: { isMoving: () => false } })
  assert.match(needs(bot(12), st).find((n) => n.startsWith('health critical')), /it heals only while food is 18 or more \(food 12\)/)
  assert.equal(needs(bot(19), st).find((n) => n.startsWith('health critical')), 'health critical (3/20)')
})

test('水の中にいる間は、陸にいないと知らせる（陸に出たと思い込まない）', () => {
  const st = { home: null, plan: null, formerHomes: [] }
  const bot = Object.assign(setup({ health: 20, food: 20, time: 1000 }).bot, { pathfinder: { isMoving: () => false } })
  assert.ok(!needs(bot, st).some((n) => /swimming/.test(n)))
  bot.entity.isInWater = true
  assert.ok(needs(bot, st).includes('you are swimming in water: you are not on land'))
})
