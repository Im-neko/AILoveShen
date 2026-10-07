import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { Knowledge } from '../src/knowledge.mjs'
import { makeGoal, evaluate } from '../src/goals.mjs'
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

test('夜の cleared は、武器がなくても目標にできる（危なさは evaluate が見る）。戦いに出る前に預けない物は武器・防具・食べ物・松明', () => {
  const state = { home, plan: null }
  assert.deepEqual(makeGoal({ predicate: 'cleared' }, bot({ dirt: 10 }, 15000), state, k).spec, { predicate: 'cleared' })
  assert.deepEqual(makeGoal({ predicate: 'cleared' }, bot({ stone_sword: 1 }, 15000), state, k).spec, { predicate: 'cleared' })
  for (const keep of ['stone_sword', 'iron_axe', 'leather_helmet', 'shield', 'torch', 'cooked_beef', 'bread']) assert.ok(KEEP_WHEN_FIGHTING.test(keep), keep)
  for (const put of ['cobblestone', 'oak_log', 'raw_iron', 'wheat_seeds', 'beef']) assert.ok(!KEEP_WHEN_FIGHTING.test(put), put)
})

test('つるはし・斧・シャベルが壊れても、観測は落ちない（種類の名前が引けず /observe が 9 分 500 を返した）', () => {
  for (const [item, kind] of [['wooden_pickaxe', 'pickaxe'], ['stone_axe', 'axe'], ['wooden_shovel', 'shovel']]) {
    const me = bot({ oak_planks: 8, stick: 4 })
    const state = { home, plan: null, brokenTools: [{ item, at: Date.now() }] }
    const goal = makeGoal({ predicate: 'have', item: 'dirt', count: 1 }, me, state, k)
    const world = { inventory: { oak_planks: 8, stick: 4 }, blocks: {}, mobs: {}, table: 'near', unlocked: () => true, dig: () => [], hunt: () => [], remembered: new Set(), furnace: null }
    const out = evaluate(me, { ...state, goal }, k, world)
    assert.ok(out.lines.some((l) => l.startsWith(`the ${item} broke`)), `${item}: ${out.lines}`)
  }
})

test('道具の種類の名前は、その種類のどれでも（グループ）として引ける', () => {
  assert.ok(k.resolve('pickaxe').members.includes('stone_pickaxe'))
  assert.ok(k.resolve('axe').members.includes('iron_axe'))
  assert.ok(!k.resolve('axe').members.includes('iron_pickaxe'))
  assert.ok(k.resolve('shovel').members.includes('wooden_shovel'))
})

test('木のつるはしが壊れたとき、丸石と棒と作業台があれば石のつるはしを作る（板がなくても）', () => {
  const inv = { cobblestone: 27, stick: 5, crafting_table: 1 }
  const me = bot(inv)
  const state = { home, plan: null, brokenTools: [{ item: 'wooden_pickaxe', at: Date.now() }] }
  const goal = makeGoal({ predicate: 'have', item: 'dirt', count: 1 }, me, state, k)
  const world = { inventory: inv, blocks: {}, mobs: {}, table: 'near', unlocked: () => true, dig: () => [], hunt: () => [], remembered: new Set(), furnace: null }
  const out = evaluate(me, { ...state, goal }, k, world)
  assert.ok(out.leaves.some((l) => l.kind === 'craft' && l.item === 'stone_pickaxe'), JSON.stringify(out.leaves))
  assert.ok(out.lines.some((l) => /broke: craft another pickaxe/.test(l)), out.lines.join('|'))
})

test('作る材料が手元になければ、家に戻って作るよう行で言う', () => {
  const inv = { dirt: 3 }
  const me = bot(inv)
  const state = { home, plan: null, brokenTools: [{ item: 'wooden_pickaxe', at: Date.now() }] }
  const goal = makeGoal({ predicate: 'have', item: 'dirt', count: 5 }, me, state, k)
  const world = { inventory: inv, blocks: {}, mobs: {}, table: null, unlocked: () => true, dig: () => [], hunt: () => [], remembered: new Set(), furnace: null }
  const out = evaluate(me, { ...state, goal }, k, world)
  assert.ok(out.lines.some((l) => /broke: no materials at hand.*go home to make one/.test(l)), out.lines.join('|'))
})

test('はさみのような格のない道具の作り直しは、名前をそのまま試す', async () => {
  const { replacementTiers } = await import('../src/wear.mjs')
  assert.deepEqual(replacementTiers('shears', 'shears'), ['shears'])
  assert.deepEqual(replacementTiers('wooden_pickaxe', 'pickaxe'), ['diamond_pickaxe', 'iron_pickaxe', 'stone_pickaxe', 'wooden_pickaxe'])
})
