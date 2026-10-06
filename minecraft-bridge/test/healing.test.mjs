import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { Knowledge } from '../src/knowledge.mjs'
import { ground } from '../src/candidates.mjs'
import { needs } from '../src/goals.mjs'
import { cannotHeal, valuablesCarried } from '../src/healing.mjs'
import { newMemory, rememberChest } from '../src/memory.mjs'
import { PRIMITIVES } from '../src/primitives.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')
const k = new Knowledge(md)
const v = (x, y, z) => new Vec3(x, y, z)
const home = { door: v(2, 70, 4), inside: v(2, 70, 3), outside: v(2, 70, 5), min: v(1, 70, 1), max: v(3, 70, 3), bed: null, breach: [] }

function setup (held, { health = 1.5, food = 15, at = v(2.5, 70, 2.5), chest = true, goal = { predicate: 'have', item: 'oak_log', count: 4, together: [] } } = {}) {
  const items = Object.entries(held).map(([name, count]) => ({ name, count }))
  const memory = newMemory()
  if (chest) rememberChest(memory, v(3, 70, 1), {}, 1)
  const bot = {
    entity: { position: at, yaw: 0 },
    entities: {},
    time: { timeOfDay: 1000, age: 100000 },
    health,
    food,
    registry: md,
    heldItem: null,
    inventory: { items: () => items, emptySlotCount: () => 20 },
    blockAt: (p) => (p.y < 70 ? { name: 'stone', boundingBox: 'block' } : { name: 'air', boundingBox: 'empty' }),
    findBlock: () => null,
    findBlocks: () => []
  }
  const state = { home, formerHomes: [], plan: null, unreachableDrops: new Set(), memory, goal: { spec: goal } }
  const stash = () => ground(bot, state, k, { inventory: {}, blocks: {}, mobs: {}, table: null, unlocked: () => true, dig: () => [], hunt: () => [] }, { leaves: [] })
    .candidates.find((c) => c.verb === 'stash')
  return { bot, state, stash }
}

const HELD = { pumpkin: 5, wheat: 2, wheat_seeds: 7, stick: 3, oak_sapling: 1, wooden_pickaxe: 1, wooden_sword: 1, torch: 2 }

test('体力が危険で、満腹度が足りず、食べ物がなければ、回復の手段がない', () => {
  assert.equal(cannotHeal(setup(HELD).bot), true)
  assert.equal(cannotHeal(setup(HELD, { food: 18 }).bot), false)
  assert.equal(cannotHeal(setup(HELD, { health: 12 }).bot), false)
  assert.equal(cannotHeal(setup({ ...HELD, bread: 1 }).bot), false)
  // 腐った肉は食べ物に数えない
  assert.equal(cannotHeal(setup({ ...HELD, rotten_flesh: 3 }).bot), true)
})

test('回復の手段がなく家にいるなら、武器・松明と目標が使う物のほかを、チェストへ預ける（urgent）', () => {
  const c = setup(HELD).stash()
  assert.ok(c)
  assert.equal(c.urgent, true)
  assert.equal(c.verb, 'stash')
  const items = Object.fromEntries(c.items.map((s) => [s.item, s.count]))
  // 道具・種・棒も預ける。剣と松明は持ったまま
  assert.deepEqual(items, { pumpkin: 5, wheat: 2, wheat_seeds: 7, stick: 3, oak_sapling: 1, wooden_pickaxe: 1 })
})

test('目標が使う物は預けない', () => {
  const c = setup(HELD, { goal: { predicate: 'have', item: 'wooden_pickaxe', count: 1, together: [] } }).stash()
  const items = c.items.map((s) => s.item)
  assert.ok(!items.includes('wooden_pickaxe') && !items.includes('stick'))
})

test('出さないとき: 回復できる、家の外、チェストがない、預ける物がない、預けられなかった直後', () => {
  assert.equal(setup(HELD, { food: 19 }).stash(), undefined)
  assert.equal(setup(HELD, { at: v(20.5, 70, 20.5) }).stash(), undefined)
  assert.equal(setup(HELD, { chest: false }).stash(), undefined)
  assert.equal(setup({ wooden_sword: 1, torch: 2 }).stash(), undefined)
  const blocked = setup(HELD)
  blocked.state.tidyBlockedUntil = 200000
  assert.equal(blocked.stash(), undefined)
})

test('needs は、回復の手段がないことと、死んで失う物の数を数だけで書く', () => {
  const { bot, state } = setup(HELD)
  const line = needs(bot, state).find((n) => n.startsWith('health critical'))
  assert.match(line, /food is 18 or more \(food 15\); you carry no food/)
  assert.match(line, /19 items besides weapons, armor and torches/)
  assert.equal(valuablesCarried(bot), 19)
  const after = setup({ wooden_sword: 1, torch: 2 })
  assert.match(needs(after.bot, after.state).find((n) => n.startsWith('health critical')), /nothing besides weapons, armor and torches/)
  // 食べ物を持っていれば、言わない
  const fed = setup({ ...HELD, bread: 1 })
  assert.doesNotMatch(needs(fed.bot, fed.state).find((n) => n.startsWith('health critical')), /you carry no food/)
})

test('預け入れの primitive が預けられないと、しばらく出さない', async () => {
  const { bot, state } = setup(HELD)
  bot.openContainer = async () => { throw new Error('chest is full') }
  await assert.rejects(PRIMITIVES.stash(bot, state, { pos: v(3, 70, 1), items: [{ item: 'pumpkin', count: 5 }] }, new AbortController().signal))
  assert.ok(state.tidyBlockedUntil > Number(bot.time.age))
})

test('チェストから出して 5 分以内の物は、すぐ預け直さない（石のクワの材料を出しては戻す輪になった: 2026-10-03）', () => {
  const held = { ...HELD, cobblestone: 2 }
  const { bot, state, stash } = setup(held)
  state.withdrawn = { cobblestone: Number(bot.time.age) - 20, stick: Number(bot.time.age) - 20 }
  const items = stash().items.map((s) => s.item)
  assert.ok(!items.includes('cobblestone') && !items.includes('stick'))
  assert.ok(items.includes('pumpkin'))
  // 5 分たてば、また預ける
  state.withdrawn = { cobblestone: Number(bot.time.age) - 5 * 60 * 20 - 1 }
  assert.ok(stash().items.map((s) => s.item).includes('cobblestone'))
})
