// 陸を探して泳ぎ着く（docs/design/89_camp.md）。
//
// 水の中にいるボットは、経路探索器が水の中から経路を作らないので、goto も explore も効かず、
// 目標が何であっても進まなかった（2026-10-06: 新しい世界の海で、昼の残りも夜も水の上にいた）。
// 読み込まれている範囲で、いちばん近い「立てる陸」を探し、そこへ水面沿いに泳いで上がる（swim.mjs）。
import vec3Pkg from 'vec3'
import { holdsWater, passable } from './breath.mjs'
import { swimToward, isSwimming, onLand } from './swim.mjs'
import { closedSpace } from './reach.mjs'

const { Vec3 } = vec3Pkg

export const LAND_RADIUS = 48
const LAND_TRIES = 3
const SWIM_MS = 120000 // 1 回の泳ぎの合計（陸を替えながらの 3 回で、ステップが 4 分以上かからないように）
const AVOID_M = 6 // 行けなかった陸のこの近くは、次の探索から外す
const CACHE_MS = 3000
const HAZARD = /lava|fire|magma|cactus|sweet_berry|powder_snow/
// 足の高さの前後で、地面を探す高さ（近い順）
const LEVELS = [0, -1, 1, -2, 2, -3, 3, 4]

const solid = (b) => !!b && b.boundingBox === 'block' && !HAZARD.test(b.name) && !holdsWater(b)
const open = (b) => passable(b) && !holdsWater(b)

// 水面から這い上がれる地面の高さ（地面のブロックの y）の上限: 水面（いちばん上の水ブロック）の 1 つ上まで。
// それより高い岸（2026-10-18: 沼地の 3〜4 ブロックの崖）は、見えていても泳いで上がれない
const reachableGround = (bot) => {
  const feet = bot.entity.position.floored()
  let top = feet.y
  while (top < feet.y + 8 && holdsWater(bot.blockAt(new Vec3(feet.x, top + 1, feet.z)))) top++
  return top + 1
}

// x,z の列に、立てる地面（固くて、上の 2 マスが水でない空き）があれば、その地面のブロックの y。なければ null
// cap: 地面の高さの上限（水から上がるとき。水を出たあとの続きの地面には付けない）
function groundY (bot, x, z, y0, cap = Infinity) {
  for (const dy of LEVELS) {
    const y = y0 + dy
    if (y > cap) continue
    if (solid(bot.blockAt(new Vec3(x, y, z))) && open(bot.blockAt(new Vec3(x, y + 1, z))) && open(bot.blockAt(new Vec3(x, y + 2, z)))) return y
  }
  return null
}

// 1 マスだけの岩や柱は陸とみなさない: 四方のうち 2 つ以上にも、立てる地面が続く
function isLand (bot, x, z, y0, cap) {
  const y = groundY(bot, x, z, y0, cap)
  if (y == null) return null
  let around = 0
  for (const [dx, dz] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
    if (groundY(bot, x + dx, z + dz, y) != null) around++
  }
  return around >= 2 ? y : null
}

// 読み込まれている範囲でいちばん近い陸 → { x, y, z, distance, pos } か null。
// y は足を置くセル（地面の上）。avoid: 行けなかった陸（{ x, z }）の近くは外す
export function findLand (bot, { radius = LAND_RADIUS, avoid = [] } = {}) {
  const me = bot.entity?.position
  if (!me) return null
  const x0 = Math.floor(me.x)
  const z0 = Math.floor(me.z)
  const y0 = Math.floor(me.y)
  const cap = reachableGround(bot)
  for (let r = 1; r <= radius; r++) {
    let best = null
    const visit = (x, z) => {
      if (avoid.some((a) => Math.hypot(a.x - x, a.z - z) <= AVOID_M)) return
      const gy = isLand(bot, x, z, y0, cap)
      if (gy == null) return
      const d = Math.hypot(x + 0.5 - me.x, z + 0.5 - me.z)
      if (!best || d < best.distance) best = { x, y: gy + 1, z, distance: Math.round(d) }
    }
    // 四角い輪の上（中は前の輪で見た）
    for (let i = -r; i <= r; i++) {
      visit(x0 + i, z0 - r)
      visit(x0 + i, z0 + r)
      if (i > -r && i < r) { visit(x0 - r, z0 + i); visit(x0 + r, z0 + i) }
    }
    // 輪の角は半径の 1.42 倍まで遠い: 次の輪にもっと近い陸があるかもしれないので、1 つ先の輪まで見て近いほうを取る
    if (best) {
      const next = r + 1 <= radius ? r + 1 : r
      if (next !== r) {
        const keep = best
        best = null
        for (let i = -next; i <= next; i++) {
          visit(x0 + i, z0 - next)
          visit(x0 + i, z0 + next)
          if (i > -next && i < next) { visit(x0 - next, z0 + i); visit(x0 + next, z0 + i) }
        }
        if (!best || keep.distance <= best.distance) best = keep
      }
      return { ...best, pos: new Vec3(best.x, best.y, best.z) }
    }
  }
  return null
}

// 候補を作るたびに探し直さない（広い範囲を読む）
const memo = new WeakMap()
export function findLandCached (bot, now = Date.now()) {
  const p = bot.entity?.position
  if (!p) return null
  const at = `${Math.floor(p.x)},${Math.floor(p.z)}`
  const m = memo.get(bot)
  if (m && m.at === at && now - m.time < CACHE_MS) return m.value
  const value = findLand(bot)
  memo.set(bot, { at, time: now, value })
  return value
}

// 水から陸へ泳いで上がる。着けなかった陸は外して、LAND_TRIES 回まで別の陸を試す
export async function reachLand (bot, signal) {
  if (!isSwimming(bot)) return 'already on land'
  // 壁や天井で閉じた所（水が入った小部屋）の「陸」は、壁の外か天井の上で、泳いでも着けない（docs/design/90 §5.1）
  if (closedSpace(bot)) throw new Error('you are shut in by walls: the land you can see is outside them, so swimming cannot reach it (dig out through a wall: dig_out)')
  const tried = []
  let why = ''
  const deadline = Date.now() + SWIM_MS
  for (let i = 0; i < LAND_TRIES; i++) {
    signal?.throwIfAborted()
    if (Date.now() >= deadline) { why ||= 'time is up'; break }
    // 反射が数秒ごとに呼ぶので、最初は候補の探索（3 秒）の結果を使う。陸が読み込まれていない海では探索が重い
    const land = i === 0 ? findLandCached(bot) : findLand(bot, { avoid: tried })
    if (!land) throw new Error(`no land within ${LAND_RADIUS} m in the loaded world${tried.length ? ` besides the ones that could not be reached (${why})` : ''}: swim toward ground you have not seen, or look for a boat`)
    const r = await swimToward(bot, land, signal, { maxMs: Math.max(1000, deadline - Date.now()), climb: true })
    if (r.landed || onLand(bot)) return `swam ${r.moved} m and climbed onto land at ${land.x},${land.y},${land.z}`
    why = r.why
    tried.push(land)
  }
  throw new Error(`could not climb onto land: ${why}`)
}
