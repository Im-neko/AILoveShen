// 閉じた所から壁を掘って出る（docs/design/90 §5.1）。
//
// 水が入った土の小部屋に素手で閉じ込められると、出る手がなかった（2026-10-06: ベンチマークの cage。13 手のうち
// 9 手が「いちばん近い陸へ泳ぐ」で、陸は壁の外にあるので毎回失敗した）。`climb_out` は土か丸石を持っていないと
// 出ず、壁を掘る手の候補はなかった。ここは、歩いて出られない閉じた所（reach.mjs の closedSpace）から、
// いちばん掘る数が少ない壁を選び、掘って外へ出る道を作る。
//
// 掘ってよい壁: 保護されていない（家・建物・自分の囲い）、溶岩に接していない、上に落ちる砂や砂利がない、
// 壊せる。水を止めている壁は、つながった水が WATER_RELEASE_MAX マス未満のときだけ（自分の小部屋の水なら、
// 解き放っても小さい。池や海を止めている壁は掘らない。設計書 55）。
import { walkableRegion } from './reach.mjs'
import { standingCell } from './observe.mjs'
import { protectedReason } from './home.mjs'
import { holdsBackLiquid } from './liquid.mjs'

export const DIG_OUT_THICKNESS = 4 // 壁の厚さ（掘る層の数）の上限
export const WATER_RELEASE_MAX = 80 // 掘って解き放ってよい水（つながった水のマスの数）
const DROP = 3 // 壁の外で降りてよい段差
const REGION_BUDGET = 400 // 外側の広さを数える上限（これより広ければ開けている）
const TRIES = 6
const DIRS = [[1, 0], [-1, 0], [0, 1], [0, -1]]
const SIX = [[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]]
const FALLING = /(^|_)(sand|gravel)$|concrete_powder$/

const key = (p) => `${p.x},${p.y},${p.z}`
const solid = (b) => b?.boundingBox === 'block'
const airy = (b) => !!b && b.boundingBox === 'empty' && b.name !== 'lava'

// pos の横と上に接する水から、つながった水のマスを limit まで数える
function waterBody (bot, pos, limit) {
  const seen = new Set()
  const stack = []
  for (const [x, y, z] of [[1, 0, 0], [-1, 0, 0], [0, 0, 1], [0, 0, -1], [0, 1, 0]]) {
    const p = pos.offset(x, y, z)
    if (bot.blockAt(p)?.name === 'water') { seen.add(key(p)); stack.push(p) }
  }
  while (stack.length && seen.size < limit) {
    const c = stack.pop()
    for (const [x, y, z] of SIX) {
      const n = c.offset(x, y, z)
      if (!seen.has(key(n)) && bot.blockAt(n)?.name === 'water') { seen.add(key(n)); stack.push(n) }
    }
  }
  return seen.size
}

// 掘ってはいけない理由か null（固いブロックだけを見る。空き・水は掘らない）
function digProblem (bot, state, pos, breakProtected = false) {
  const b = bot.blockAt(pos)
  if (!b) return 'it is not loaded'
  if (!solid(b)) return null
  if (b.diggable === false) return `${b.name} cannot be dug`
  const why = breakProtected ? null : protectedReason(state, pos, 0, b)
  if (why) return why
  if (SIX.some(([x, y, z]) => bot.blockAt(pos.offset(x, y, z))?.name === 'lava')) return 'lava is next to it'
  if (FALLING.test(bot.blockAt(pos.offset(0, 1, 0))?.name ?? '')) return 'sand or gravel above it would fall in'
  if (holdsBackLiquid(bot, pos) && waterBody(bot, pos, WATER_RELEASE_MAX) >= WATER_RELEASE_MAX) {
    return 'it holds back a large body of water or lava'
  }
  return null
}

// 壁の外で立てるセル（足の位置）か null: 足と頭が空いていて、DROP 段までの下に床（固いか水）がある
function standable (bot, feet) {
  for (let k = 0; k <= DROP; k++) {
    const f = feet.offset(0, -k, 0)
    if (!airy(bot.blockAt(f)) || !airy(bot.blockAt(f.offset(0, 1, 0)))) return null
    const below = bot.blockAt(f.offset(0, -1, 0))
    if (solid(below) || below?.name === 'water') return f
  }
  return null
}

// 掘って出る道: { from, dir, cells, outside } か { why }。cells は掘るブロック（手前の層から、足・頭の順）、
// outside は出た先に立つセル。閉じていなければ { why: 'not shut in' }
// breakProtected: 家・建物の壁も掘ってよい（ほかに出る道がないときの最後の手。2026-10-10: 塔の中の閉じた部屋から、
// 保護された建物のセルしかなく出る道がなかった）。掘った保護のセルの数は、返す値の protectedCells
export function digOutRoute (bot, state, region = walkableRegion(bot, standingCell(bot)), { breakProtected = false } = {}) {
  if (region.open || region.cliff) return { why: 'not shut in' }
  const me = standingCell(bot)
  let why = 'no wall within 4 blocks has open ground behind it'
  const found = []
  for (const c of region.cells.values()) {
    for (const [dx, dz] of DIRS) {
      const cells = []
      let outside = null
      for (let k = 1; k <= DIG_OUT_THICKNESS + 1; k++) {
        const feet = c.offset(dx * k, 0, dz * k)
        const head = feet.offset(0, 1, 0)
        const fb = bot.blockAt(feet)
        const hb = bot.blockAt(head)
        if (!fb || !hb) break // 読み込まれていない
        if (airy(fb) && airy(hb)) {
          // 領域の隣が空いているのに歩けないのは、床がないから（崖）: この向きは使わない
          if (k > 1) outside = standable(bot, feet)
          break
        }
        const problem = [feet, head].map((p) => digProblem(bot, state, p, breakProtected)).find(Boolean)
        if (problem) { why = problem; break }
        for (const p of [feet, head]) if (solid(bot.blockAt(p))) cells.push(p)
      }
      if (outside && cells.length) found.push({ from: c, dir: [dx, dz], cells, outside, cost: cells.length + 0.05 * me.distanceTo(c) })
    }
  }
  found.sort((a, b) => a.cost - b.cost)
  for (const r of found.slice(0, TRIES)) {
    const beyond = walkableRegion(bot, r.outside, () => false, REGION_BUDGET)
    // 外が開けているか、いまの小部屋より広い（別の小さな穴に出るだけではない）
    if (beyond.open || beyond.cells.size > region.cells.size) {
      const protectedCells = breakProtected ? r.cells.filter((p) => protectedReason(state, p, 0, bot.blockAt(p))).length : 0
      return { from: r.from, dir: r.dir, cells: r.cells, outside: r.outside, protectedCells }
    }
  }
  return { why: found.length ? 'every way out leads into another small closed space' : why }
}
