// 目の前にあるもの: 足元の高さ ±2 マス、半径 8 マスの、空気に面したブロックと落ちているアイテム。
//
// これまでの観測は、覚えた場所（見えなくなったもの）と目印（ベッド、作業台など）と、ソルバーが
// 候補にしたものだけだった。そのため目の前の原木・作物・石が Gemini に見えず、無視して遠くへ
// 探しに行くことがあった（2026-09-27）。ここで種類ごとにまとめて、毎回の観測に入れる。
import { round, bearing } from './observe.mjs'
import { exposed } from './world.mjs'
import { cropByBlock, isRipe } from './farming.mjs'
import vec3Pkg from 'vec3'

const { Vec3 } = vec3Pkg

export const AROUND_RADIUS = 8 // 水平の距離
export const AROUND_DY = 2 // 足元の高さからの上下
export const AROUND_KINDS = 24 // 近い順にこれだけの種類
const EMPTY = new Set(['air', 'cave_air', 'void_air'])

// dropsOf(name): そのブロックから取れるアイテム（Knowledge.dropsOf。同じ名前だけなら []）
// untouchable(p, block): 断られる場所（今の家、建物、建てている家）なら真。ここに出すと、掘れと
// 言われて断られる（配信者はここの座標をそのまま dig に使う）ので出さない
export function aroundMe (bot, dropsOf = () => [], untouchable = () => false) {
  const me = bot.entity.position
  const feet = me.floored()
  const kinds = new Map()
  for (let dy = -AROUND_DY; dy <= AROUND_DY; dy++) {
    for (let dx = -AROUND_RADIUS; dx <= AROUND_RADIUS; dx++) {
      for (let dz = -AROUND_RADIUS; dz <= AROUND_RADIUS; dz++) {
        if (dx * dx + dz * dz > AROUND_RADIUS * AROUND_RADIUS) continue
        const p = new Vec3(feet.x + dx, feet.y + dy, feet.z + dz)
        const b = bot.blockAt(p)
        if (!b || EMPTY.has(b.name) || !exposed(bot, p) || untouchable(p, b)) continue
        const dist = p.offset(0.5, 0.5, 0.5).distanceTo(me)
        let k = kinds.get(b.name)
        if (!k) kinds.set(b.name, (k = { name: b.name, count: 0, dist: Infinity, at: null }))
        k.count++
        if (cropByBlock(b.name)) k.ripe = (k.ripe ?? 0) + (isRipe(b) ? 1 : 0)
        if (dist < k.dist) { k.dist = dist; k.at = p }
      }
    }
  }
  const blocks = [...kinds.values()]
    .sort((a, b) => a.dist - b.dist)
    .slice(0, AROUND_KINDS)
    .map((k) => {
      const gives = dropsOf(k.name)
      return {
        name: k.name,
        count: k.count,
        nearest: { x: k.at.x, y: k.at.y, z: k.at.z, distance_m: round(k.dist), direction: bearing(me, k.at), dy: k.at.y - feet.y },
        ...(gives.length ? { gives } : {}),
        ...(k.ripe !== undefined ? { ripe: k.ripe } : {})
      }
    })
  const items = new Map()
  for (const e of Object.values(bot.entities ?? {})) {
    if (e === bot.entity || e.name !== 'item' || !e.position) continue
    const d = e.position.minus(me)
    if (Math.hypot(d.x, d.z) > AROUND_RADIUS || Math.abs(d.y) > AROUND_DY) continue
    const drop = e.getDroppedItem?.()
    const name = drop?.name ?? 'unknown'
    const dist = e.position.distanceTo(me)
    const it = items.get(name) ?? { item: name, count: 0, dist: Infinity, at: null }
    it.count += drop?.count ?? 1
    if (dist < it.dist) { it.dist = dist; it.at = e.position }
    items.set(name, it)
  }
  const dropped = [...items.values()]
    .sort((a, b) => a.dist - b.dist)
    .map((it) => ({ item: it.item, count: it.count, distance_m: round(it.dist), direction: bearing(me, it.at) }))
  return { radius: AROUND_RADIUS, dy: AROUND_DY, blocks, items: dropped }
}
