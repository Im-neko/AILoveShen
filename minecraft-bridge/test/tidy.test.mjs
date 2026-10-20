import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { Knowledge } from '../src/knowledge.mjs'
import { ground } from '../src/candidates.mjs'
import { newMemory, rememberChest } from '../src/memory.mjs'
import { PRIMITIVES, depositInto } from '../src/primitives.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')
const k = new Knowledge(md)
const v = (x, y, z) => new Vec3(x, y, z)

const home = { door: v(2, 70, 4), inside: v(2, 70, 3), outside: v(2, 70, 5), min: v(1, 70, 1), max: v(3, 70, 3), bed: null, breach: [] }

function setup (held, { at = v(2.5, 70, 2.5), chest = true, goal = { predicate: 'at_home' }, free = 20 } = {}) {
  const items = Object.entries(held).map(([name, count]) => ({ name, count }))
  const memory = newMemory()
  if (chest) rememberChest(memory, v(3, 70, 1), {}, 1)
  const bot = {
    entity: { position: at, yaw: 0 },
    entities: {},
    time: { timeOfDay: 1000, age: 100000 },
    health: 20,
    food: 20,
    registry: md,
    heldItem: null,
    inventory: { items: () => items, emptySlotCount: () => free },
    blockAt: (p) => (p.y < 70 ? { name: 'stone', boundingBox: 'block' } : { name: 'air', boundingBox: 'empty' }),
    findBlock: () => null,
    findBlocks: () => []
  }
  const state = { home, formerHomes: [], plan: null, unreachableDrops: new Set(), memory, goal: { spec: goal } }
  const tidy = () => ground(bot, state, k, { dig: () => [], hunt: () => [] }, { leaves: [] }).candidates.find((c) => c.verb === 'tidy')
  const tidyWithWorld = () => ground(bot, state, k, { inventory: {}, blocks: {}, mobs: {}, table: null, unlocked: () => true, dig: () => [], hunt: () => [] }, { leaves: [] }).candidates.find((c) => c.verb === 'tidy')
  return { bot, state, tidy, tidyWithWorld }
}

const HELD = { oak_log: 6, cobblestone: 40, raw_iron: 4, stone_pickaxe: 1, stone_sword: 1, torch: 12, beef: 3, coal: 4, stick: 2, wheat_seeds: 5, bucket: 1, dirt: 20 }

test('家の中で、今使わない物を持っていれば、チェストに戻す（道具・武器・食べ物・松明は持ったまま、種・バケツは目標が使わなければ戻す、土と丸石は少し残す）', () => {
  const { tidy } = setup(HELD)
  const c = tidy()
  assert.ok(c)
  assert.equal(c.id, "put the things you do not need now in the home's chest") // id に数を入れない
  const stow = Object.fromEntries(c.stow.map((s) => [s.item, s.count]))
  assert.deepEqual(stow, { oak_log: 6, cobblestone: 24, raw_iron: 4, dirt: 4, wheat_seeds: 5, bucket: 1 })
})

test('家の外・チェストなし・預ける物がないときは出さない。少しでも預ける物があれば、すぐ出す', () => {
  assert.equal(setup(HELD, { at: v(90.5, 70, 90.5) }).tidy(), undefined, '家のチェストから遠い')
  assert.equal(setup(HELD, { chest: false }).tidy(), undefined)
  assert.equal(setup({ stone_sword: 1, torch: 4, beef: 2 }).tidy(), undefined)
  assert.deepEqual(setup({ oak_log: 3, stone_sword: 1 }).tidy().stow, [{ item: 'oak_log', count: 3 }])
})

test('目標が使う物は戻さない（丸石を集める目標では丸石を、道具の目標ではその材料を持ったまま）', () => {
  const cobble = setup(HELD, { goal: { predicate: 'have', item: 'cobblestone', count: 64, together: [] } }).tidy()
  assert.ok(!cobble.stow.some((s) => s.item === 'cobblestone'))
  // 鉄のつるはしの材料の木（原木・板材・鉄鉱石…）は、目標の品物でなくても残る
  const pick = setup({ ...HELD, oak_planks: 6, andesite: 9 }, { goal: { predicate: 'have', item: 'iron_pickaxe', count: 1, together: [] } }).tidyWithWorld()
  const stowed = pick.stow.map((s) => s.item)
  assert.ok(!stowed.includes('raw_iron') && !stowed.includes('oak_planks'), stowed.join())
  assert.ok(stowed.includes('andesite'))
})

test('夜明かしや帰宅の小目標でも、大目標の残りの中目標が集める物（also）は、その個数まで戻さない', () => {
  const { state, tidy } = setup(HELD)
  state.goal.also = [{ item: 'raw_iron', count: 7, members: ['raw_iron'] }, { item: 'oak_log', count: 2, members: ['oak_log'] }]
  const stow = Object.fromEntries(tidy().stow.map((s) => [s.item, s.count]))
  assert.equal(stow.raw_iron, undefined, '持っている 4 個は 7 個に満たないので全部残る')
  assert.equal(stow.oak_log, 4, '2 個は残して、超えた分だけ戻す')
  assert.equal(stow.cobblestone, 24)
})

test('戻すのは数どおり。入らなかったときは、しばらく出さない', async () => {
  const { bot, state, tidy } = setup(HELD)
  const c = tidy()
  const put = []
  bot.openContainer = async () => ({ deposit: async (id, meta, n) => { put.push([md.items[id].name, n]) }, containerItems: () => [], close () {} })
  bot.pathfinder = { setMovements () {}, goto: async () => {}, stop () {} }
  const result = await PRIMITIVES.tidy(bot, state, { ...c, pos: v(3, 70, 1) }, new AbortController().signal).catch((e) => e)
  // 歩く部分はテストの外: ここでは、チェストに入れる数と失敗の扱いを確かめる
  if (result instanceof Error) {
    assert.equal(state.tidyBlockedUntil, 100000 + 5 * 60 * 20)
    assert.equal(tidy(), undefined)
  } else {
    assert.deepEqual(put, [['oak_log', 6], ['cobblestone', 24], ['raw_iron', 4], ['dirt', 4]])
  }
})

test('松明の目標では、材料の板材はどの種類でも持ったまま（ソルバーが選んだ 1 種類だけ残さない）', () => {
  const { tidyWithWorld } = setup({ ...HELD, oak_planks: 2, spruce_planks: 3, andesite: 9 }, { goal: { predicate: 'have', item: 'torch', count: 16, together: [] } })
  const stowed = tidyWithWorld().stow.map((s) => s.item)
  assert.ok(!stowed.includes('oak_planks') && !stowed.includes('spruce_planks'), stowed.join())
  assert.ok(stowed.includes('andesite'))
})

test('チェストから出した物は、しばらく預け直さない', () => {
  const { tidy, state, bot } = setup({ ...HELD, pumpkin: 9 })
  assert.ok(tidy().stow.some((s) => s.item === 'pumpkin'))
  state.withdrawn = { pumpkin: Number(bot.time.age) - 1000 }
  assert.ok(!tidy().stow.some((s) => s.item === 'pumpkin'))
  state.withdrawn = { pumpkin: Number(bot.time.age) - 6 * 60 * 20 } // 5 分たてば戻してよい
  assert.ok(tidy().stow.some((s) => s.item === 'pumpkin'))
})

test('畑の目標は種・クワ・バケツ・骨粉を持ったまま。別の目標（鉄）では種を戻す（地下へ種 60 個を持って行って全部失った: 2026-10-03）', () => {
  const held = { wheat_seeds: 60, stone_hoe: 1, bucket: 1, bone_meal: 4, raw_iron: 3, stone_pickaxe: 1 }
  const farm = setup(held, { goal: { predicate: 'facility', name: 'wheat_farm_1' } })
  farm.state.facilities = { wheat_farm_1: { kind: 'farm', layout: { crop: 'wheat' } } }
  const kept = farm.tidyWithWorld()
  assert.deepEqual((kept?.stow ?? []).map((s) => s.item), ['raw_iron'])
  const iron = setup(held, { goal: { predicate: 'have', item: 'iron_ingot', count: 3, together: [] } }).tidyWithWorld()
  const stowed = iron.stow.map((s) => s.item)
  assert.ok(stowed.includes('wheat_seeds') && stowed.includes('stone_hoe') && stowed.includes('bone_meal'), stowed.join())
  assert.ok(!stowed.includes('stone_pickaxe'))
})

test('ダイヤ 1 個でも、家の中なら預ける（個数に関わらない）。目標が使うなら持ったまま', () => {
  assert.deepEqual(setup({ diamond: 1, stone_pickaxe: 1 }).tidy().stow, [{ item: 'diamond', count: 1 }])
  const diamonds = setup({ diamond: 1 }, { goal: { predicate: 'have', item: 'diamond', count: 3, together: [] } }).tidy()
  assert.equal(diamonds, undefined)
})

test('家の外では、すぐ戻らない。地下へ出る直前（次の手が鉱山）で、家のチェストが近いときだけ預けに戻る。地下では戻らない', () => {
  const near = { at: v(20.5, 70, 20.5) }
  const down = (s) => ground(s.bot, s.state, k, { dig: () => [], hunt: () => [] }, { leaves: [{ kind: 'make_mine', item: 'raw_iron', bottom_y: 16 }] }).candidates.find((c) => c.verb === 'tidy')
  assert.equal(setup(HELD, near).tidy(), undefined, '拾った直後に戻らない')
  assert.deepEqual(down(setup({ diamond: 1, wheat_seeds: 3, stone_sword: 1 }, near)).stow.map((s) => s.item).sort(), ['diamond', 'wheat_seeds'])
  assert.equal(down(setup(HELD, { at: v(90.5, 70, 90.5) })), undefined, '家のチェストが遠い')
  const under = setup(HELD, near)
  under.bot.blockAt = (p) => (p.y < 70 || p.y === 73 ? { name: 'stone', boundingBox: 'block' } : { name: 'cave_air', boundingBox: 'empty' })
  under.bot.entity.position = v(5.5, 45, 2.5) // チェストまで 25 m: 近さでなく、地下だから戻らない
  assert.equal(down(under), undefined)
})

test('家から遠くても、価値の高い物（ダイヤなど）か、持ち物が多いときは、一度戻って預ける。余裕がなければ戻らない', () => {
  const far = { at: v(300.5, 70, 300.5) }
  assert.equal(setup({ stone_sword: 1, raw_iron: 3 }, far).tidy(), undefined, '価値が高くも多くもない')
  const diamond = setup({ stone_sword: 1, diamond: 1 }, far).tidy()
  assert.deepEqual(diamond.stow, [{ item: 'diamond', count: 1 }])
  assert.match(diamond.purpose, /diamond/)
  // 目標が使うダイヤは持ったまま
  assert.equal(setup({ diamond: 1 }, { ...far, goal: { predicate: 'have', item: 'diamond', count: 3, together: [] } }).tidy(), undefined)
  // 多い: 空きが 6 以下で、預けると 4 スロット以上空く
  const heavy = { oak_log: 128, raw_iron: 64, andesite: 64, granite: 64, stone_sword: 1 }
  assert.ok(setup(heavy, { ...far, free: 4 }).tidy())
  assert.equal(setup(heavy, { ...far, free: 20 }).tidy(), undefined, '空きがあるうちは戻らない')
  assert.equal(setup({ oak_log: 3, stone_sword: 1 }, { ...far, free: 3 }).tidy(), undefined, '預けても空きがほとんど増えない')
  // 戻る道中が危ないときは出さない（体力が危険、満腹度が低い）
  const weak = setup({ diamond: 1 }, far)
  weak.bot.health = 6
  assert.equal(weak.tidy(), undefined)
  const hungry = setup({ diamond: 1 }, far)
  hungry.bot.food = 4
  assert.equal(hungry.tidy(), undefined)
})

test('地下からでも、価値の高い物を持っていれば戻って預ける', () => {
  const under = setup({ stone_pickaxe: 1, diamond: 2 }, { at: v(5.5, 45, 2.5) })
  under.bot.blockAt = (p) => (p.y < 45 || p.y === 48 ? { name: 'stone', boundingBox: 'block' } : { name: 'cave_air', boundingBox: 'empty' })
  assert.ok(under.tidy())
})

// チェストの窓: 入れた物は窓の中身に入り、bot.inventory は窓を閉じるまで変わらない（mineflayer の動き）
function chestWindow (room = Infinity) {
  const slots = []
  return {
    slots,
    deposit: async (id, _meta, n) => {
      const taken = Math.min(n, room - slots.reduce((sum, i) => sum + i.count, 0))
      if (taken > 0) slots.push({ name: md.items[id].name, count: taken })
    },
    containerItems: () => slots
  }
}

test('チェストに入れた数は窓の中身で数える（bot.inventory は窓を閉じるまで変わらない: 2026-10-08 入ったのに「もう持っていない」と失敗した）', async () => {
  const { bot } = setup({ dirt: 38, peony: 3 })
  const win = chestWindow()
  const todo = new Map([['dirt', 38], ['peony', 3]])
  const put = []
  await depositInto(bot, win, todo, put)
  assert.deepEqual(put, ['38 dirt', '3 peony'])
  assert.deepEqual([...todo.values()], [0, 0])
})

test('チェストがいっぱいで一部しか入らなかったら、入った分だけ数えて残りを次のチェストへ', async () => {
  const { bot } = setup({ dirt: 38 })
  const todo = new Map([['dirt', 38]])
  const put = []
  await depositInto(bot, chestWindow(30), todo, put)
  assert.deepEqual(put, ['30 dirt'])
  assert.equal(todo.get('dirt'), 8)
  const none = []
  await depositInto(bot, chestWindow(0), todo, none)
  assert.deepEqual(none, [], '何も入らなければ何も数えない')
})
