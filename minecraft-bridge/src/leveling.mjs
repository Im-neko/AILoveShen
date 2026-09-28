// 整地（設計書 41）: leveled(where, distance)。
//
// where のまわり半径 distance の地面を、決まった高さにそろえる。高さは足の高さ（そこに立つ高さ）:
// home なら家の床の上（home.inside.y）、x,y,z ならその y（その場所に立ったときの足の高さ）。地面の
// いちばん上の固いブロックが y-1 になっていればそろっている。
//   - 高い列（出っ張り）: y 以上にある自然の地形（TERRAIN）を掘る
//   - 低い列（穴）: 地面の上から y-1 まで土で埋める
// 触らない列: 家・前の家・建物とそのまわり 1 マス（inHouse）、植えた木のまわり、畑（耕地と作物）、
// 木（原木・葉）、水・溶岩、自然でないブロック（家具、置いたもの）が y 以上にある列、DY より深い穴。
// 見えていない列はそろっていないと数える（lit と同じ: 見えない所をそろったとはみなさない）。

import vec3Pkg from 'vec3'
import { inHouse } from './home.mjs'
import { onMineCell } from './facilities.mjs'

const { Vec3 } = vec3Pkg
export const MIN_LEVEL = 3
export const MAX_LEVEL = 16
const DY = 8 // 目標の高さからこれだけ上下まで見る（これより深い穴・高い崖は触らない）
const PLANTING_GAP = 1 // 植えた木のまわりのこのマス数は触らない

// 自然の地形（掘ってよい。歩きながら掘るのもこれと葉だけ: primitives.mjs の configureMovements）
export const TERRAIN = /^(dirt|grass_block|coarse_dirt|podzol|rooted_dirt|mycelium|mud|clay|sand|red_sand|gravel|snow|snow_block|stone|granite|diorite|andesite|deepslate|tuff|calcite|dripstone_block|sandstone|red_sandstone|terracotta|(white|orange|yellow|red|brown|light_gray)_terracotta|netherrack|moss_block|\w+_ore|dirt_path)$/
const TREE = /_(log|leaves|wood)$|^(mangrove_roots|bamboo)$/
const FLUID = /water|lava|bubble_column/
const FARM = /^farmland$/
// 埋めるのに使うブロック（持っているもの）: 土を先に
export const FILL_ITEMS = ['dirt', 'coarse_dirt', 'cobblestone', 'cobbled_deepslate']
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
export function columnWork (bot, state, x, z, y) {
  const floor = y - 1
  for (const dy of [0, 1]) if (inHouse(state, new Vec3(x, floor + dy, z), 1)) return 'house or build'
  if ((state.plantings ?? []).some((p) => Math.abs(p.x - x) <= PLANTING_GAP && Math.abs(p.z - z) <= PLANTING_GAP)) return 'planted tree'
  for (let dy = -DY; dy <= DY; dy++) if (onMineCell(state, new Vec3(x, floor + dy, z))) return 'mine'
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

// 範囲全体 → { y, center, total, off, cuts, fills, unloaded, skipped: {理由: 数} }
// cuts は列ごとに上から、fills は列ごとに下から
export function levelStatus (bot, state, where, radius) {
  const { center, y } = levelTarget(state, where)
  const out = { y, center, total: 0, off: 0, cuts: [], fills: [], unloaded: 0, skipped: {} }
  for (let dx = -radius; dx <= radius; dx++) {
    for (let dz = -radius; dz <= radius; dz++) {
      if (dx * dx + dz * dz > radius * radius) continue
      out.total++
      const w = columnWork(bot, state, center.x + dx, center.z + dz, y)
      if (w === UNLOADED) { out.unloaded++; continue }
      if (typeof w === 'string') { out.skipped[w] = (out.skipped[w] ?? 0) + 1; continue }
      if (!w.off) continue
      out.off++
      out.cuts.push(...w.cuts)
      out.fills.push(...w.fills)
    }
  }
  return out
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

export function fillItem (bot) {
  const items = bot.inventory.items()
  for (const name of FILL_ITEMS) {
    const item = items.find((i) => i.name === name)
    if (item) return item
  }
  return null
}
