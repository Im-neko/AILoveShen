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
// 縁が崖の高み（屋根・柱の頂上）は、岩に囲まれた所ではない。既定では null（呼び出し側の「閉じた所から出る」の案内が
// 当てはまらない）。cliff: true を渡す呼び出し側には { cliff: true, ... } を返して、崖用の案内を出させる
// （2026-10-10: 塔の屋根の上で「閉じた空間（26 マス）」と断られ、積んでも出られない所で積む手を案内された）
export function rockBetween (bot, target, riding = false, { cliff = false } = {}) {
  if (riding || bot.entity.isInWater) return null
  const t = new Vec3(target.x, target.y, target.z)
  const region = walkableRegion(bot, standingCell(bot), (p) => p.distanceTo(t) <= MAX_DIG_GAP)
  if (region.open) return null
  if (region.cliff && !cliff) return null
  let gap = Infinity
  for (const p of region.cells.values()) gap = Math.min(gap, p.distanceTo(t))
  return gap > MAX_DIG_GAP ? { cells: region.cells.size, gap: Math.round(gap), ...(region.cliff ? { cliff: true } : {}) } : null
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
export function regionCached (bot, now = Date.now()) {
  const me = standingCell(bot)
  const memo = regionMemo.get(bot)
  if (memo && memo.at === `${me.x},${me.y},${me.z}` && now - memo.time < 3000) return memo.value
  const value = regionOf(bot)
  regionMemo.set(bot, { at: `${me.x},${me.y},${me.z}`, time: now, value })
  return value
}
// 行動のあと（掘る・置く・歩く）に捨てる: 位置が同じでも、まわりのブロックが変わっていれば読み直す
// （2026-10-10: 動かずに足元を掘っても 3 秒は古い結果が返り、積んでは降りる往復に気づけなかった）
export const forgetRegion = (bot) => regionMemo.delete(bot)
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

// ---- 崖の上（屋根・柱の頂上）から出る ----

// 足元が、climb_out で自分が積んだセルか（state.pillar）。そのブロックか null
export function piledUnder (bot, state) {
  const below = standingCell(bot).offset(0, -1, 0)
  if (!(state.pillar ?? []).some((p) => p.x === below.x && p.y === below.y && p.z === below.z)) return null
  const b = bot.blockAt(below)
  return b?.boundingBox === 'block' ? b : null
}

export const MAX_FALL = 12 // 縁から降りる先を探す深さ
const BAD_FLOOR = /lava|cactus|magma|fire|campfire|sweet_berry|powder_snow/
const solidAt = (b) => b?.boundingBox === 'block'
const clearAt = (b) => !!b && b.boundingBox === 'empty' && b.name !== 'lava'
const AROUND = [[1, 0], [-1, 0], [0, 1], [0, -1]]

// 歩ける範囲の縁から、飛び降りる先: [{ from, dir, landing, drop, damage }]（ダメージの少ない順、近い順）。
// 落下ダメージは (落差 − 3)。水の上に降りるならなし。降りた先が、いまの高みより広い（別の穴に降りるだけではない）ものだけ
export function dropOffs (bot, region = regionOf(bot), { limit = 6 } = {}) {
  const me = standingCell(bot)
  const found = []
  for (const c of region.cells.values()) {
    for (const [dx, dz] of AROUND) {
      const n = c.offset(dx, 0, dz)
      if (!clearAt(bot.blockAt(n)) || !clearAt(bot.blockAt(n.offset(0, 1, 0)))) continue
      for (let k = 1; k <= MAX_FALL; k++) {
        const f = n.offset(0, -k, 0)
        const here = bot.blockAt(f)
        if (!here || !(clearAt(here) || here.name === 'water') || !clearAt(bot.blockAt(f.offset(0, 1, 0)))) break
        const below = bot.blockAt(f.offset(0, -1, 0))
        if (!below) break
        if (!solidAt(below) && below.name !== 'water') continue
        if (BAD_FLOOR.test(below.name)) break
        const drop = c.y - f.y
        if (drop <= DROP) break // 歩いて降りられる（崖ではない）
        found.push({ from: c, dir: [dx, dz], landing: f, drop, damage: below.name === 'water' ? 0 : drop - DROP, cost: (below.name === 'water' ? 0 : drop - DROP) + 0.05 * me.distanceTo(c) })
        break
      }
    }
  }
  found.sort((a, b) => a.cost - b.cost)
  const out = []
  for (const o of found) {
    if (out.length >= limit) break
    const beyond = walkableRegion(bot, o.landing, () => false)
    if (beyond.open || beyond.cells.size > region.cells.size) out.push(o)
  }
  return out
}

// 足元に積んで上がった先（積む列の上）が、歩いて出られる所か。cell から真上へ、頭の高さの岩を掘りながら 1 つずつ
// 上がったとして、最初に「閉じていない」高さで: ok（開けている）か、plateau（縁が崖の屋根・柱の頂上）。
// dug は途中で掘る固いブロックの数。isProtected(ブロック, 位置) は、保護されている（家・建物）かを返す関数で、protectedCount に数える
export function climbExit (bot, cell, { max = 24, isProtected = () => false } = {}) {
  for (let h = 1; h <= max; h++) {
    const standing = cell.offset(0, h, 0)
    const head = bot.blockAt(standing.offset(0, 1, 0))
    const feet = bot.blockAt(standing)
    if (!head || !feet) return { ok: false, why: 'the space above is not loaded', dug: 0, protectedCount: 0 }
    const region = walkableRegion(bot, standing)
    if (region.open) return { ok: true, height: h, dug: dugAbove(bot, cell, h), protectedCount: protectedAbove(bot, cell, h, isProtected) }
    if (region.cliff) return { ok: false, plateau: true, height: h, at: standing, dug: dugAbove(bot, cell, h), protectedCount: protectedAbove(bot, cell, h, isProtected) }
  }
  return { ok: false, why: `no way out within ${max} blocks above`, dug: dugAbove(bot, cell, max), protectedCount: protectedAbove(bot, cell, max, isProtected) }
}

// cell の 2 つ上から h + 1 つ上までの、掘る固いブロック
const columnAbove = (bot, cell, h) => {
  const out = []
  for (let k = 2; k <= h + 1; k++) {
    const pos = cell.offset(0, k, 0)
    const b = bot.blockAt(pos)
    if (solidAt(b)) out.push({ b, pos })
  }
  return out
}
const dugAbove = (bot, cell, h) => columnAbove(bot, cell, h).length
const protectedAbove = (bot, cell, h, isProtected) => columnAbove(bot, cell, h).filter(({ b, pos }) => isProtected(b, pos)).length
