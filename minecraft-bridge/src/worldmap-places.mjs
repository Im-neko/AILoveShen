// 視界の地図（worldmap.mjs）から、覚えている場所を引く（docs/design/59_world_map.md §4）。
//
// - mapFind: ある種類のブロックを、地図から近い順に（find_blocks が、読み込まれていない所や半径の外を
//   補う）
// - mapPlaces: memory.places と同じ形（種類 -> 16x16 の区域ごとの代表 1 点）を地図から作る。前は
//   afterRun で見えた 4 系統だけを覚えていたが、地図は見たものすべてを持つ
// - memoryView: 動物は memory.places のまま、ブロックは地図から
//
// 行けと言われて掘れない場所を出さないため、記録するときにしていた除外（家・建物、水をせき止める物、
// 木でない原木）を、引くときにする。地図は見えた面しか持たないので、木の判定は未知を偽とする

import { protectedReason } from './home.mjs'
import { isNaturalLogName, logClusterIsTree } from './trees.mjs'
import { REMEMBERED_ANIMALS, REMEMBERED_BLOCK, REMEMBERED_STONE, recallableKinds } from './memory.mjs'
import { AIR, TICKS_PER_MINUTE, minuteOf, minutesSince } from './worldmap.mjs'
import { UNDERGROUND_BELOW } from './observe.mjs'
import { caveOre, mineFromHome } from './mining.mjs'

export const PLACES_RADIUS = 192
const MAP_FIND_RADIUS = 96
const CACHE_MS = 10 * 1000
const CACHE_MOVE = 8
const SLOW_MS = 100 // これより遅い引き方は、ブリッジの物理の tick を遅らせるので記録する
// 種類を決めずに引く（search_memory）とき出さないもの: どこにでもある地形、葉、草花、水と溶岩。出すと
// 種類の数が数十増え、検索が葉や草にも当たる。探すものを名指しすれば（kinds）、これらも引ける
const NOISE = /_leaves$|_tulip$|^potted_|^(stone|deepslate|dirt|coarse_dirt|rooted_dirt|grass_block|podzol|mycelium|bedrock|gravel|andesite|granite|diorite|tuff|calcite|snow|snow_block|powder_snow|water|lava|short_grass|tall_grass|fern|large_fern|dead_bush|vine|seagrass|tall_seagrass|kelp|kelp_plant|lily_pad|dandelion|poppy|blue_orchid|allium|azure_bluet|oxeye_daisy|cornflower|lily_of_the_valley|sunflower|lilac|rose_bush|peony|brown_mushroom|red_mushroom|moss_block|moss_carpet|dripstone_block|pointed_dripstone|cobweb|sculk|sculk_vein|glow_lichen)$/
const noiseCache = new WeakMap()
const noiseCells = (cls) => {
  if (!noiseCache.has(cls)) noiseCache.set(cls, cls.cellsOf(NOISE))
  return noiseCache.get(cls)
}

// 観察の要約とソルバーが引く種類: 原木、石炭・鉄鉱石、作物、石（前に覚えていたものと同じ）
const summaryCache = new WeakMap()
export function summaryKinds (cls) {
  if (!summaryCache.has(cls)) summaryCache.set(cls, [...cls.cellsOf(REMEMBERED_BLOCK), ...cls.cellsOf(REMEMBERED_STONE)].map((c) => cls.nameOf(c)))
  return summaryCache.get(cls)
}

const nameAtOf = (map, cls) => (x, y, z) => {
  const c = map.cellAt(x, y, z)
  return c === 0 ? null : cls.nameOf(c)
}

// 水や溶岩に接した固いブロック: 掘ると流れ出す（liquid.mjs と同じ考えで、地図の近所を見る）
function holdsBackLiquid (map, cls, x, y, z) {
  return [[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, 0, 1], [0, 0, -1]].some(([dx, dy, dz]) => {
    const name = nameAtOf(map, cls)(x + dx, y + dy, z + dz)
    return name === 'water' || name === 'lava'
  })
}

// この地図のセルを、行き先にしてよいか（false なら出さない）
export function usable (state, x, y, z, cell) {
  const { worldMap: map, mapCells: cls } = state
  const name = cls.nameOf(cell)
  if (!name || name === 'air') return false
  if (protectedReason(state, { x, y, z }, 0, null)) return false
  if (cls.isSolid(cell) && holdsBackLiquid(map, cls, x, y, z)) return false
  if (name.endsWith('_log')) {
    if (!isNaturalLogName(name)) return false
    const mine = state.placed?.[`${x},${y},${z}`]?.name === name
    if (!mine && !logClusterIsTree(nameAtOf(map, cls), x, y, z, false).tree) return false
  }
  return true
}

// name の種類のブロックを、from に近い順に。読み込まれていて実物が違うものは出さず、地図を直す
// （記録を使う前に、実物を読み直す）。live(x, y, z) は実物の名前（読み込まれていなければ undefined）
export function mapFind (state, name, from, radius = MAP_FIND_RADIUS, { limit = 16, ticksNow = 0, live = () => undefined } = {}) {
  const { worldMap: map, mapCells: cls } = state
  const cell = cls.cellOf(name)
  if (cell === null || !state.worldMap) return []
  const now = minuteOf(ticksNow)
  const out = []
  for (const r of map.find(new Set([cell]), from, radius, { limit: limit * 4, exposed: true })) {
    if (out.length >= limit) break
    const actual = live(r.x, r.y, r.z)
    if (actual !== undefined && actual !== name) {
      const seen = actual === 'cave_air' || actual === 'void_air' ? AIR : cls.cellOf(actual)
      if (seen !== null) map.set(r.x, r.y, r.z, seen, now)
      continue
    }
    if (!usable(state, r.x, r.y, r.z, cell)) continue
    out.push({ x: r.x, y: r.y, z: r.z, distance_m: Math.round(Math.sqrt(r.d2) * 10) / 10, minutes_ago: minutesSince(now, r.minute) })
  }
  return out
}

const cache = new WeakMap() // state -> Map(kindsKey -> { at, from, places })

// kinds（名前の配列）の場所を、memory.places と同じ形で: { kind: [{ region, x, y, z, count, seen }] }。
// kinds が null なら、地形以外のすべての種類。数秒のあいだ、近くにいる間は同じ結果を返す（観察のたびに引く）
export function mapPlaces (state, me, ticksNow, kinds = null) {
  const map = state.worldMap
  if (!map) return {}
  const cls = state.mapCells
  const key = kinds ? [...kinds].sort().join(',') : '*'
  const bucket = cache.get(state) ?? cache.set(state, new Map()).get(state)
  const hit = bucket.get(key)
  const now = Date.now()
  if (hit && now - hit.at < CACHE_MS && Math.hypot(me.x - hit.from.x, me.z - hit.from.z) < CACHE_MOVE) return hit.places
  const started = performance.now()
  const cells = kinds ? new Set(kinds.map((k) => cls.cellOf(k)).filter((c) => c !== null && c !== AIR)) : null
  const places = {}
  if (!cells || cells.size) {
    const reps = map.regionReps(cells, me, PLACES_RADIUS, {
      skip: (x, y, z, cell) => !usable(state, x, y, z, cell),
      exclude: cells ? null : noiseCells(cls)
    })
    const nowMinute = minuteOf(ticksNow)
    for (const [cell, regions] of reps) {
      const list = [...regions.values()].map((r) => ({
        region: r.region, x: r.x, y: r.y, z: r.z, count: r.count,
        seen: ticksNow - minutesSince(nowMinute, r.minute) * TICKS_PER_MINUTE
      }))
      if (list.length) places[cls.nameOf(cell)] = list
    }
  }
  bucket.set(key, { at: now, from: { x: me.x, z: me.z }, places })
  const took = performance.now() - started
  if (took > SLOW_MS) console.log(`[worldmap] 場所を引くのに ${Math.round(took)} ms（${key.length > 60 ? `${key.slice(0, 60)}…` : key}、断面 ${map.sectionCount}）`)
  return places
}

// 場所を引くときの memory: 動物は memory.places のまま、ブロックは地図から。kinds: 探す種類（null で全部）
export function memoryView (state, me, ticksNow, kinds = null) {
  const memory = state.memory
  if (!state.worldMap) return mineFromHome(state) ? { ...memory, places: withoutCaveOre(state, memory.places ?? {}) } : memory
  const animals = Object.fromEntries(Object.entries(memory.places ?? {}).filter(([k]) => REMEMBERED_ANIMALS.has(k)))
  return { ...memory, places: withoutCaveOre(state, { ...animals, ...mapPlaces(state, me, ticksNow, kinds) }) }
}

// 洞窟の深い鉱石は、覚えている場所に出さない（出すと、LLM が見に行く。取らない物へは行かせない: 2026-10-07、
// 覚えていた洞窟の鉄鉱石へ歩いて、自然の洞窟で鉄を掘った）
function withoutCaveOre (state, places) {
  return Object.fromEntries(Object.entries(places)
    .map(([kind, list]) => [kind, list.filter((p) => !caveOre(state, p, kind))])
    .filter(([, list]) => list.length))
}

// ソルバーの remembered: 種類ごとに、行く価値のある場所があるか（has だけ）。地図は要約の種類の分を 1 回だけ
// 引く（種類ごとに引くと、原木が 8 種類あるだけで全部の断面を 8 回なめる）
// 地下で見た場所は、戻る価値のある所に数えない（水は洞窟の水に行かない。ほかは松明があるときだけ候補に出る:
// candidates.mjs の recallTripOk）
const onSurface = (p) => !(p.y < UNDERGROUND_BELOW)

export const rememberedKinds = (state, me, ticksNow) => {
  let kinds = null
  return {
    has: (kind) => (kinds ??= recallableKinds(memoryView(state, me, ticksNow, summaryKinds(state.mapCells)), me, ticksNow, onSurface)).has(kind)
  }
}
