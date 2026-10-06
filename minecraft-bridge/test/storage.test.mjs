// 家のチェスト: 空きの数え方、まとめ方（デフラグ）、まとめる手
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { storageUsage, defragPlan, defragChests, contentsSignature } from '../src/storage.mjs'
import { newMemory, rememberChest } from '../src/memory.mjs'

const STACK = { cobblestone: 64, dirt: 64, oak_log: 64, ender_pearl: 16, iron_pickaxe: 1 }
const stack = (n) => STACK[n] ?? 64
const bot0 = { registry: { itemsByName: Object.fromEntries(Object.entries(STACK).map(([n, s]) => [n, { id: n.length, stackSize: s }])) } }
const home = { min: { x: 0, y: 70, z: 0 }, max: { x: 4, y: 70, z: 4 } }

test('空き: 開いたときに覚えたスロット数から。開いたことのないチェストは中身から見積もる', () => {
  const memory = newMemory()
  rememberChest(memory, { x: 1, y: 70, z: 1 }, { cobblestone: 130 }, 0, { used: 5, size: 27 }) // 半端が 2 つ
  rememberChest(memory, { x: 2, y: 70, z: 1 }, { dirt: 64, oak_log: 10 }, 0)
  const u = storageUsage(bot0, { home, memory })
  assert.equal(u.chests, 2)
  assert.equal(u.size, 54)
  assert.equal(u.used, 5 + 2) // 開いていない方: dirt 1 スロット + oak_log 1 スロット
  assert.equal(u.free, 54 - 7)
  assert.equal(u.saving, 2) // 丸石 130 は 3 スロット、dirt 1、oak_log 1: 5 スロットに詰まる。使用 7 → 2 空く
})

test('まとめ方: 同じ物は 1 つのチェストに。もともと多く入っているチェストに寄せる。半端なスタックは詰める', () => {
  const chests = [
    { key: 'a', size: 27, slots: [{ name: 'cobblestone', count: 64 }, { name: 'cobblestone', count: 10 }, { name: 'dirt', count: 5 }] },
    { key: 'b', size: 27, slots: [{ name: 'cobblestone', count: 20 }, { name: 'dirt', count: 64 }, { name: 'dirt', count: 30 }] }
  ]
  const { plan, work, saving } = defragPlan(chests, stack)
  assert.deepEqual(plan.cobblestone, [{ key: 'a', count: 94 }]) // a に 74、b に 20 → a に寄せる
  assert.deepEqual(plan.dirt, [{ key: 'b', count: 99 }])
  assert.deepEqual([...work].sort(), ['cobblestone', 'dirt'])
  assert.equal(saving, 6 - 4)
})

test('まとめ済みなら work は空', () => {
  const chests = [
    { key: 'a', size: 27, slots: [{ name: 'cobblestone', count: 64 }, { name: 'cobblestone', count: 30 }] },
    { key: 'b', size: 27, slots: [{ name: 'dirt', count: 64 }] }
  ]
  assert.deepEqual(defragPlan(chests, stack).work, [])
})

test('1 つのチェストに入りきらない物は、空きの多い順に分ける', () => {
  const chests = [
    { key: 'a', size: 2, slots: [{ name: 'cobblestone', count: 64 }, { name: 'cobblestone', count: 64 }] },
    { key: 'b', size: 2, slots: [{ name: 'cobblestone', count: 64 }, { name: 'dirt', count: 1 }] }
  ]
  const { plan } = defragPlan(chests, stack)
  assert.equal(plan.cobblestone.reduce((n, c) => n + c.count, 0), 192)
  assert.ok(plan.cobblestone.length >= 2)
})

// 窓つきの偽チェストと持ち物
function world (layout, inventory = {}, freeSlots = 20) {
  const chests = Object.fromEntries(Object.entries(layout).map(([k, v]) => [k, { size: 27, slots: v.map((s) => ({ ...s })) }]))
  const inv = { ...inventory }
  const idName = (id) => Object.keys(STACK).find((n) => n.length === id)
  const bot = {
    ...bot0,
    inventory: {
      items: () => Object.entries(inv).filter(([, n]) => n > 0).map(([name, count]) => ({ name, count })),
      emptySlotCount: () => freeSlots
    }
  }
  const open = async (pos, use) => {
    const c = chests[`${pos.x},${pos.y},${pos.z}`]
    const win = {
      inventoryStart: c.size,
      containerItems: () => c.slots.map((s) => ({ ...s })),
      withdraw: async (id, _m, count) => {
        const name = idName(id)
        let left = count
        for (const s of c.slots) if (s.name === name && left > 0) { const t = Math.min(s.count, left); s.count -= t; left -= t; inv[name] = (inv[name] ?? 0) + t }
        c.slots = c.slots.filter((s) => s.count > 0)
      },
      deposit: async (id, _m, count) => {
        const name = idName(id)
        let left = Math.min(count, inv[name] ?? 0)
        const room = (c.size - c.slots.length) * stack(name) + c.slots.filter((s) => s.name === name).reduce((n, s) => n + stack(name) - s.count, 0)
        if (room < left) throw new Error('destination full')
        for (const s of c.slots) if (s.name === name && left > 0) { const t = Math.min(stack(name) - s.count, left); s.count += t; left -= t; inv[name] -= t }
        while (left > 0) { const t = Math.min(stack(name), left); c.slots.push({ name, count: t }); left -= t; inv[name] -= t }
      }
    }
    return use(win)
  }
  return { bot, open, chests, inv }
}
const P = (x) => ({ x, y: 70, z: 1 })

test('まとめる手: 散らばった丸石を 1 つのチェストに集め、半端を詰める。持ち物は増減しない', async () => {
  const w = world({
    '1,70,1': [{ name: 'cobblestone', count: 64 }, { name: 'cobblestone', count: 10 }, { name: 'dirt', count: 5 }],
    '2,70,1': [{ name: 'cobblestone', count: 20 }, { name: 'dirt', count: 64 }, { name: 'dirt', count: 30 }]
  }, { cobblestone: 7 })
  const result = await defragChests(w.bot, [P(1), P(2)], w.open)
  const total = (name) => Object.values(w.chests).flatMap((c) => c.slots).filter((s) => s.name === name).reduce((n, s) => n + s.count, 0)
  assert.equal(total('cobblestone'), 94)
  assert.equal(total('dirt'), 99)
  assert.equal(w.inv.cobblestone, 7, '持っていた丸石はそのまま')
  const slotsOf = (key) => w.chests[key].slots.length
  assert.equal(slotsOf('1,70,1') + slotsOf('2,70,1'), 4)
  assert.match(result, /6 → 4 slots/)
})

test('持ち物の空きが足りない物は動かさない（理由を言う）', async () => {
  const w = world({
    '1,70,1': [{ name: 'cobblestone', count: 64 }, { name: 'cobblestone', count: 10 }],
    '2,70,1': [{ name: 'cobblestone', count: 20 }]
  }, {}, 1)
  const result = await defragChests(w.bot, [P(1), P(2)], w.open)
  assert.match(result, /not moved: cobblestone/)
  assert.equal(w.chests['2,70,1'].slots.length, 1)
})

test('署名: 中身が変われば変わる', () => {
  const memory = newMemory()
  rememberChest(memory, { x: 1, y: 70, z: 1 }, { dirt: 5 }, 0)
  const a = contentsSignature({ home, memory })
  rememberChest(memory, { x: 1, y: 70, z: 1 }, { dirt: 6 }, 0)
  assert.notEqual(a, contentsSignature({ home, memory }))
})
