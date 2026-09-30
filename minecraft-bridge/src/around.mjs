// 目の前にあるもの: 足元の高さ ±2 マス、半径 8 マスの、空気に面したブロックと落ちているアイテム。
//
// これまでの観測は、覚えた場所（見えなくなったもの）と目印（ベッド、作業台など）と、ソルバーが
// 候補にしたものだけだった。そのため目の前の原木・作物・石が Gemini に見えず、無視して遠くへ
// 探しに行くことがあった（2026-09-27）。ここで種類ごとにまとめて、毎回の観測に入れる。
import { round, bearing } from './observe.mjs'
import { exposed } from './world.mjs'
import { isQuarryKind, quarrySpot, inQuarry } from './quarry.mjs'
import { isTossed } from './spare.mjs'
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
export function aroundMe (bot, dropsOf = () => [], untouchable = () => false, notGatherable = () => false, state = null) {
  const me = bot.entity.position
  const spot = state ? quarrySpot(state, me) : null
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
        if (!k) kinds.set(b.name, (k = { name: b.name, count: 0, dist: Infinity, rank: 2, at: null }))
        // 木でない原木（村の畑の縁、家の柱）: 数だけ。一番近いものには選ばない（docs/design/55）
        if (notGatherable(p, b)) {
          k.notTrees = (k.notTrees ?? 0) + 1
          continue
        }
        k.count++
        if (cropByBlock(b.name)) {
          // 作物は熟したものだけ掘れる（育ち途中は断られる）ので、一番近いものは熟したものの中から選ぶ
          const ripe = isRipe(b)
          k.ripe = (k.ripe ?? 0) + (ripe ? 1 : 0)
          if (!ripe) continue
        }
        // 石・土などの地形は、採掘の場所があればその中のいちばん近いものを先に（場所の外は、その次）
        const rank = spot && isQuarryKind(b.name) && !inQuarry(spot, p) ? 1 : 0
        if (rank < k.rank || (rank === k.rank && dist < k.dist)) { k.rank = rank; k.dist = dist; k.at = p }
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
        // 熟した作物が 1 つもなければ座標は出さない（ここの座標をそのまま dig に使って、育ち途中を断られていた）
        nearest: k.at ? { x: k.at.x, y: k.at.y, z: k.at.z, distance_m: round(k.dist), direction: bearing(me, k.at), dy: k.at.y - feet.y } : null,
        ...(gives.length ? { gives } : {}),
        ...(k.at && spot && isQuarryKind(k.name) && inQuarry(spot, k.at) ? { quarry: true } : {}),
        ...(k.ripe !== undefined ? { ripe: k.ripe } : {}),
        ...(k.notTrees ? { not_trees: k.notTrees } : {})
      }
    })
  const items = new Map()
  for (const e of Object.values(bot.entities ?? {})) {
    if (e === bot.entity || e.name !== 'item' || !e.position || isTossed(state, bot, e)) continue
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
