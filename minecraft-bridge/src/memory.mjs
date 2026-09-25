// World memory: what was seen and is out of view now (docs/design/14_world_memory.md §4).
//
// What can be read at any time (inventory, health, the goal's progress) is never copied here;
// only places seen before are kept, each with when it was last seen (world age in ticks):
// - places: per kind (a block or animal name), one entry per 16x16 region with a count
// - explored: when each region was last visited
// - deaths: where the bot died
// A remembered place near the bot that is not seen now is forgotten (dug out, walked away, or
// never there), so a trip to it is not repeated.

export const REGION = 16
export const MAX_PLACES_PER_KIND = 8
export const ANIMAL_STALE_TICKS = 24000 // a game day: animals wander
export const FORGET_WITHIN = 20 // a place remembered this near and not seen now is gone
export const RECALL_BEYOND = 32 // nearer ones are in view: the candidates offer them directly
const TICKS_PER_MINUTE = 1200
const MAX_DEATHS = 3
const RECALLED_OFFERED = 3
const SUMMARY_PLACES = 10

export const REMEMBERED_BLOCK = /^(?!stripped_).*_log$|^(deepslate_)?(coal|iron)_ore$/
export const REMEMBERED_ANIMALS = new Set(['cow', 'pig', 'sheep', 'chicken'])

export function newMemory () {
  return { places: {}, explored: {}, deaths: [] }
}

export const regionOf = (p) => `${Math.floor(p.x / REGION)},${Math.floor(p.z / REGION)}`
const flatDistance = (a, b) => Math.hypot(a.x - b.x, a.z - b.z)

// What was just seen around `me`: sightings are [{ kind, pos }]
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

// Places of these kinds out of view and still worth a trip, nearest first
export function recall (memory, kinds, me, now) {
  return kinds.flatMap((kind) => (memory.places[kind] ?? [])
    .filter((p) => !stale(kind, p, now) && flatDistance(p, me) > RECALL_BEYOND)
    .map((p) => ({ kind, ...p, distance: Math.round(flatDistance(p, me)), minutesAgo: Math.round((now - p.seen) / TICKS_PER_MINUTE) })))
    .sort((a, b) => a.distance - b.distance)
    .slice(0, RECALLED_OFFERED)
}

export const visited = (memory, pos) => memory.explored[regionOf(pos)] !== undefined

// What the decision makers see: the nearest place of each kind, and where the bot died
export function summarizeMemory (memory, me, now, bearing) {
  const places = Object.entries(memory.places).flatMap(([kind, list]) => {
    const fresh = list.filter((p) => !stale(kind, p, now))
    if (!fresh.length) return []
    const p = fresh.reduce((a, b) => (flatDistance(a, me) <= flatDistance(b, me) ? a : b))
    return [{ kind, count: p.count, direction: bearing(me, p), distance_m: Math.round(flatDistance(p, me)), minutes_ago: Math.round((now - p.seen) / TICKS_PER_MINUTE) }]
  }).sort((a, b) => a.distance_m - b.distance_m).slice(0, SUMMARY_PLACES)
  return {
    places,
    explored_regions: Object.keys(memory.explored).length,
    deaths: memory.deaths.map((d) => ({ direction: bearing(me, d), distance_m: Math.round(flatDistance(d, me)), minutes_ago: Math.round((now - d.seen) / TICKS_PER_MINUTE) }))
  }
}
