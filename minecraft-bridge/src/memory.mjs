// ワールドの記憶: 前に見て、いまは見えていないもの（docs/design/14_world_memory.md §4）。
//
// いつでも読めるもの（インベントリ、体力、目標の進み具合）はここに写さない。前に見た場所だけを、
// 最後に見た時刻（ワールドの経過ティック）とともに持つ:
// - places: 種類（ブロック名か動物名）ごとに、16x16 の区域1つにつき数つきの項目1つ
// - explored: 各区域を最後に訪れた時刻
// - deaths: ボットが死んだ場所
// - chests: 各チェストを最後に開けたときの中身（使うのはボットだけなので、次に開けて上書き
//   するまでこの記録が正しい）
// - furnaces: 各かまどを最後に開けたときに作っていたもの（できたものとこれからできるもの）
// ボットの近くにあるはずの場所がいま見えていなければ忘れる（掘り尽くした、歩いて去った、または
// 最初からなかった）。同じ場所へ何度も行かないようにするため。

export const REGION = 16
export const MAX_PLACES_PER_KIND = 8
export const ANIMAL_STALE_TICKS = 24000 // ゲーム内の1日: 動物は歩き回る
export const FORGET_WITHIN = 20 // この距離内で覚えていて、いま見えていない場所はもうない
export const RECALL_BEYOND = 32 // これより近いものは見えている: 候補が直接出す
const TICKS_PER_MINUTE = 1200
const MAX_DEATHS = 3
const RECALLED_OFFERED = 3
const SUMMARY_PLACES = 10

export const REMEMBERED_BLOCK = /^(?!stripped_).*_log$|^(deepslate_)?(coal|iron)_ore$/
export const REMEMBERED_ANIMALS = new Set(['cow', 'pig', 'sheep', 'chicken'])

export function newMemory () {
  return { places: {}, explored: {}, deaths: [], chests: {}, furnaces: {} }
}

export const regionOf = (p) => `${Math.floor(p.x / REGION)},${Math.floor(p.z / REGION)}`
const flatDistance = (a, b) => Math.hypot(a.x - b.x, a.z - b.z)

// `me` のまわりでいま見たもの: sightings は [{ kind, pos }]
export function remember (memory, sightings, me, now) {
  const seen = {}
  for (const { kind, pos } of sightings) {
    const regions = (seen[kind] ??= {})
    const r = regionOf(pos)
    if (regions[r]) regions[r].count++
    else regions[r] = { x: Math.floor(pos.x), y: Math.floor(pos.y), z: Math.floor(pos.z), count: 1 }
  }
  for (const kind of Object.keys(memory.places)) {
    memory.places[kind] = memory.places[kind].filter((p) => seen[kind]?.[p.region] || flatDistance(p, me) > FORGET_WITHIN)
    if (!memory.places[kind].length) delete memory.places[kind]
  }
  for (const [kind, regions] of Object.entries(seen)) {
    const places = (memory.places[kind] ??= [])
    for (const [region, s] of Object.entries(regions)) {
      const old = places.find((p) => p.region === region)
      if (old) Object.assign(old, s, { seen: now })
      else places.push({ region, ...s, seen: now })
    }
    places.sort((a, b) => b.seen - a.seen)
    places.splice(MAX_PLACES_PER_KIND)
  }
  memory.explored[regionOf(me)] = now
}

export function rememberDeath (memory, pos, now) {
  memory.deaths.unshift({ x: Math.floor(pos.x), y: Math.floor(pos.y), z: Math.floor(pos.z), seen: now })
  memory.deaths.splice(MAX_DEATHS)
}

const stale = (kind, place, now) => REMEMBERED_ANIMALS.has(kind) && now - place.seen > ANIMAL_STALE_TICKS

const worthATrip = (kind, p, me, now) => !stale(kind, p, now) && flatDistance(p, me) > RECALL_BEYOND

// これらの種類のうち、見えていなくて行く価値がまだある場所。近い順
export function recall (memory, kinds, me, now) {
  return kinds.flatMap((kind) => (memory.places[kind] ?? [])
    .filter((p) => worthATrip(kind, p, me, now))
    .map((p) => ({ kind, ...p, distance: Math.round(flatDistance(p, me)), minutesAgo: Math.round((now - p.seen) / TICKS_PER_MINUTE) })))
    .sort((a, b) => a.distance - b.distance)
    .slice(0, RECALLED_OFFERED)
}

// 行く価値のある場所がある種類（ソルバーはあてのない探索よりこちらを優先する）
export function recallableKinds (memory, me, now) {
  return new Set(Object.entries(memory.places ?? {})
    .filter(([kind, list]) => list.some((p) => worthATrip(kind, p, me, now)))
    .map(([kind]) => kind))
}

const chestKey = (p) => `${p.x},${p.y},${p.z}`

export function rememberChest (memory, pos, contents, now) {
  memory.chests[chestKey(pos)] = { x: pos.x, y: pos.y, z: pos.z, contents, seen: now }
}

export function forgetChest (memory, pos) {
  delete memory.chests[chestKey(pos)]
}

export const chests = (memory) => Object.values(memory.chests ?? {})

// すべてのチェストの中身。アイテム名ごと
export function storedCounts (memory) {
  const out = {}
  for (const c of chests(memory)) {
    for (const [name, n] of Object.entries(c.contents)) out[name] = (out[name] ?? 0) + n
  }
  return out
}

// names のどれかが入っている一番近いチェスト
export function chestWith (memory, names, me) {
  return chests(memory)
    .filter((c) => names.some((n) => (c.contents[n] ?? 0) > 0))
    .sort((a, b) => flatDistance(a, me) - flatDistance(b, me))[0] ?? null
}

// making: {product: count} できたものとこれからできるもの（燃料は材料と一緒に入れる）
export function rememberFurnace (memory, pos, making, now) {
  memory.furnaces ??= {}
  if (Object.values(making).some((n) => n > 0)) memory.furnaces[chestKey(pos)] = { x: pos.x, y: pos.y, z: pos.z, making, seen: now }
  else delete memory.furnaces[chestKey(pos)]
}

export function forgetFurnace (memory, pos) {
  delete memory.furnaces?.[chestKey(pos)]
}

// item を作っている一番近いかまど
export function furnaceWith (memory, item, me) {
  return Object.values(memory.furnaces ?? {})
    .filter((f) => (f.making[item] ?? 0) > 0)
    .sort((a, b) => flatDistance(a, me) - flatDistance(b, me))[0] ?? null
}

// かまどが作っているもの。できるものごと
export function smeltingCounts (memory) {
  const out = {}
  for (const f of Object.values(memory.furnaces ?? {})) {
    for (const [name, n] of Object.entries(f.making)) out[name] = (out[name] ?? 0) + n
  }
  return out
}

export const visited = (memory, pos) => memory.explored[regionOf(pos)] !== undefined

// 判断する側が見るもの: 種類ごとの一番近い場所と、ボットが死んだ場所
export function summarizeMemory (memory, me, now, bearing) {
  const places = Object.entries(memory.places).flatMap(([kind, list]) => {
    const fresh = list.filter((p) => !stale(kind, p, now))
    if (!fresh.length) return []
    const p = fresh.reduce((a, b) => (flatDistance(a, me) <= flatDistance(b, me) ? a : b))
    return [{ kind, count: p.count, direction: bearing(me, p), distance_m: Math.round(flatDistance(p, me)), minutes_ago: Math.round((now - p.seen) / TICKS_PER_MINUTE) }]
  }).sort((a, b) => a.distance_m - b.distance_m).slice(0, SUMMARY_PLACES)
  return {
    places,
    chests: chests(memory).map((c) => ({ direction: bearing(me, c), distance_m: Math.round(flatDistance(c, me)), contents: c.contents, minutes_ago: Math.round((now - c.seen) / TICKS_PER_MINUTE) })),
    explored_regions: Object.keys(memory.explored).length,
    deaths: memory.deaths.map((d) => ({ direction: bearing(me, d), distance_m: Math.round(flatDistance(d, me)), minutes_ago: Math.round((now - d.seen) / TICKS_PER_MINUTE) }))
  }
}
