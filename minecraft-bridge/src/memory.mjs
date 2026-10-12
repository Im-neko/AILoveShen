// ワールドの記憶: 前に見て、いまは見えていないもの（docs/design/14_world_memory.md §4）。
//
// いつでも読めるもの（インベントリ、体力、目標の進み具合）はここに写さない。前に見た場所だけを、
// 最後に見た時刻（ワールドの経過ティック）とともに持つ:
// - places: 種類（ブロック名か動物名）ごとに、16x16 の区域1つにつき数つきの項目1つ
// - explored: 各区域を最後に訪れた時刻
// - biomes: 各区域で最後に立った所のバイオーム名（地上だけ）。羊は草原、など探す方向の手がかり
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

// 畑の作物も覚える（離れていても畑へ戻れる。熟しているかは行って確かめる）
export const REMEMBERED_BLOCK = /^(?!stripped_).*_log$|^(deepslate_)?(coal|iron)_ore$|^(wheat|carrots|potatoes|beetroots)$/
// 石: 掘り下げた階段の底や崖で見えた所に、次に石が要るときに戻る（docs/design/17_dig_down.md）。
// 地表のどこにでもあるので、ほかのものとは別に数える
export const REMEMBERED_STONE = /^(stone|deepslate)$/
export const REMEMBERED_ANIMALS = new Set(['cow', 'pig', 'sheep', 'chicken'])

export function newMemory () {
  return { places: {}, explored: {}, biomes: {}, deaths: [], chests: {}, furnaces: {}, sites: {} }
}

export const regionOf = (p) => `${Math.floor(p.x / REGION)},${Math.floor(p.z / REGION)}`
const flatDistance = (a, b) => Math.hypot(a.x - b.x, a.z - b.z)

// `me` のまわりでいま見たもの: sightings は [{ kind, pos }]。biome は今立っている所のバイオーム名（地下や読めないときは null）
export function remember (memory, sightings, me, now, biome = null) {
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
  if (biome) (memory.biomes ??= {})[regionOf(me)] = biome
}

export function rememberDeath (memory, pos, now) {
  memory.deaths.unshift({ x: Math.floor(pos.x), y: Math.floor(pos.y), z: Math.floor(pos.z), seen: now })
  memory.deaths.splice(MAX_DEATHS)
}

// 死んで落とした持ち物は 5 分で消える: 余裕を見て 4 分半まで取りに戻る。2 回行って取れなければ諦める
const DEATH_ITEMS_TICKS = 4.5 * TICKS_PER_MINUTE
const RECOVER_TRIES = 2

// 取りに戻れる直近の死（なければ null）
export function recoverableDeath (memory, now) {
  const d = memory?.deaths?.[0]
  if (!d || d.recovered || (d.tries ?? 0) >= RECOVER_TRIES || now - d.seen > DEATH_ITEMS_TICKS) return null
  return d
}

const stale = (kind, place, now) => REMEMBERED_ANIMALS.has(kind) && now - place.seen > ANIMAL_STALE_TICKS

const worthATrip = (kind, p, me, now) => !stale(kind, p, now) && flatDistance(p, me) > RECALL_BEYOND

// これらの種類のうち、見えていなくて行く価値がまだある場所（allowed が偽のものは除く）。近い順
export function recall (memory, kinds, me, now, allowed = () => true) {
  return kinds.flatMap((kind) => (memory.places[kind] ?? [])
    .filter((p) => worthATrip(kind, p, me, now))
    .map((p) => ({ kind, ...p, distance: Math.round(flatDistance(p, me)), minutesAgo: Math.round((now - p.seen) / TICKS_PER_MINUTE) })))
    .filter(allowed)
    .sort((a, b) => a.distance - b.distance)
    .slice(0, RECALLED_OFFERED)
}

// 行く価値のある場所がある種類（ソルバーはあてのない探索よりこちらを優先する）
export function recallableKinds (memory, me, now, allowed = () => true) {
  return new Set(Object.entries(memory.places ?? {})
    .filter(([kind, list]) => list.some((p) => worthATrip(kind, p, me, now) && allowed(p)))
    .map(([kind]) => kind))
}

const chestKey = (p) => `${p.x},${p.y},${p.z}`

// slots: { used, size }（使っているスロットの数と、チェストのスロットの数。ラージチェストは 54）。開いたときだけ分かる
export function rememberChest (memory, pos, contents, now, slots = null) {
  memory.chests[chestKey(pos)] = { x: pos.x, y: pos.y, z: pos.z, contents, seen: now, ...(slots ? { slots } : {}) }
}

export function forgetChest (memory, pos) {
  delete memory.chests[chestKey(pos)]
}

export const chests = (memory) => Object.values(memory.chests ?? {})

// すべてのチェストの中身。アイテム名ごと
// 家の中のチェスト（街のチェスト）。前の家のチェストは入らない
export function homeChests (memory, home) {
  if (!home) return []
  return chests(memory).filter((c) => c.x >= home.min.x - 1 && c.x <= home.max.x + 1 && c.z >= home.min.z - 1 &&
    c.z <= home.max.z + 1 && c.y >= home.min.y - 1 && c.y <= home.min.y + 3)
}

// チェストの中身の合計。home を渡すと家の中のチェストだけ（stored の判定）、渡さなければ全部
// （取り出す元。引っ越した後も前の家から運べる）
export function storedCounts (memory, home = null) {
  const out = {}
  for (const c of home ? homeChests(memory, home) : chests(memory)) {
    for (const [name, n] of Object.entries(c.contents)) out[name] = (out[name] ?? 0) + n
  }
  return out
}

// 調べた候補地の数字（survey.mjs）。数字だけで、判断は入れない
export function rememberSite (memory, numbers, now) {
  memory.sites ??= {}
  memory.sites[numbers.id] = { ...numbers, seen: now }
}

// names のどれかが入っている一番近いチェスト
export function chestWith (memory, names, me, except = []) {
  return chests(memory)
    .filter((c) => !except.includes(c) && names.some((n) => (c.contents[n] ?? 0) > 0))
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

// item を作っている一番近いかまど（accept: 取りに行ってよいかまどか。家のかまど以外へは行かない）
export function furnaceWith (memory, item, me, accept = () => true) {
  return Object.values(memory.furnaces ?? {})
    .filter((f) => (f.making[item] ?? 0) > 0 && accept(f))
    .sort((a, b) => flatDistance(a, me) - flatDistance(b, me))[0] ?? null
}

// かまどが作っているもの。できるものごと
export function smeltingCounts (memory, accept = () => true) {
  const out = {}
  for (const f of Object.values(memory.furnaces ?? {})) {
    if (!accept(f)) continue
    for (const [name, n] of Object.entries(f.making)) out[name] = (out[name] ?? 0) + n
  }
  return out
}

export const visited = (memory, pos) => memory.explored[regionOf(pos)] !== undefined

// 見たことのある所: 行った区域か、その隣（まわりは 16〜32 m 先まで見えている）
const known = (memory, x, z) => [-1, 0, 1].some((i) => [-1, 0, 1].some((j) =>
  memory.explored[regionOf({ x: x + i * REGION, z: z + j * REGION })] !== undefined))

// (dx, dz) の向きに、まだ見ていない土地が始まるまでの距離（区域ごとに調べる、max まで）。
// 探しものが近くにないとき、行った所を行き来せずに、その先へ進むため（2026-09-26: 原木が見つからず、
// 家のまわりの同じ所の探索を繰り返していた）
export const FRONTIER_MAX = 320
export function frontierDistance (memory, from, dx, dz, max = FRONTIER_MAX) {
  for (let d = REGION; d <= max; d += REGION) {
    if (!known(memory, from.x + dx * d, from.z + dz * d)) return d
  }
  return max
}

// (dx, dz) の向きに進んだとき、まだ見ていない土地の手前（frontier の区域）のバイオーム。手前から現在地に向かって、
// 記録のある最初の区域のもの。記録がなければ null（バイオームは立った所だけを覚える。立っていない区域は分からない）
export function frontierBiome (memory, from, dx, dz, go) {
  for (let d = go; d >= REGION; d -= REGION) {
    const b = memory.biomes?.[regionOf({ x: from.x + dx * d, z: from.z + dz * d })]
    if (b) return b
  }
  return null
}

// 見たバイオームごとの、一番近い区域: [{ biome, regions（その数）, direction, distance_m }]。近い順。
// 今いる区域のバイオームは今いる場所の行に出るので、ここは近い順に BIOMES_SHOWN 種類まで
export const BIOMES_SHOWN = 8
export function biomesSeen (memory, me, bearing) {
  const nearest = {}
  for (const [region, biome] of Object.entries(memory.biomes ?? {})) {
    const [rx, rz] = region.split(',').map(Number)
    const at = { x: (rx + 0.5) * REGION, z: (rz + 0.5) * REGION }
    const d = flatDistance(at, me)
    const e = (nearest[biome] ??= { biome, regions: 0, at, d })
    e.regions++
    if (d < e.d) { e.at = at; e.d = d }
  }
  return Object.values(nearest)
    .sort((a, b) => a.d - b.d)
    .slice(0, BIOMES_SHOWN)
    .map((e) => ({ biome: e.biome, regions: e.regions, direction: bearing(me, e.at), distance_m: Math.round(e.d) }))
}

// 判断する側が見るもの: 種類ごとの一番近い場所と、ボットが死んだ場所
// GET /memory（設計書 47 §4 search_memory）: 覚えている場所を座標つきで。場所は種類ごとに 1 件（近い順
// MEMORY_PER_KIND か所。検索の一覧を種類の数に抑える）、チェスト（中身）、焼いている途中のかまど、
// 死んだ場所。dropsOf(kind) はそのブロックから取れる物、jaOf(name) は日本語名（日本語の検索語で引く）
export const MEMORY_PER_KIND = 5
export function memoryList (memory, me, now, bearing, dropsOf = () => [], jaOf = () => '') {
  const where = (p) => ({ x: p.x, y: p.y, z: p.z, distance_m: Math.round(flatDistance(p, me)), direction: bearing(me, p), minutes_ago: Math.round((now - p.seen) / TICKS_PER_MINUTE) })
  const places = Object.entries(memory.places).flatMap(([kind, list]) => {
    const fresh = list.filter((p) => !stale(kind, p, now)).sort((a, b) => flatDistance(a, me) - flatDistance(b, me))
    if (!fresh.length) return []
    const gives = dropsOf(kind)
    return [{
      kind,
      ja: jaOf(kind),
      ...(gives.length ? { gives, gives_ja: gives.map(jaOf) } : {}),
      places_known: fresh.length,
      nearest: fresh.slice(0, MEMORY_PER_KIND).map((p) => ({ count: p.count, ...where(p) }))
    }]
  }).sort((a, b) => a.nearest[0].distance_m - b.nearest[0].distance_m)
  return {
    places,
    chests: chests(memory).map((c) => ({ ...where(c), contents: c.contents })),
    furnaces: Object.values(memory.furnaces ?? {}).map((f) => ({ ...where(f), making: f.making })),
    deaths: memory.deaths.map(where)
  }
}

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
    biomes: biomesSeen(memory, me, bearing),
    deaths: memory.deaths.map((d) => ({ direction: bearing(me, d), distance_m: Math.round(flatDistance(d, me)), minutes_ago: Math.round((now - d.seen) / TICKS_PER_MINUTE) }))
  }
}
