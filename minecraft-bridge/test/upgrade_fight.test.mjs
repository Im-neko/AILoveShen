import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { Knowledge } from '../src/knowledge.mjs'
import { makeGoal } from '../src/goals.mjs'
import { upgradesToTry, KEEP_WHEN_FIGHTING } from '../src/wear.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')
const k = new Knowledge(md)
const bot = (items, timeOfDay = 1000) => ({
  registry: md,
  time: { timeOfDay },
  entity: { position: new Vec3(0, 64, 0) },
  inventory: { items: () => Object.entries(items).map(([name, count]) => ({ name, count })) }
})
const home = { door: new Vec3(1, 64, 3), inside: new Vec3(1, 64, 2), outside: new Vec3(1, 64, 4), min: new Vec3(0, 64, 0), max: new Vec3(2, 64, 2), bed: null, breach: [] }

test('持っている道具より上の格を、上から順に作れるか調べる（木のつるはし → ダイヤ、鉄、石）', () => {
  assert.deepEqual(upgradesToTry(bot({ wooden_pickaxe: 1, stone_axe: 1, iron_sword: 1 })).map((u) => u.item), [
    'diamond_pickaxe', 'iron_pickaxe', 'stone_pickaxe', 'diamond_axe', 'iron_axe', 'diamond_sword'
  ])
  assert.deepEqual(upgradesToTry(bot({ diamond_pickaxe: 1, wooden_hoe: 1 })), [])
})

test('夜の cleared は武器があるときだけ。戦いに出る前に預けない物は武器・防具・食べ物・松明', () => {
  const state = { home, plan: null }
  assert.throws(() => makeGoal({ predicate: 'cleared' }, bot({ dirt: 10 }, 15000), state, k), /without a sword or an axe/)
  assert.deepEqual(makeGoal({ predicate: 'cleared' }, bot({ stone_sword: 1 }, 15000), state, k).spec, { predicate: 'cleared' })
  for (const keep of ['stone_sword', 'iron_axe', 'leather_helmet', 'shield', 'torch', 'cooked_beef', 'bread']) assert.ok(KEEP_WHEN_FIGHTING.test(keep), keep)
  for (const put of ['cobblestone', 'oak_log', 'raw_iron', 'wheat_seeds', 'beef']) assert.ok(!KEEP_WHEN_FIGHTING.test(put), put)
})
