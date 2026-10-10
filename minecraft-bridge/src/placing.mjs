// 好きな物を好きな場所に置く条件 placed(item, where, count)（2026-09-27: 「家の中に松明を置いて」と
// 約束しても、それを表す条件がなく何もしなかった。ユーザー: 任意の場所に任意のアイテムを置ける条件に）。
//
// 場所は座標をじかに決めさせず、目印で書く（設計書 25 と同じ考え）:
//   home        家の部屋の中（床と壁の高さ）
//   near_home   家のまわり（家から 3〜12 m の外）
//   build:NAME  名前のついた建物の中とまわり（箱から 2 m）
//   X,Y,Z       その点のまわり 2 m（find_blocks や記憶の座標）
// 判定は世界から: その範囲にその物のブロックが count 個あるか（前からあったものも数える）。

import vec3Pkg from 'vec3'
import { isHomeCell, buildBox } from './builds.mjs'
import { inHouse } from './home.mjs'

const { Vec3 } = vec3Pkg

const NEAR_HOME_MIN = 3
const NEAR_HOME_MAX = 12
const BUILD_MARGIN = 2
const POINT_RADIUS = 2
const MAX_COUNT = 64

// 置くと形が変わる物（壁につく松明、看板、旗）も数える
export function placedNames (item) {
  const names = new Set([item])
  const wall = item.replace(/_(torch|sign|banner|head|skull|coral_fan|fan)$/, '_wall_$1')
  if (wall !== item) names.add(wall)
  if (item === 'torch') names.add('wall_torch')
  return names
}

// 壁に付けられる物（松明、看板、旗、頭、はしご）。はしごは壁にしか付かない
export function wallMountable (bot, item) {
  if (item === 'ladder') return true
  return [...placedNames(item)].some((n) => n !== item && n.includes('wall') && !!bot.registry?.blocksByName[n])
}

export { INTERACTIVE } from './place-faces.mjs'
import { INTERACTIVE } from './place-faces.mjs'

// 面を借りる隣（against）。on: floor（下）/ wall（横）/ ceiling（上）。省略: 壁に付けられる物は壁を先に
const SIDES = [[1, 0, 0], [-1, 0, 0], [0, 0, 1], [0, 0, -1]]
export function supportFor (bot, pos, item, on = null) {
  const order = on === 'floor'
    ? [[0, -1, 0]]
    : on === 'wall'
      ? SIDES
      : on === 'ceiling'
        ? [[0, 1, 0]]
        : item === 'ladder'
          ? SIDES
          : wallMountable(bot, item) ? [...SIDES, [0, -1, 0], [0, 1, 0]] : [[0, -1, 0], ...SIDES, [0, 1, 0]]
  for (const [x, y, z] of order) {
    const p = pos.offset(x, y, z)
    const b = bot.blockAt(p)
    if (b?.boundingBox === 'block' && !INTERACTIVE.test(b.name)) return p
  }
  return null
}

// 置けるアイテムか（ブロックになる物）
export function isPlaceableItem (bot, item) {
  return !!item && !!bot.registry?.itemsByName[item] && !!bot.registry?.blocksByName[item]
}

export function parseWhere (where, state) {
  const w = String(where ?? '').trim()
  if (w === 'home') {
    if (!state.home) throw new Error('placed(…, home) needs a home: there is none yet (the house is not finished). Use build:<name> or x,y,z, or build the house first')
    return { kind: 'home', label: 'in the home' }
  }
  if (w === 'near_home') {
    if (!state.home) throw new Error('placed(…, near_home) needs a home: there is none yet (the house is not finished). Use build:<name> or x,y,z, or build the house first')
    return { kind: 'near_home', label: 'around the home' }
  }
  const b = /^build:(.+)$/.exec(w)
  if (b) {
    const plan = state.builds?.[b[1].trim()]
    if (!plan?.origin) throw new Error(`there is no build named ${b[1].trim()} yet`)
    return { kind: 'build', name: b[1].trim(), label: `at the build ${b[1].trim()}` }
  }
  const m = /^(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)$/.exec(w)
  if (m) return { kind: 'point', at: new Vec3(+m[1], +m[2], +m[3]), label: `near ${m[1]},${m[2]},${m[3]}` }
  throw new Error('where must be home, near_home, build:<name> or x,y,z')
}

export function whereKey (where) {
  return where.kind === 'point' ? `${where.at.x},${where.at.y},${where.at.z}` : where.kind === 'build' ? `build:${where.name}` : where.kind
}

export function checkPlacedSpec (bot, state, spec) {
  if (!isPlaceableItem(bot, spec.item)) throw new Error(`placed needs an item that is a block when placed (torch, lantern, oak_planks, …), got ${spec.item}`)
  const count = spec.count == null ? 1 : Number(spec.count)
  if (!Number.isInteger(count) || count < 1 || count > MAX_COUNT) throw new Error(`count must be 1-${MAX_COUNT}`)
  const where = parseWhere(spec.where, state)
  return { predicate: 'placed', item: spec.item, where: whereKey(where), count }
}

// 範囲のセル（y も含む）
function* region (state, where) {
  if (where.kind === 'home') {
    const h = state.home
    for (let x = h.min.x; x <= h.max.x; x++) {
      for (let z = h.min.z; z <= h.max.z; z++) {
        if (!isHomeCell(h, { x, z })) continue
        for (let dy = 0; dy <= 2; dy++) yield new Vec3(x, h.min.y + dy, z)
      }
    }
    return
  }
  if (where.kind === 'near_home') {
    const h = state.home
    const cx = (h.min.x + h.max.x) / 2
    const cz = (h.min.z + h.max.z) / 2
    for (let x = Math.floor(cx - NEAR_HOME_MAX); x <= Math.ceil(cx + NEAR_HOME_MAX); x++) {
      for (let z = Math.floor(cz - NEAR_HOME_MAX); z <= Math.ceil(cz + NEAR_HOME_MAX); z++) {
        const d = Math.hypot(x - cx, z - cz)
        if (d < NEAR_HOME_MIN || d > NEAR_HOME_MAX) continue
        for (let dy = -2; dy <= 3; dy++) yield new Vec3(x, h.min.y + dy, z)
      }
    }
    return
  }
  if (where.kind === 'build') {
    const box = buildBox(state.builds[where.name])
    for (let x = box.min.x - BUILD_MARGIN; x <= box.max.x + BUILD_MARGIN; x++) {
      for (let z = box.min.z - BUILD_MARGIN; z <= box.max.z + BUILD_MARGIN; z++) {
        for (let y = box.min.y - 1; y <= box.max.y; y++) yield new Vec3(x, y, z)
      }
    }
    return
  }
  const a = where.at
  for (let x = a.x - POINT_RADIUS; x <= a.x + POINT_RADIUS; x++) {
    for (let z = a.z - POINT_RADIUS; z <= a.z + POINT_RADIUS; z++) {
      for (let y = a.y - POINT_RADIUS; y <= a.y + POINT_RADIUS; y++) yield new Vec3(x, y, z)
    }
  }
}

// 範囲にあるその物の数
export function countPlaced (bot, state, whereText, item) {
  const where = parseWhere(whereText, state)
  const names = placedNames(item)
  let n = 0
  for (const p of region(state, where)) if (names.has(bot.blockAt(p)?.name)) n++
  return n
}

// 置ける所 [{ pos, against }]（空気のセルと、面を借りる隣）。壁に付けられる物（松明など）は壁に付ける
// 所を先に: 家の中なら目の高さ（床 +1）の壁、ほかは横に固いブロックのある所。床に置く所は下が固い
// ブロック（家の中は床の高さだけ）。家・建物の壁や屋根の上、家のドアのすぐ内側（通り道）には置かない。
// 同じ種類の中は近い順
export function placeSpots (bot, state, whereText, limit = 3, item = null) {
  const where = parseWhere(whereText, state)
  const me = bot.entity.position
  const home = state.home
  const wall = !!item && wallMountable(bot, item)
  const walls = []
  const floors = []
  for (const p of region(state, where)) {
    if (bot.blockAt(p)?.name !== 'air') continue
    if (me && p.floored?.().equals(me.floored())) continue
    if (where.kind === 'home') {
      if (home.inside && p.x === home.inside.x && p.z === home.inside.z && p.y - home.inside.y <= 1) continue
    } else if (inHouse(state, p, 0)) continue
    const onFloor = where.kind !== 'home' || p.y === home.min.y
    if (wall && (where.kind !== 'home' || p.y === home.min.y + 1)) {
      const against = supportFor(bot, p, item, 'wall')
      if (against) walls.push({ pos: p, against })
    }
    if (onFloor && item !== 'ladder') {
      const below = p.offset(0, -1, 0)
      const b = bot.blockAt(below)
      if (b?.boundingBox === 'block' && !INTERACTIVE.test(b.name)) floors.push({ pos: p, against: below })
    }
  }
  const near = (a, b) => a.pos.distanceTo(me) - b.pos.distanceTo(me)
  return [...walls.sort(near), ...floors.sort(near)].slice(0, limit)
}

export function describeWhere (whereText, state) {
  try {
    return parseWhere(whereText, state).label
  } catch {
    return String(whereText)
  }
}
