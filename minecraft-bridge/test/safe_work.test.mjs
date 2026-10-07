// 精錬・調理は家の中だけ、地下で腰を据えない（2026-10-04: 地下の洞窟でかまどを置いて 51 秒の精錬を始め、
// 骸骨に撃たれ続けて死んだ。docs/design/86_safe_work_and_branch_mining.md）
import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { Knowledge } from '../src/knowledge.mjs'
import { tendFurnace, findFurnace, knownFurnace, stationSpots, stationRefusal, PRIMITIVES } from '../src/primitives.mjs'
import { ground } from '../src/candidates.mjs'
import { smeltingCounts, furnaceWith, newMemory } from '../src/memory.mjs'

const { Vec3 } = vec3Pkg
const v = (x, y, z) => new Vec3(x, y, z)
const md = minecraftData('1.21.4')
const k = new Knowledge(md)

// 家は x 1..3、z 1..3、床 y 70。家のかまどは (3,70,2)、外のかまどは (12,70,2)
const home = { min: v(1, 70, 1), max: v(3, 70, 3), door: v(2, 70, 4), inside: v(2, 70, 3), outside: v(2, 70, 5), bed: null, breach: [] }
const IN = { name: 'furnace', position: v(3, 70, 2), boundingBox: 'block' }
const OUT = { name: 'furnace', position: v(12, 70, 2), boundingBox: 'block' }

// at: ボットの足元。roof: 頭の上の岩の厚み（8 以上で地下）。furnaces: 世界にあるかまど
function bot ({ at = v(2.5, 70, 2.5), roof = 0, y = 70, furnaces = [IN, OUT], items = {} } = {}) {
  const head = Math.floor(at.y) + 2
  return {
    registry: md,
    entity: { position: at },
    entities: {},
    time: { timeOfDay: 1000, age: 1000 },
    health: 20,
    food: 20,
    heldItem: null,
    blockAt: (p) => {
      const f = furnaces.find((b) => b.position.equals(p))
      if (f) return f
      const solid = (p.y >= head && p.y < head + roof) || p.y < Math.floor(at.y)
      return { name: solid ? 'stone' : 'air', boundingBox: solid ? 'block' : 'empty', position: p }
    },
    findBlock: ({ matching }) => (matching === md.blocksByName.furnace.id ? furnaces[0] ?? null : null),
    findBlocks: ({ matching }) => (matching === md.blocksByName.furnace.id ? furnaces.map((b) => b.position) : []),
    inventory: { items: () => Object.entries(items).map(([name, count]) => ({ name, count })), emptySlotCount: () => 20 }
  }
}
const homeState = () => ({ home, plan: null, unreachableDrops: new Set(), memory: newMemory() })

test('かまどは家の中のものだけを使う。家の外のかまどは、すぐそばにあっても使わない', () => {
  const b = bot({ at: v(12.5, 70, 3.5) }) // 外のかまどのそば
  assert.deepEqual(findFurnace(b, homeState())?.position, IN.position)
  const k1 = knownFurnace(b, homeState())
  assert.deepEqual([k1.near, k1.position.toString()], [true, IN.position.toString()]) // 家のは近くない距離でも見つかる範囲内
  // 家のかまどがなければ、外のかまどがあっても使えるかまどはない
  assert.equal(findFurnace(bot({ furnaces: [OUT] }), homeState()), null)
  assert.equal(knownFurnace(bot({ furnaces: [OUT] }), homeState()), null)
})

test('家のかまどが遠いときは、歩いて行く先として知っている（near: false）', () => {
  const far = bot({ at: v(500.5, 70, 500.5), furnaces: [] })
  far.blockAt = (p) => (p.equals(IN.position) ? IN : { name: 'air', boundingBox: 'empty', position: p })
  const known = knownFurnace(far, homeState())
  assert.deepEqual([known.near, known.position.toString()], [false, IN.position.toString()])
})

test('家がまだないときだけ、地上の近くのかまどを使う。地下では使わない', () => {
  const surface = bot({ at: v(11.5, 70, 3.5), furnaces: [OUT] })
  assert.deepEqual(findFurnace(surface, { home: null })?.position, OUT.position)
  const cave = bot({ at: v(11.5, 40, 3.5), roof: 20, furnaces: [OUT] })
  assert.equal(findFurnace(cave, { home: null }), null)
  assert.equal(knownFurnace(cave, { home: null, lastFurnace: { x: 12, y: 70, z: 2 } }), null)
})

test('かまどを置ける場所は家の中だけ。外にいるときは置く手を出さず、家へ帰る手を出す', () => {
  const state = homeState()
  const outside = bot({ at: v(20.5, 70, 20.5), furnaces: [] })
  assert.deepEqual(stationSpots(outside, state, 'furnace'), [])
  const leaf = { leaves: [{ kind: 'place', item: 'furnace' }], blocked: [] }
  const { candidates } = ground(outside, state, k, { inventory: { furnace: 1 } }, leaf)
  assert.ok(!candidates.some((c) => c.verb === 'place_station'), candidates.map((c) => c.id).join())
  const goHome = candidates.find((c) => c.verb === 'go_home')
  assert.ok(goHome, candidates.map((c) => c.id).join())
  assert.match(goHome.id, /go home to place the furnace inside the house/)
  assert.match(goHome.why, /only inside the house/)
})

test('作業台は外にも置けるが、地下では置かない。かまども地下では置かない（家がないとき）', () => {
  const cave = bot({ at: v(30.5, 40, 30.5), roof: 20, furnaces: [] })
  assert.deepEqual(stationSpots(cave, { home: null, plan: null }, 'crafting_table'), [])
  assert.deepEqual(stationSpots(cave, { home: null, plan: null }, 'furnace'), [])
  const surface = bot({ at: v(30.5, 70, 30.5), furnaces: [] })
  assert.ok(stationSpots(surface, { home: null, plan: null }, 'furnace').length)
  assert.ok(stationSpots(surface, homeState(), 'crafting_table').some((s) => s.where === 'nearby'))
})

test('stationRefusal: かまどは家の外に置かない。作業台は地下に置かない', () => {
  const state = homeState()
  const surface = bot({ furnaces: [] })
  assert.equal(stationRefusal(surface, state, 'furnace', v(2, 70, 2)), '')
  assert.match(stationRefusal(surface, state, 'furnace', v(30, 70, 30)), /only inside the house/)
  assert.match(stationRefusal(surface, state, 'blast_furnace', v(30, 70, 30)), /only inside the house/)
  assert.equal(stationRefusal(surface, state, 'crafting_table', v(30, 70, 30)), '') // 建築予定地への仮設など
  const cave = bot({ at: v(30.5, 40, 30.5), roof: 20, furnaces: [] })
  assert.match(stationRefusal(cave, { home: null }, 'furnace', v(30, 40, 30)), /not placed underground/)
  assert.match(stationRefusal(cave, { home: null }, 'crafting_table', v(30, 40, 30)), /not placed underground/)
  assert.equal(stationRefusal(surface, state, 'torch', v(30, 70, 30)), '')
})

test('stationRefusal: 閉じた地下でツルハシがなくて掘れないときは、作業台を置いてよい（かまどは置かない）。ツルハシがあれば置かない', () => {
  const shaft = (items) => {
    const b = bot({ at: v(30.5, 40, 30.5), roof: 20, furnaces: [], items })
    const open = (p) => p.x === 30 && p.z === 30 && p.y >= 40 && p.y <= 41 // 1 x 1 x 2 の閉じた縦穴。まわりは岩
    b.blockAt = (p) => (open(p) ? { name: 'air', boundingBox: 'empty', position: p } : { name: 'stone', boundingBox: 'block', position: p })
    return b
  }
  assert.equal(stationRefusal(shaft({ cobblestone: 74 }), { home: null }, 'crafting_table', v(30, 41, 30)), '')
  assert.match(stationRefusal(shaft({ cobblestone: 74 }), { home: null }, 'furnace', v(30, 41, 30)), /not placed underground/)
  assert.match(stationRefusal(shaft({ wooden_pickaxe: 1 }), { home: null }, 'crafting_table', v(30, 41, 30)), /not placed underground/)
})

test('smelt の行動は、家の外のかまどを断る（候補や道具が出さなくても、古い記録からの呼び出しを止める）', async () => {
  const state = homeState()
  await assert.rejects(
    PRIMITIVES.smelt(bot(), state, { pos: OUT.position, input: 'raw_iron', count: 5 }, new AbortController().signal),
    /not inside the house/
  )
})

test('家のかまど以外に覚えている「焼いているもの」は、取りに行かず、数えもしない', () => {
  const memory = newMemory()
  memory.furnaces = {
    in: { x: 3, y: 70, z: 2, making: { iron_ingot: 2 }, seen: 0 },
    out: { x: 6, y: 11, z: -261, making: { iron_ingot: 5 }, seen: 0 }
  }
  const inHome = (f) => f.x >= 1 && f.x <= 3
  assert.deepEqual(smeltingCounts(memory, inHome), { iron_ingot: 2 })
  assert.deepEqual(furnaceWith(memory, 'iron_ingot', v(0, 70, 0), inHome)?.x, 3)
  assert.equal(furnaceWith(memory, 'iron_ingot', v(0, 70, 0), () => false), null)
})

test('精錬の待ちは中断できる: 反射やダメージの abort で、焼き終わるまで待たずに止まる', async () => {
  const slots = { input: { name: 'raw_iron', count: 5 }, output: null, fuel: { name: 'coal', count: 1 } }
  const furnace = {
    fuel: 100,
    inputItem: () => slots.input,
    outputItem: () => slots.output,
    fuelItem: () => slots.fuel,
    takeOutput: async () => {},
    takeInput: async () => {},
    close () { closed = true }
  }
  let closed = false
  let ticks = 0
  const controller = new AbortController()
  const b = {
    registry: md,
    time: { age: 5000 },
    openFurnace: async () => furnace,
    // 1 秒（20 tick）待つたびに数える。3 回目の待ちの中で反射が abort する
    waitForTicks: async () => { ticks++; if (ticks === 3) controller.abort(new Error('reflex: skeleton 5.9m away (approaching)')) },
    inventory: { items: () => [] }
  }
  const state = { memory: newMemory(), home }
  await assert.rejects(tendFurnace(b, state, { pos: IN.position }, IN, controller.signal), /reflex: skeleton/)
  assert.equal(ticks, 3, '次の待ちに入らない（5 個で 50 秒続けない）')
  assert.ok(closed, '中断してもかまどは閉じる')
  // 焼いているものは記録に残る（あとで家で取り出す）
  assert.deepEqual(Object.values(state.memory.furnaces).map((f) => f.making.cooked_iron ?? f.making.iron_ingot), [5])
})
