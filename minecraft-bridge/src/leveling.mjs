// 整地（設計書 41）: leveled(where, distance)。
//
// where のまわり半径 distance の地面を、決まった高さにそろえる。高さは足の高さ（そこに立つ高さ）:
// home なら家の床の上（home.inside.y）、x,y,z ならその y（その場所に立ったときの足の高さ）。地面の
// いちばん上の固いブロックが y-1 になっていればそろっている。
//   - 高い列（出っ張り）: y 以上にある自然の地形（TERRAIN）を掘る
//   - 低い列（穴）: 地面の上から y-1 まで土で埋める
// 触らない列: 家・前の家・建物とそのまわり 1 マス（inHouse）。ただし家の床と同じ高さに整地するなら、壁のすぐ外は整地する、植えた木のまわり、畑（耕地と作物）、
// 木（原木・葉）、水・溶岩、自然でないブロック（家具、置いたもの）が y 以上にある列、DY より深い穴。
// 見えていない列はそろっていないと数える（lit と同じ: 見えない所をそろったとはみなさない）。

import vec3Pkg from 'vec3'
import { inHouse, isDoorstepFloor } from './home.mjs'
import { onMineCell } from './facilities.mjs'
import { TILLABLE } from './farming.mjs'
import { onShaftTop } from './quarry.mjs'

const { Vec3 } = vec3Pkg
export const MIN_LEVEL = 3
export const MAX_LEVEL = 16
const DY = 8 // 目標の高さからこれだけ上下まで見る（これより深い穴・高い崖は触らない）
const PLANTING_GAP = 1 // 植えた木のまわりのこのマス数は触らない

// 整地する（した）土地の下は、土や石を取るために掘らない（掘ると、そろえた地面の下が穴だらけ（スポンジ）に
// なって、埋めた土がまた足りなくなる）。土は整地の外から取る。床の高さより上の出っ張りは、整地で掘るものなので
// 掘ってよい。zones は 2 つから: wanted = 中目標にある leveled（Python が目標のたびに送る。まだ整地していない
// 土地も守る）、done = 整地を始めた土地（ボット自身の記録。目標が終わった後も守る）
const ZONE_MARGIN = 1 // 整地の半径のこのマス数外まで守る
const MAX_ZONES = 8

// 自然の地形（掘ってよい。歩きながら掘るのもこれと葉だけ: primitives.mjs の configureMovements）
export const TERRAIN = /^(dirt|grass_block|coarse_dirt|podzol|rooted_dirt|mycelium|mud|clay|sand|red_sand|gravel|snow|snow_block|stone|granite|diorite|andesite|deepslate|tuff|calcite|dripstone_block|sandstone|red_sandstone|terracotta|(white|orange|yellow|red|brown|light_gray)_terracotta|netherrack|moss_block|\w+_ore|dirt_path)$/
const TREE = /_(log|leaves|wood)$|^(mangrove_roots|bamboo)$/
const FLUID = /water|lava|bubble_column/
const FARM = /^farmland$/
// 畑の作物のマスの地面が、耕せない固いブロックに変わっていたら、掘って土で埋め直す。壊してよいのは自然の地形と、整地や
// 足場に置いた丸石（家具や置いた物は壊さない）
const FILLER_STONE = /^(cobblestone|mossy_cobblestone|cobbled_deepslate)$/
export const farmRebuildable = (name) => TERRAIN.test(name ?? '') || FILLER_STONE.test(name ?? '')
// 畑を埋める土: 畑の地面は耕せる土だけ（丸石で埋めると、そのマスは耕せない）
export const SOIL_ITEMS = ['dirt', 'coarse_dirt']
export const soilItem = (bot) => SOIL_ITEMS.map((n) => bot.inventory.items().find((i) => i.name === n)).find(Boolean) ?? null
export const soilCount = (bot) => bot.inventory.items().filter((i) => SOIL_ITEMS.includes(i.name)).reduce((n, i) => n + i.count, 0)
const UNLOADED = 'unloaded'

// 中心と、足の高さ。where: 'home' か 'x,y,z'
export function levelTarget (state, where) {
  if (where === 'home') {
    if (!state.home) throw new Error('there is no home yet')
    const c = state.home.inside
    return { center: new Vec3(c.x, c.y, c.z), y: c.y }
  }
  const m = /^\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*$/.exec(String(where ?? ''))
  if (!m) throw new Error('where must be home or x,y,z (y: the height to stand on, the ground is y-1)')
  const [x, y, z] = m.slice(1).map(Number)
  return { center: new Vec3(x, y, z), y }
}

const isPlant = (b) => b.boundingBox === 'empty' && !FLUID.test(b.name)

// 1 列を見る → { off: bool, cuts: [Vec3], fills: [Vec3] } か、触らない理由の文字列か UNLOADED
// stairs: false は、階段の入口のまわりも見る（設計した畑のマスは、階段の記録より先に直す）
export function columnWork (bot, state, x, z, y, { stairs = true } = {}) {
  const floor = y - 1
  // ドアの前の床は、同じ高さに整地するなら普通の列（穴を埋める。家のブロックは TERRAIN でないので削らない）
  const doorstep = isDoorstepFloor(state, new Vec3(x, floor, z)) && floor === state.home.outside.y - 1
  // 家の床と同じ高さに整地するなら、壁のすぐ外も整地する（壁の下は足元の土として家の計画が直す）。
  // 別の高さなら、壁のまわり 1 マスは触らない
  const margin = state.home && floor === state.home.inside.y - 1 ? 0 : 1
  if (!doorstep) for (const dy of [0, 1]) if (inHouse(state, new Vec3(x, floor + dy, z), margin)) return 'house or build'
  if ((state.plantings ?? []).some((p) => Math.abs(p.x - x) <= PLANTING_GAP && Math.abs(p.z - z) <= PLANTING_GAP)) return 'planted tree'
  for (let dy = -DY; dy <= DY; dy++) if (onMineCell(state, new Vec3(x, floor + dy, z))) return 'mine'
  if (stairs && onShaftTop(state, x, z)) return 'quarry stairs' // 階段の入口（埋めると、続きから下りられない）
  let top = null
  for (let h = floor + DY; h >= floor - DY; h--) {
    const b = bot.blockAt(new Vec3(x, h, z))
    if (!b) return UNLOADED
    if (FLUID.test(b.name)) return 'water'
    if (TREE.test(b.name)) return 'tree'
    if (FARM.test(b.name)) return 'farmland'
    if (isPlant(b)) continue
    top = b
    break
  }
  if (!top) return 'too deep'
  const t = top.position.y
  if (t === floor) return { off: false, cuts: [], fills: [] }
  if (t > floor) {
    const cuts = []
    for (let h = t; h > floor; h--) {
      const b = bot.blockAt(new Vec3(x, h, z))
      if (isPlant(b) || b.name === 'air') continue
      if (!TERRAIN.test(b.name)) return `${b.name} (not natural ground)`
      cuts.push(b.position)
    }
    return { off: true, cuts, fills: [] }
  }
  const fills = []
  for (let h = t + 1; h <= floor; h++) fills.push(new Vec3(x, h, z))
  return { off: true, cuts: [], fills }
}

// 列の並び [[x, z]] を、立つ高さ y にそろえる → { y, total, off, cuts, fills, unloaded, skipped: {理由: 数} }
// cuts は列ごとに上から、fills は列ごとに下から
export function levelColumns (bot, state, columns, y) {
  const out = { y, total: 0, off: 0, cuts: [], fills: [], unloaded: 0, skipped: {} }
  for (const [x, z] of columns) {
    out.total++
    const w = columnWork(bot, state, x, z, y)
    if (w === UNLOADED) { out.unloaded++; continue }
    if (typeof w === 'string') { out.skipped[w] = (out.skipped[w] ?? 0) + 1; continue }
    if (!w.off) continue
    out.off++
    out.cuts.push(...w.cuts)
    out.fills.push(...w.fills)
  }
  return out
}

// 範囲全体 → { y, center, total, off, cuts, fills, unloaded, skipped: {理由: 数} }
export function levelStatus (bot, state, where, radius) {
  const { center, y } = levelTarget(state, where)
  const columns = []
  for (let dx = -radius; dx <= radius; dx++) {
    for (let dz = -radius; dz <= radius; dz++) {
      if (dx * dx + dz * dz <= radius * radius) columns.push([center.x + dx, center.z + dz])
    }
  }
  return { center, ...levelColumns(bot, state, columns, y) }
}

// 畑の整地（設計書 75）: 畑の四角と、そのまわり FARM_MARGIN マスの列を、畑の地面の高さにそろえる。そろえてから
// 畑を作る（耕す）。まわりは柵の輪（1 マス外）を含み、畑の外を歩く・柵を立てるのに段差がないように（2026-10-04、
// user: 「畑はその周囲 2 マスくらいも畑と同じ高さになるように整地してから畑を作ってほしい」）。耕した所と作物、水、
// 木は触らない（columnWork が飛ばす）ので、耕したあとは数えない。ただし、四角の中で地面が低い耕地（耕して作物が
// ある所）は、作物を掘って、土で埋め上げる（user: 「低い 2 マスも埋めていい」）
export const FARM_MARGIN = 2
export function farmColumns (layout, { margin = FARM_MARGIN } = {}) {
  const { x0, z0, w, d } = layout.rect
  const out = []
  for (let x = x0 - margin; x <= x0 + w - 1 + margin; x++) for (let z = z0 - margin; z <= z0 + d - 1 + margin; z++) out.push([x, z])
  return out
}

// 畑の四角の中の 1 列。耕地が地面の高さ y より低いなら、作物を掘って土で埋め上げる。それ以外は columnWork と同じ
function farmCellWork (bot, state, x, z, y, layout) {
  const base = columnWork(bot, state, x, z, y + 1, { stairs: false })
  if (base !== 'farmland') {
    // 作物のマスの地面が耕せない固いブロック（丸石など）なら、掘って土で埋め直す（高さは合っていても直す）
    if (base?.off === false && layout.cells.some((c) => c.x === x && c.z === z)) {
      const ground = bot.blockAt(new Vec3(x, y, z))
      if (ground?.boundingBox === 'block' && !TILLABLE.test(ground.name) && !FARM.test(ground.name) && farmRebuildable(ground.name)) {
        return { off: true, cuts: [ground.position], fills: [ground.position] }
      }
    }
    return base
  }
  let h = y + DY
  for (; h >= y - DY; h--) {
    const b = bot.blockAt(new Vec3(x, h, z))
    if (!b) return UNLOADED
    if (FARM.test(b.name)) break
  }
  if (h < y - DY) return 'farmland'
  if (h === y) return { off: false, cuts: [], fills: [] }
  if (h > y) return 'farmland above the farm level'
  const cuts = []
  for (let hh = h + 1; hh <= y; hh++) {
    const b = bot.blockAt(new Vec3(x, hh, z))
    if (b && isPlant(b) && b.name !== 'air' && !/air$/.test(b.name)) cuts.push(b.position)
  }
  const fills = []
  for (let hh = h + 1; hh <= y; hh++) fills.push(new Vec3(x, hh, z))
  return { off: true, cuts, fills }
}

export function farmLevel (bot, state, fac, { margin = FARM_MARGIN } = {}) {
  const { rect, y } = fac.layout
  const out = { y: y + 1, total: 0, off: 0, cuts: [], fills: [], unloaded: 0, skipped: {} }
  const inRect = (x, z) => x >= rect.x0 && x < rect.x0 + rect.w && z >= rect.z0 && z < rect.z0 + rect.d
  for (const [x, z] of farmColumns(fac.layout, { margin })) {
    out.total++
    const w = inRect(x, z) ? farmCellWork(bot, state, x, z, y, fac.layout) : columnWork(bot, state, x, z, y + 1)
    if (w === UNLOADED) { out.unloaded++; continue }
    if (typeof w === 'string') { out.skipped[w] = (out.skipped[w] ?? 0) + 1; continue }
    if (!w.off) continue
    out.off++
    out.cuts.push(...w.cuts)
    out.fills.push(...w.fills)
  }
  return { ...out, center: new Vec3(rect.x0 + Math.floor(rect.w / 2), y + 1, rect.z0 + Math.floor(rect.d / 2)) }
}

export function describeLevel (s, radius) {
  const skipped = Object.entries(s.skipped).map(([why, n]) => `${why} ${n}`).join(', ')
  return `ground within ${radius} of ${s.center.x},${s.center.z} at height ${s.y - 1} (stand at y ${s.y}): ` +
    `${s.total - s.off - s.unloaded - Object.values(s.skipped).reduce((a, b) => a + b, 0)} level, ${s.off} off ` +
    `(${s.cuts.length} blocks to dig, ${s.fills.length} to fill)` +
    (s.unloaded ? `, ${s.unloaded} out of view` : '') + (skipped ? `; left as they are: ${skipped}` : '')
}

// 掘るセルのうち、道具が要るのに持っていないもの（石にツルハシ）。道具の名前 → 数
export function toolsMissing (bot, cuts) {
  const held = new Set(bot.inventory.items().map((i) => i.type))
  const out = {}
  for (const p of cuts) {
    const b = bot.blockAt(p)
    const ids = b?.harvestTools ? Object.keys(b.harvestTools).map(Number) : []
    if (!ids.length || ids.some((id) => held.has(id))) continue
    const cheapest = bot.registry.items[Math.min(...ids)]?.name ?? 'a tool'
    out[cheapest] = (out[cheapest] ?? 0) + 1
  }
  return out
}

// 埋めるブロックは、穴の数だけまとめて持ってから始める（2 個ずつ掘っては埋める往復で、1 回の整地に何十回も
// 行き来していた）。1 回で持つのは MAX_FILL_BATCH まで
export const MAX_FILL_BATCH = 64
export const fillNeeded = (s) => Math.min(s.fills.length, MAX_FILL_BATCH)

const wholeNumber = (n) => Number.isInteger(n)

// Python が送る整地の予定地（[{where, distance}]）を、目標のたびに置き換える。読めないものは捨てる
export function setLevelWanted (state, areas) {
  if (!Array.isArray(areas)) return
  const wanted = []
  for (const a of areas.slice(0, MAX_ZONES)) {
    const where = String(a?.where ?? 'home')
    const distance = Number(a?.distance)
    if (!wholeNumber(distance) || distance < MIN_LEVEL || distance > MAX_LEVEL) continue
    try {
      levelTarget(state, where)
    } catch {
      if (where !== 'home') continue // 家がまだない home は、家ができてから効く
    }
    wanted.push({ where, distance })
  }
  state.leveling = { done: [], ...state.leveling, wanted }
}

// 整地を始めた土地を覚える（同じ中心と半径は新しく置き直す）
export function noteLevelZone (state, where, radius) {
  const { center, y } = levelTarget(state, where)
  noteLevelArea(state, { x: center.x, y, z: center.z, radius })
}

// 整地した土地の記録（畑は、四角を覆う円で）
export function noteLevelArea (state, zone) {
  const done = (state.leveling?.done ?? []).filter((z) => !(z.x === zone.x && z.z === zone.z && z.radius === zone.radius))
  done.push(zone)
  state.leveling = { wanted: [], ...state.leveling, done: done.slice(-MAX_ZONES) }
}

// 守る土地: [{ x, y, z, radius }]（y は立つ高さ。地面の上のブロックは y - 1）
export function levelZones (state) {
  const out = [...(state.leveling?.done ?? [])]
  for (const w of state.leveling?.wanted ?? []) {
    try {
      const { center, y } = levelTarget(state, w.where)
      out.push({ x: center.x, y, z: center.z, radius: w.distance })
    } catch {
      // 家がまだない
    }
  }
  return out
}

// 整地する土地の、床の高さ以下にある自然の地形か（土を取るために掘らない所）
export function keptGround (state, p, name) {
  if (!TERRAIN.test(name ?? '')) return false
  return levelZones(state).some((z) => p.y <= z.y - 1 && Math.hypot(p.x - z.x, p.z - z.z) <= z.radius + ZONE_MARGIN)
}

// 掘れないブロックが近くに多いと、近い順に 64 個では掘れるものが残らない: 整地の土地があるときは多めに探す
export const findCount = (state, base = 64) => levelZones(state).length ? base * 8 : base

export const KEPT_REMEDY = 'the ground there is being leveled (or was): take dirt and stone from outside that area (the bumps above its floor are fine)'
