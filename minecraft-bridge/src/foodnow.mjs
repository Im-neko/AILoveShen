// 食べ物が要るとき、いまどこに何があるか（find_foods の now）。持ち物、家のチェスト、見えている動物、
// 熟した作物、覚えている場所を、満腹度で数える。飢えてから遠くへ探しに行かないよう、足りない満腹度と
// 比べられるようにする（2026-10-03、user: 「検索ツールに食べ物検索を増やして食べ物が必要な時になにがあるかを
// 調べられるようにしたい」）
import { inventoryCounts, nearbyEntities, bearing, round } from './observe.mjs'
import { HUNTABLE, smeltingProduct } from './knowledge.mjs'
import { isRawMeat, foodPointsIn } from './cooking.mjs'
import { knownFurnace } from './primitives.mjs'
import { homeChests, storedCounts, recall, REMEMBERED_ANIMALS } from './memory.mjs'
import { memoryView } from './worldmap-places.mjs'
import { CROPS } from './farming.mjs'
import { cropFarms } from './facilities.mjs'
import { REGEN_FOOD } from './healing.mjs'

const SIGHT_RADIUS = 32 // 見えている動物を数える範囲（狩りの候補と同じ）
const REMEMBERED_SHOWN = 6

// 食べ物の名前 → 満腹度（生肉は焼いた値も添える）
function itemsOf (counts, knowledge) {
  const foods = knowledge.md.foodsByName
  return knowledge.resolve('food').members
    .filter((name) => counts[name] > 0)
    .map((name) => {
      const cooked = isRawMeat(knowledge, name) ? foods[smeltingProduct(name)] : null
      return { name, count: counts[name], points_each: foods[name].foodPoints, ...(cooked ? { cooked_points_each: cooked.foodPoints } : {}) }
    })
    .sort((a, b) => b.points_each * b.count - a.points_each * a.count)
}

// モブが落とす食べ物
function foodsFrom (mob, knowledge) {
  const foods = knowledge.md.foodsByName
  const out = []
  for (const name of knowledge.resolve('food').members) {
    if (!(knowledge.mobSources.get(name) ?? []).includes(mob)) continue
    const cooked = isRawMeat(knowledge, name) ? foods[smeltingProduct(name)] : null
    out.push(`${name} ${foods[name].foodPoints}${cooked ? ` (cooked ${cooked.foodPoints})` : ''}`)
  }
  return out
}

export function foodNow (bot, state, knowledge) {
  const furnaceKnown = !!knownFurnace(bot, state)
  const me = bot.entity.position
  const age = Number(bot.time.age)
  const carried = inventoryCounts(bot)
  const stored = state.home && state.memory ? storedCounts(state.memory, state.home) : null
  const hasChest = !!(state.home && state.memory && homeChests(state.memory, state.home).length)

  const animals = new Map()
  for (const { e, dist } of nearbyEntities(bot)) {
    if (!HUNTABLE.has(e.name) || dist > SIGHT_RADIUS) continue
    const a = animals.get(e.name) ?? { mob: e.name, count: 0, nearest_m: round(dist), direction: bearing(me, e.position), gives: foodsFrom(e.name, knowledge) }
    a.count++
    animals.set(e.name, a)
  }

  const crops = []
  for (const crop of Object.keys(CROPS)) {
    const f = cropFarms(bot, state, crop)
    if (f.ripe > 0) crops.push({ crop, ripe: f.ripe, farms: (f.farms ?? []).filter((x) => x.ripe).map((x) => x.name) })
  }

  const places = recall(memoryView(state, me, age, [...REMEMBERED_ANIMALS]), [...REMEMBERED_ANIMALS], me, age)
    .slice(0, REMEMBERED_SHOWN)
    .map((p) => ({ mob: p.kind, x: p.x, z: p.z, distance_m: p.distance, minutes_ago: p.minutesAgo }))

  const points = foodPointsIn(carried, knowledge, furnaceKnown)
  const storedPoints = stored ? foodPointsIn(stored, knowledge, furnaceKnown) : 0
  return {
    status: {
      food: bot.food,
      health: round(bot.health),
      points_to_full: Math.max(0, 20 - bot.food),
      points_to_heal: Math.max(0, REGEN_FOOD - bot.food),
      furnace_known: furnaceKnown
    },
    carried: { points, items: itemsOf(carried, knowledge) },
    ...(hasChest ? { home_chest: { points: storedPoints, items: itemsOf(stored, knowledge), note: 'as last opened' } } : {}),
    in_sight: [...animals.values()],
    ripe_crops: crops,
    remembered_animals: places,
    note: 'points are hunger points (a full bar is 20; walking costs about 1 per 40-70 m); raw meat counts as cooked only when a furnace is known'
  }
}
