// 家のチェストの空きと、デフラグ（散らばった中身をまとめる）。2026-10-04, user: 「チェストいっぱいに
// なってきたらチェストを増やしてほしい」「チェストが複数になったらデフラグできるようにしたい」。
//
// 空きは、チェストを開いたときに覚えた slots（使っている数と、チェストのスロット数）から。開いたことのない
// チェストは、中身の数から見積もる。

import vec3Pkg from 'vec3'
import { homeChests, rememberChest, forgetChest } from './memory.mjs'
import { chestSpot } from './home.mjs'

const { Vec3 } = vec3Pkg

export const SINGLE_CHEST = 27
export const LARGE_CHEST = 54
const stackOf = (bot, name) => bot.registry.itemsByName[name]?.stackSize ?? 64

// 記録するチェストの位置: ラージチェストなら左の半分（もう片方の位置の記録は消す）、そうでなければそのチェスト
export function chestRecordPos (bot, block) {
  const type = block.getProperties?.().type
  if (type !== 'left' && type !== 'right') return { pos: block.position, same: true }
  const facing = block.getProperties().facing
  const other = [[1, 0], [-1, 0], [0, 1], [0, -1]].map(([dx, dz]) => bot.blockAt(block.position.offset(dx, 0, dz)))
    .find((b) => b?.name === 'chest' && b.getProperties?.().facing === facing && b.getProperties().type !== type)
  if (!other) return { pos: block.position, same: true }
  const left = type === 'left' ? block : other
  return { pos: left.position, same: left === block }
}

// 家にあるチェストを、記録と合わせる（設計書 121）: 置いたのに記録されないチェストは、開かれることがなく、空きとして
// 数えられない（`place` で置いた 4 つのチェストが使われず、満杯の 1 つに預け続けて「また増設」になった: 2026-10-08）。
// ない記録は消し、あるブロックで記録にないものは、中身を空として覚える（開いたとき本当の中身に直る）。
// 家の中が読み込まれていないマスは触らない
export function syncHomeChests (bot, state) {
  const { home, memory } = state
  if (!home || !memory) return
  const now = Number(bot.time?.age ?? 0)
  for (let x = home.min.x - 1; x <= home.max.x + 1; x++) {
    for (let z = home.min.z - 1; z <= home.max.z + 1; z++) {
      for (let y = home.min.y - 1; y <= home.min.y + 3; y++) {
        const block = bot.blockAt(new Vec3(x, y, z))
        if (!block) continue
        const key = `${x},${y},${z}`
        if (block.name !== 'chest') {
          if (memory.chests[key]) forgetChest(memory, { x, y, z })
          continue
        }
        const at = chestRecordPos(bot, block)
        if (!at.same) { forgetChest(memory, { x, y, z }); continue }
        const type = block.getProperties?.().type
        const size = type === 'left' || type === 'right' ? LARGE_CHEST : SINGLE_CHEST
        const known = memory.chests[key]
        if (!known) rememberChest(memory, at.pos, {}, now, { used: 0, size })
        else if (known.slots && known.slots.size !== size) known.slots.size = size
      }
    }
  }
}

// 預け先の順: 空きのあるチェストが先（満杯の記録のチェストを毎回先に開けない）、その中でボットに近い順
export function roomyChests (bot, state) {
  if (!state.home || !state.memory) return []
  const free = new Map(chestRecords(bot, state).map((r) => [r.key, r.size - r.used]))
  const me = bot.entity.position
  const away = (c) => Math.hypot(c.x + 0.5 - me.x, c.y - me.y, c.z + 0.5 - me.z)
  return homeChests(state.memory, state.home)
    .sort((a, b) => ((free.get(`${a.x},${a.y},${a.z}`) ?? 0) <= 0) - ((free.get(`${b.x},${b.y},${b.z}`) ?? 0) <= 0) || away(a) - away(b))
}

// 記録からのチェストごとの使用状況
export function chestRecords (bot, state) {
  if (!state.home || !state.memory) return []
  return homeChests(state.memory, state.home).map((c) => {
    const est = Object.entries(c.contents ?? {}).reduce((n, [name, k]) => n + Math.ceil(k / stackOf(bot, name)), 0)
    return { key: `${c.x},${c.y},${c.z}`, pos: { x: c.x, y: c.y, z: c.z }, size: c.slots?.size ?? SINGLE_CHEST, used: c.slots?.used ?? est, contents: c.contents ?? {} }
  })
}

// 家にもう 1 つチェストを置ける場所があるか。家の中が読み込まれていなければ null（ないとは言わない）
export function chestSpotFact (bot, home) {
  if (!home || !bot.blockAt(home.inside)) return null
  return !!chestSpot(bot, home)
}

// { chests, size, used, free, saving }: saving = まとめると空くスロットの数
export function storageUsage (bot, state) {
  const recs = chestRecords(bot, state)
  const size = recs.reduce((n, r) => n + r.size, 0)
  const used = recs.reduce((n, r) => n + r.used, 0)
  const totals = {}
  for (const r of recs) for (const [name, k] of Object.entries(r.contents)) totals[name] = (totals[name] ?? 0) + k
  const packed = Object.entries(totals).reduce((n, [name, k]) => n + Math.ceil(k / stackOf(bot, name)), 0)
  // 同じ物が複数のチェストにあるか
  const spread = Object.keys(totals).some((name) => recs.filter((r) => (r.contents[name] ?? 0) > 0).length > 1)
  return { chests: recs.length, size, used, free: size - used, saving: Math.max(0, used - packed), spread }
}

// 中身（スロットごと）から、まとめ方を決める。chests: [{ key, size, slots: [{ name, count }] }]、stack(name): 1 スロットの数。
// 戻り値: { plan: { name: [{ key, count }] }, work: [name], saving }。
//   同じ物は 1 つのチェストに（入りきらなければ空きの多い順に分ける）。もともと一番多く入っているチェストを優先して、
//   動かす物を減らす。work は、今の並びが計画と違う物（別のチェストにある、半端なスタックが残っている）
export function defragPlan (chests, stack) {
  const names = new Map()
  for (const c of chests) {
    for (const { name, count } of c.slots) {
      const e = names.get(name) ?? { name, total: 0, stack: stack(name), held: {}, slotsHeld: {} }
      e.total += count
      e.held[c.key] = (e.held[c.key] ?? 0) + count
      e.slotsHeld[c.key] = (e.slotsHeld[c.key] ?? 0) + 1
      names.set(name, e)
    }
  }
  const room = Object.fromEntries(chests.map((c) => [c.key, c.size]))
  const order = chests.map((c) => c.key)
  const plan = {}
  const items = [...names.values()].map((e) => ({ ...e, need: Math.ceil(e.total / e.stack) }))
    .sort((a, b) => b.need - a.need || a.name.localeCompare(b.name))
  for (const e of items) {
    const byHeld = [...order].sort((a, b) => (e.held[b] ?? 0) - (e.held[a] ?? 0) || room[b] - room[a] || order.indexOf(a) - order.indexOf(b))
    const whole = byHeld.find((k) => room[k] >= e.need)
    if (whole) {
      plan[e.name] = [{ key: whole, count: e.total }]
      room[whole] -= e.need
      continue
    }
    let left = e.total
    plan[e.name] = []
    for (const k of [...order].sort((a, b) => room[b] - room[a])) {
      if (left <= 0 || room[k] <= 0) continue
      const count = Math.min(left, room[k] * e.stack)
      plan[e.name].push({ key: k, count })
      room[k] -= Math.ceil(count / e.stack)
      left -= count
    }
    if (left > 0) plan[e.name] = null // 入りきらない（ありえない: まとめるとスロットは減る）
  }
  const work = items.filter((e) => {
    const chunks = plan[e.name]
    if (!chunks) return false
    const keys = Object.keys(e.held)
    if (keys.length !== chunks.length || !chunks.every((ch) => e.held[ch.key] === ch.count)) return true
    return chunks.some((ch) => e.slotsHeld[ch.key] !== Math.ceil(ch.count / e.stack))
  }).map((e) => e.name)
  const before = chests.reduce((n, c) => n + c.slots.length, 0)
  const after = items.reduce((n, e) => n + (plan[e.name] ? plan[e.name].reduce((m, ch) => m + Math.ceil(ch.count / e.stack), 0) : e.slotsHeld ? Object.values(e.slotsHeld).reduce((a, b) => a + b, 0) : 0), 0)
  return { plan, work, saving: Math.max(0, before - after) }
}

// 家のチェストをまとめる。open(pos, use): チェストまで行って開き、use(window) を呼んで閉じる（primitives の withOpenChest）。
// 同じ物を全部のチェストから取り出して持ち物でまとめ、計画のチェストに入れ直す。入らなかった分は、ほかのチェストへ。
// 持ち物の空きが足りない物は飛ばす。戻り値: 結果の文
export async function defragChests (bot, chestPositions, open, signal) {
  const stack = (name) => stackOf(bot, name)
  const read = async () => {
    const out = []
    for (const pos of chestPositions) {
      signal?.throwIfAborted()
      await open(pos, async (win) => {
        out.push({ key: `${pos.x},${pos.y},${pos.z}`, pos, size: win.inventoryStart ?? SINGLE_CHEST, slots: win.containerItems().map((i) => ({ name: i.name, count: i.count })) })
      })
    }
    return out
  }
  const idOf = (name) => bot.registry.itemsByName[name].id
  const held = (name) => bot.inventory.items().filter((i) => i.name === name).reduce((n, i) => n + i.count, 0)
  const start = await read()
  const startSlots = start.reduce((n, c) => n + c.slots.length, 0)
  const skipped = []
  let moved = 0
  let chests = start
  for (let pass = 0; pass < 2; pass++) {
    const { plan, work } = defragPlan(chests, stack)
    if (!work.length) break
    let progress = 0
    for (const name of work) {
      signal?.throwIfAborted()
      const chunks = plan[name]
      const total = chests.reduce((n, c) => n + c.slots.filter((s) => s.name === name).reduce((m, s) => m + s.count, 0), 0)
      if (bot.inventory.emptySlotCount() < Math.ceil(total / stack(name))) { skipped.push(name); continue }
      const carried = held(name)
      for (const c of chests) {
        const here = c.slots.filter((s) => s.name === name).reduce((n, s) => n + s.count, 0)
        if (!here) continue
        await open(c.pos, async (win) => { await win.withdraw(idOf(name), null, here) })
      }
      for (const ch of chunks) {
        const target = chests.find((c) => c.key === ch.key)
        try {
          await open(target.pos, async (win) => { await win.deposit(idOf(name), null, Math.min(ch.count, held(name) - carried)) })
        } catch {
          // 入らなかった: 残りは最後にほかのチェストへ
        }
      }
      for (const c of chests) {
        const left = held(name) - carried
        if (left <= 0) break
        try {
          await open(c.pos, async (win) => { await win.deposit(idOf(name), null, left) })
        } catch {
          // 次のチェストへ
        }
      }
      if (held(name) > carried) skipped.push(`${name} (${held(name) - carried} left in the inventory)`)
      progress++
      moved++
    }
    chests = await read()
    if (!progress) break
  }
  const endSlots = chests.reduce((n, c) => n + c.slots.length, 0)
  return `put the chests in order: ${moved} kinds moved, ${startSlots} → ${endSlots} slots used` + (skipped.length ? `; not moved: ${skipped.join(', ')}` : '')
}

// 家のチェストの中身の署名: デフラグのあと、中身が変わるまで出さない
export function contentsSignature (state) {
  if (!state.home || !state.memory) return ''
  return JSON.stringify(homeChests(state.memory, state.home).map((c) => [c.x, c.y, c.z, Object.entries(c.contents ?? {}).sort()]).sort())
}
