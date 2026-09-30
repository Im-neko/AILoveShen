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
// arrived: 目標に近いマスに届いたら打ち切る（そこまで歩いて行ける）
export function walkableRegion (bot, start, arrived = () => false, budget = REGION_BUDGET) {
  const cells = new Map([[key(start), start]])
  const queue = [start]
  let unknown = false
  if (arrived(start)) return { cells, open: true }
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
      }
      for (const p of lands) {
        if (cells.has(key(p))) continue
        cells.set(key(p), p)
        if (arrived(p)) return { cells, open: true }
        queue.push(p)
      }
    }
  }
  return { cells, open: unknown || queue.length > 0 }
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
