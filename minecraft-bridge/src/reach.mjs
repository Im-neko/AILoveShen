// 歩いて行ける範囲（docs/design/57_goto_through_rock.md）。
//
// 岩に囲まれた所から、岩の向こうの遠い場所へ goto すると、経路探索は掘り進む道を探して岩の中に広がり、
// 5 秒（thinkTimeout）で「Took to long to decide path to goal!」になる（2026-09-30: 石の中から 8 m 先へ）。
// 掘るコストを歩くより大きく見積もらないので、掘る距離が長いほど探索が膨らむ。歩いて出られる範囲が
// 小さく閉じていて、目標がその外の遠い所にあるときだけ、探索を始める前に断る。

import vec3Pkg from 'vec3'
import { standingCell } from './observe.mjs'

const { Vec3 } = vec3Pkg

export const REGION_BUDGET = 3000 // これ以上広ければ、閉じていない（岩の中ではない）とみなす
export const MAX_DIG_GAP = 4 // 歩ける範囲から目標までがこれ以内なら、掘って行ける（探索が間に合う）
const DROP = 3 // 降りてよい段差
const SIDES = [[1, 0], [-1, 0], [0, 1], [0, -1]]
// 当たり判定があっても通れる物: 開け閉めして通る、歩きながら刈る
const PASSABLE = /(_door|_fence_gate|_trapdoor|_leaves)$/

const key = (p) => `${p.x},${p.y},${p.z}`

// 歩いて行けるマス（足の位置）。unknown: 読み込まれていない所に届いた（閉じているとは言えない）
// cliff: 歩ける範囲の縁に、床が 3 段下まで見つからない空いた柱（崖、1 本の柱の頂上のまわり）がある
// arrived: 目標に近いマスに届いたら打ち切る（そこまで歩いて行ける）
export function walkableRegion (bot, start, arrived = () => false, budget = REGION_BUDGET) {
  const cells = new Map([[key(start), start]])
  const queue = [start]
  let unknown = false
  let cliff = false
  if (arrived(start)) return { cells, open: true, cliff }
  const at = (p) => {
    const b = bot.blockAt(p)
    if (!b) unknown = true
    return b
  }
  const free = (p) => {
    const b = at(p)
    return !!b && b.name !== 'lava' && (b.boundingBox === 'empty' || PASSABLE.test(b.name))
  }
  const floor = (p) => {
    const b = at(p.offset(0, -1, 0))
    return !!b && (b.boundingBox === 'block' || b.name === 'water')
  }
  while (queue.length && cells.size < budget) {
    const c = queue.shift()
    for (const [dx, dz] of SIDES) {
      const n = c.offset(dx, 0, dz)
      const lands = []
      // 1 段上る: 頭の上が空いていて、隣の 1 つ上の足場に立てる
      const up = n.offset(0, 1, 0)
      if (free(c.offset(0, 2, 0)) && free(up) && free(up.offset(0, 1, 0)) && floor(up)) lands.push(up)
      // 同じ高さ、または降りる（柱が空いている間、床のあるところまで）
      for (let k = 0; k <= DROP; k++) {
        const f = n.offset(0, -k, 0)
        if (!free(f) || !free(f.offset(0, 1, 0))) break
        if (floor(f)) { lands.push(f); break }
        if (k === DROP) cliff = true
      }
      for (const p of lands) {
        if (cells.has(key(p))) continue
        cells.set(key(p), p)
        if (arrived(p)) return { cells, open: true, cliff }
        queue.push(p)
      }
    }
    if (unknown) return { cells, open: true, cliff } // 読み込まれていない所に届いた: 閉じているとは言えない（先を数えない）
  }
  return { cells, open: unknown || queue.length > 0, cliff }
}

// 岩の向こうか。歩いて出られる範囲が閉じていて、目標がそこから遠ければ { cells, gap }、そうでなければ null
export function rockBetween (bot, target, riding = false) {
  if (riding || bot.entity.isInWater) return null
  const t = new Vec3(target.x, target.y, target.z)
  const region = walkableRegion(bot, standingCell(bot), (p) => p.distanceTo(t) <= MAX_DIG_GAP)
  if (region.open) return null
  let gap = Infinity
  for (const p of region.cells.values()) gap = Math.min(gap, p.distanceTo(t))
  return gap > MAX_DIG_GAP ? { cells: region.cells.size, gap: Math.round(gap) } : null
}

// いまの足元から歩ける範囲を調べる: { cells, open, cliff }
const regionOf = (bot) => walkableRegion(bot, standingCell(bot))

// 歩いて出られない閉じた所にいるか（{ cells: 歩けるマスの数 } か null）。階段の床が抜けて 1 段ずつ上がれない、
// 深い穴に落ちた、掘った穴の口が埋まった、水が入った小部屋、など。家の中や自分で塞いだ穴も閉じているので、
// 呼ぶ側が除く。壁や天井で閉じているときだけ: 縁に崖がある所（積んだ柱の頂上）は、積んでも出られない
// （2026-10-04: 柱の頂上は 1 マスの閉じた所と数えられ、24 個積んでは失敗を 5 回繰り返して 110 ブロックの塔になった）
export function closedSpace (bot) {
  return closedFrom(bot, regionOf(bot))
}
const closedFrom = (bot, region) => bot.entity && !region.open && !region.cliff ? { cells: region.cells.size } : null

// 歩ける範囲が閉じていて、縁が崖（落ちる）にいるか（{ cells } か null）。積んだ柱の頂上など。出る道は下
export function isolatedSpace (bot) {
  return isolatedFrom(bot, regionOf(bot))
}
const isolatedFrom = (bot, region) => bot.entity && !region.open && region.cliff ? { cells: region.cells.size } : null

// 結果を 3 秒だけ覚える（候補を作るたびに、広い所では 3000 マスを読み直さないように）
const regionMemo = new WeakMap()
function regionCached (bot, now) {
  const me = standingCell(bot)
  const memo = regionMemo.get(bot)
  if (memo && memo.at === `${me.x},${me.y},${me.z}` && now - memo.time < 3000) return memo.value
  const value = regionOf(bot)
  regionMemo.set(bot, { at: `${me.x},${me.y},${me.z}`, time: now, value })
  return value
}
export const closedSpaceCached = (bot, now = Date.now()) => closedFrom(bot, regionCached(bot, now))
export const isolatedSpaceCached = (bot, now = Date.now()) => isolatedFrom(bot, regionCached(bot, now))

// 足元が 1 本の柱か（下の 3 段が固く、四方が空気）: 自分で積んだ柱の頂上。台帳は丸太と家具しか覚えていないので、形で見る
export function pillarUnder (bot) {
  const feet = standingCell(bot)
  const below = bot.blockAt(feet.offset(0, -1, 0))
  if (below?.boundingBox !== 'block') return null
  for (let dy = 1; dy <= 3; dy++) {
    const level = feet.offset(0, -dy, 0)
    if (bot.blockAt(level)?.boundingBox !== 'block') return null
    for (const [dx, dz] of SIDES) {
      const side = bot.blockAt(level.offset(dx, 0, dz))
      if (!side || side.boundingBox === 'block' || /water|lava/.test(side.name)) return null
    }
  }
  return below
}
