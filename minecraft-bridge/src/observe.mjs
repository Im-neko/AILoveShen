// Compact observation of the bot's situation, shared by action enumeration and /observe.

export const MOB_RADIUS = 48
export const MOB_LIMIT = 16
export const DROP_RADIUS = 24
export const THREAT_RADIUS = 16
const MELEE_RADIUS = 4 // this close a hostile is a threat even when leaves or a corner hide it
export const HISTORY = 5

// Mineflayer tags these as "hostile", but they only attack when provoked.
const NEUTRAL = new Set(['enderman', 'zombified_piglin', 'piglin', 'wolf', 'bee', 'llama', 'trader_llama',
  'polar_bear', 'dolphin', 'panda', 'goat', 'iron_golem'])
// Spiders are neutral in daylight.
const DAY_NEUTRAL = new Set(['spider', 'cave_spider'])

export const round = (v, d = 1) => Math.round(v * 10 ** d) / 10 ** d

export function bearing (from, to) {
  // Compass direction of `to` seen from `from` (Minecraft: -Z north, +X east)
  const deg = (Math.atan2(to.x - from.x, -(to.z - from.z)) * 180 / Math.PI + 360) % 360
  return ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'][Math.round(deg / 45) % 8]
}

export function dayPhase (timeOfDay) {
  if (timeOfDay < 12000) return 'day'
  if (timeOfDay < 13000) return 'dusk'
  if (timeOfDay < 23000) return 'night'
  return 'dawn'
}

export const isLog = (name) => name.endsWith('_log')
export const isPlanks = (name) => name.endsWith('_planks')

export function inventoryCounts (bot) {
  const counts = {}
  for (const it of bot.inventory.items()) counts[it.name] = (counts[it.name] ?? 0) + it.count
  return counts
}

export function countMatching (bot, pred) {
  return bot.inventory.items().filter((i) => pred(i.name)).reduce((n, i) => n + i.count, 0)
}

export function nearbyEntities (bot) {
  const me = bot.entity.position
  return Object.values(bot.entities)
    .filter((e) => e !== bot.entity && e.position)
    .map((e) => ({ e, dist: e.position.distanceTo(me) }))
    .sort((a, b) => a.dist - b.dist)
}

export function isHostile (bot, e) {
  if (e.type !== 'hostile' || NEUTRAL.has(e.name)) return false
  if (DAY_NEUTRAL.has(e.name) && dayPhase(bot.time.timeOfDay) === 'day') return false
  return true
}

// True when nothing solid blocks the straight line between the bot's eyes and the entity.
export function canSee (bot, e) {
  const eye = bot.entity.position.offset(0, bot.entity.eyeHeight ?? 1.62, 0)
  const target = e.position.offset(0, (e.height ?? 1) * 0.8, 0)
  const dir = target.minus(eye)
  const dist = dir.norm()
  if (dist === 0) return true
  const hit = bot.world.raycast(eye, dir.normalize(), dist)
  return !hit
}

// Hostile mobs that can actually reach the bot now: close, roughly level and in line of sight.
// Mobs in caves below or behind walls are reported in the observation but are not threats.
export function threats (bot) {
  const me = bot.entity.position
  return nearbyEntities(bot).filter(({ e, dist }) =>
    dist <= THREAT_RADIUS && isHostile(bot, e) && Math.abs(e.position.y - me.y) < 3 && (dist <= MELEE_RADIUS || canSee(bot, e)))
}

export function summarize (bot, history, extra = {}) {
  const me = bot.entity.position
  const ents = nearbyEntities(bot)
  const mobs = ents
    .filter(({ e, dist }) => e.type !== 'player' && e.name !== 'item' && e.type !== 'other' && e.type !== 'projectile' && dist <= MOB_RADIUS)
    .slice(0, MOB_LIMIT)
    .map(({ e, dist }) => ({
      name: e.name,
      hostile: isHostile(bot, e),
      distance_m: round(dist),
      direction: bearing(me, e.position),
      height_diff_m: round(e.position.y - me.y),
      visible: canSee(bot, e)
    }))
  const drops = ents
    .filter(({ e, dist }) => e.name === 'item' && dist <= DROP_RADIUS)
    .map(({ e, dist }) => ({ item: e.getDroppedItem()?.name ?? 'unknown', distance_m: round(dist), direction: bearing(me, e.position) }))
  return {
    time: { phase: dayPhase(bot.time.timeOfDay), time_of_day: bot.time.timeOfDay, raining: bot.isRaining },
    self: {
      health: round(bot.health),
      max_health: 20,
      food: bot.food,
      position: { x: round(me.x), y: round(me.y), z: round(me.z) },
      held_item: bot.heldItem ? bot.heldItem.name : null,
      in_water: !!bot.entity.isInWater
    },
    inventory: inventoryCounts(bot),
    mobs,
    drops,
    recent_actions: history.slice(-HISTORY),
    ...extra
  }
}
