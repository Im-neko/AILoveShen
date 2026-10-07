// 採掘は、家のそばの採掘場から（docs/design/68_mine_by_the_bridge.md）。
//
// 家があるとき、石と地下の鉱石は、ブリッジが自分で設計する採掘場（らせん階段で鉱石の高さまで下り、
// 底の部屋から横へ坑道を掘る）から取る。石は、地表から 10 マス以上下でないと掘らない。
// 家がない（または仮の宿しかない）ときは、今までの階段掘り（dig_down、設計書 17・64）のまま。

import vec3Pkg from 'vec3'
import { isLog } from './observe.mjs'
import { minesAt, mineBottom, onMineCell, MINE_BOTTOM, MINE_MIN_STEPS } from './facilities.mjs'

const { Vec3 } = vec3Pkg

export const SURFACE_DEPTH = 10 // 石はこれ以上、地表から下でないと掘らない
export const SITE_NEAR = 10 // 採掘場の入口は、家の壁からこの内側には作らない
export const SITE_FAR = 30 // …これより外にも作らない
const SITE_STEP = 4 // 家からの距離を試す間隔
const SITE_ANGLES = 16
const ALIGN_STEP = 6 // 2 つ目からは、前の採掘場の入口と同じ行に、この間隔で並べる
const TOLERANCE = 8 // 鉱石の高さと採掘場の底の高さがこの内なら、その採掘場で足りる（坑道と枝は底の 2 マスの高さしか見えない）
const STONE_DEPTH = 14 // 石だけ欲しいとき、採掘場の底は家の床からこれだけ下
const STONE = /^(stone|granite|diorite|andesite|deepslate|tuff)$/

// 鉱石が多い高さ（1.21 の分布から。deepslate_ は外して引く）
const ORE_Y = { coal_ore: 32, copper_ore: 32, iron_ore: 16, gold_ore: -16, lapis_ore: 0, redstone_ore: -58, diamond_ore: -58, emerald_ore: 0 }
const DEEP_STONE_Y = -20 // deepslate は y 0 より下

const isLeaves = (name) => !!name?.endsWith('_leaves')
const plain = (name) => String(name).replace(/^deepslate_/, '')

// 採掘場のほかの深い岩を、経路探索に掘らせない（設計書 86: 鉱石が見えると、採掘場を使わず岩を掘り抜いて向かい、
// 22 段の高さに無計画な穴を残した）。自分で建てた家があるとき、家の床より SURFACE_DEPTH 以上下の岩・鉱石は、
// 採掘場のセル（階段・部屋・坑道・枝）以外は壊さない。歩いて行ける所（洞窟）は通れる
export const ROCK = /^(stone|deepslate|tuff|granite|diorite|andesite)$|_ore$/
export function protectedRock (state, p, name) {
  return !!state.home && !state.home.found && ROCK.test(name) && p.y < Math.floor((state.home.inside ?? state.home.min).y) - SURFACE_DEPTH && !onMineCell(state, p)
}

// 家のそばの採掘場から取る相手か（自分で建てた家がある）
export const mineFromHome = (state) => !!state.home && !state.home.found

// 洞窟（自然の空洞）に出ている深い鉱石か: 家の床より SURFACE_DEPTH 以上下の鉱石で、採掘場のセルに接していないもの。
// 取らない（鉱石は採掘場の壁に出たものだけ。user: 「地下探索する時はブランチマイニング」。設計書 86 §4 で「歩いて行ける
// 洞窟の鉱石は取ってよい」にしていたが、洞窟を歩いて蜘蛛に殺された: 2026-10-07 04:55Z）。浅い所の露頭（地表の石炭など）は取る
const NEIGHBOURS = [[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]]
export function caveOre (state, p, name) {
  if (!mineFromHome(state) || !String(name).endsWith('_ore')) return false
  if (p.y >= Math.floor((state.home.inside ?? state.home.min).y) - SURFACE_DEPTH) return false
  return !onMineCell(state, p) && !NEIGHBOURS.some(([x, y, z]) => onMineCell(state, { x: p.x + x, y: p.y + y, z: p.z + z }))
}

// 掘る先（sources）を集めるのに合う採掘場の底の高さ: 鉱石の高さ。石だけなら null（どの採掘場でもよい）
export function oreY (sources) {
  for (const s of sources) if (ORE_Y[plain(s)] !== undefined) return ORE_Y[plain(s)]
  if (sources.includes('deepslate')) return DEEP_STONE_Y
  return null
}

// 新しく設計するときの底の高さ
export function bottomFor (state, sources) {
  const y = oreY(sources) ?? Math.floor(state.home.inside.y) - STONE_DEPTH
  return Math.max(MINE_BOTTOM.min, Math.min(y, Math.floor(state.home.inside.y) - MINE_MIN_STEPS))
}

// 採掘の次の一手（solver の world.minePlan）: { go: 採掘場 } 下りて鉱石のある所へ / { dig: 名前 } 途中の採掘場の続きを掘る /
// { make: bottom_y } 新しく設計して掘る（{ exhausted: 名前 } = 底まで掘り尽くして、壁に鉱石が出なかった採掘場。
// make_mine がその印を付けて、次の採掘場は別の入口に作る）
export function minePlan (bot, state, sources) {
  const at = minesAt(bot, state, oreY(sources), TOLERANCE)
  const open = at.find((m) => m.fac.status !== 'done')
  if (open) return { dig: open.fac.name }
  const done = at[0]
  if (done) {
    const above = bot.entity.position.y > done.bottom.y + 2
    // 下りきった採掘場の底にいて、見える所に鉱石がない: 掘り尽くした。新しい採掘場へ
    return above ? { go: { name: done.fac.name, bottom: done.bottom, above } } : { make: bottomFor(state, sources), exhausted: done.fac.name }
  }
  return { make: bottomFor(state, sources) }
}

// 入口（輪の隅 x0, z0）の候補: 前の採掘場があれば、その入口と同じ行に ALIGN_STEP おきに、次に家の外周の輪を、
// 家の壁から SITE_NEAR〜SITE_FAR の距離（近い順）で。個数の上限は付けない（設計書 68 §3）
export function mineSites (state) {
  const home = state.home
  const cx = (home.min.x + home.max.x + 1) / 2
  const cz = (home.min.z + home.max.z + 1) / 2
  const half = Math.max(home.max.x - home.min.x + 1, home.max.z - home.min.z + 1) / 2
  const within = ([x0, z0]) => {
    const d = Math.hypot(x0 + 2 - cx, z0 + 2 - cz) - half
    return d >= SITE_NEAR && d <= SITE_FAR
  }
  const sites = []
  const mines = Object.values(state.facilities ?? {}).filter((f) => f.kind === 'mine' && f.layout?.cells?.length)
  for (const m of mines) {
    const e = m.layout.cells[0]
    for (let k = 1; k <= 5; k++) {
      for (const [dx, dz] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) sites.push([e.x + dx * k * ALIGN_STEP, e.z + dz * k * ALIGN_STEP])
    }
  }
  const ring = []
  for (let d = SITE_NEAR; d <= SITE_FAR; d += SITE_STEP) {
    for (let i = 0; i < SITE_ANGLES; i++) {
      const a = (i / SITE_ANGLES) * 2 * Math.PI
      ring.push([Math.round(cx + Math.cos(a) * (half + d)) - 2, Math.round(cz + Math.sin(a) * (half + d)) - 2])
    }
  }
  return [...sites.filter(within), ...ring]
}

// そこから上へ、空気や水、木に当たるまでの固いブロックの数（SURFACE_DEPTH で頭打ち）。読み込まれていなければ null
function coverAbove (bot, p) {
  for (let dy = 1; dy <= SURFACE_DEPTH; dy++) {
    const b = bot.blockAt(new Vec3(p.x, p.y + dy, p.z))
    if (!b) return null
    if (b.boundingBox === 'block' && !isLog(b.name) && !isLeaves(b.name)) continue
    return dy - 1
  }
  return SURFACE_DEPTH
}

// 家があるとき、地表に近い石か（石 / 花崗岩 / 閃緑岩 / 安山岩 / 深層岩 / 凝灰岩）。掘らずに採掘場へ。
// 鉱石、土、砂利、砂は対象外。上が空いた所までの固いブロックの数が SURFACE_DEPTH 未満なら真
export function shallowStone (bot, state, p, name) {
  if (!mineFromHome(state) || !STONE.test(name ?? bot.blockAt(p)?.name)) return false
  // 家の床より SURFACE_DEPTH 以上下なら、深い: 自分で掘った縦穴（採掘場の階段）の下は、頭の上が空気で岩の厚みが
  // 薄く見えるので、地表に近いと誤判定していた（2026-10-07: 地下 28 ブロックの岩を「地表に近い」と断られ、出られなかった）
  if (p.y < Math.floor((state.home.inside ?? state.home.min).y) - SURFACE_DEPTH) return false
  const cover = coverAbove(bot, p)
  return cover !== null && cover < SURFACE_DEPTH
}

export const SHALLOW_REMEDY = `stone is taken from the mine (at least ${SURFACE_DEPTH} blocks below the surface), not from the surface: use the suggestion that makes or digs the mine`

export { mineBottom }
