// What is around the bot for the solver and the candidates: blocks it may dig, animals it may hunt,
// a crafting table. One snapshot per observation; lookups are cached in it.

import { isLog, inventoryCounts, nearbyEntities } from './observe.mjs'
import { inHouse } from './home.mjs'
import { findTable, findFurnace, isLeaves, isUnreachable, HUNGER_URGENT } from './primitives.mjs'
import { HUNTABLE } from './knowledge.mjs'
import { REMEMBERED_BLOCK, REMEMBERED_ANIMALS, storedCounts, smeltingCounts, recallableKinds } from './memory.mjs'

const DIG_RADIUS = 32
const DIG_DY = 4 // blocks above or below the feet the bot digs without climbing or tunnelling
const MAX_LOG_HEIGHT = 4 // a log this far above the ground under its trunk is chopped from the ground
const HUNT_RADIUS = 32
const TARGETS_PER_KIND = 3

// A log is reachable if the ground under its trunk is within MAX_LOG_HEIGHT blocks.
function logReachable (bot, block) {
  for (let dy = 1; dy <= MAX_LOG_HEIGHT; dy++) {
    const below = bot.blockAt(block.position.offset(0, -dy, 0))
    if (!below) return false
    if (isLog(below.name)) continue
    return below.boundingBox === 'block' && !isLeaves(below.name)
  }
  return false
}

const NEIGHBORS = [[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]]
// Touching air (water has no collision box either: town2 drowned digging coal under the sea)
const AIR = new Set(['air', 'cave_air'])
const exposed = (bot, p) => NEIGHBORS.some(([x, y, z]) => AIR.has(bot.blockAt(p.offset(x, y, z))?.name))

// Blocks of this kind the bot can dig: nearest first, never part of the house
export function digTargets (bot, state, name, limit = TARGETS_PER_KIND) {
  const id = bot.registry.blocksByName[name]?.id
  if (id == null) return []
  const me = bot.entity.position
  return bot.findBlocks({ matching: id, maxDistance: DIG_RADIUS, count: 64 })
    .filter((p) => !inHouse(state, p) && exposed(bot, p) && !isUnreachable(state, p) &&
      (isLog(name) ? logReachable(bot, bot.blockAt(p)) : Math.abs(p.y - Math.floor(me.y)) <= DIG_DY))
    .sort((a, b) => a.distanceTo(me) - b.distanceTo(me))
    .slice(0, limit)
}

export function huntTargets (bot, names, limit = TARGETS_PER_KIND) {
  const me = bot.entity.position
  return nearbyEntities(bot)
    .filter(({ e, dist }) => names.includes(e.name) && HUNTABLE.has(e.name) && dist <= HUNT_RADIUS && Math.abs(e.position.y - me.y) < 4)
    .slice(0, limit)
}

// What is around now that is worth remembering (memory.mjs): [{ kind, pos }]. Blocks only where the
// bot can dig them, touching air: buried ore counted too sent it off exploring (town2: 64 coal ore
// "around the home", none it could reach)
export function sightings (bot) {
  const ids = Object.values(bot.registry.blocksByName).filter((b) => REMEMBERED_BLOCK.test(b.name)).map((b) => b.id)
  const blocks = bot.findBlocks({ matching: ids, maxDistance: DIG_RADIUS, count: 512 })
    .filter((p) => exposed(bot, p))
    .map((p) => ({ kind: bot.blockAt(p)?.name, pos: p }))
    .filter((s) => s.kind)
  const animals = nearbyEntities(bot)
    .filter(({ e, dist }) => REMEMBERED_ANIMALS.has(e.name) && dist <= HUNT_RADIUS)
    .map(({ e }) => ({ kind: e.name, pos: e.position }))
  return [...blocks, ...animals]
}

// What may be taken out of the chests: all but what the mid goals keep there (food kept for a stock
// is still eaten when starving)
export function takeable (stored, keep, starving) {
  const out = { ...stored }
  for (const k of keep) {
    if (k.food && starving) continue
    let left = k.count
    for (const m of k.members) {
      const n = Math.min(out[m] ?? 0, left)
      if (!n) continue
      out[m] -= n
      left -= n
    }
  }
  return out
}

// The solver's view of the world; dig/hunt lookups are cached for the candidates
export function snapshot (bot, state) {
  const digCache = new Map()
  const dig = (name) => {
    if (!digCache.has(name)) digCache.set(name, digTargets(bot, state, name))
    return digCache.get(name)
  }
  const hunt = (name) => huntTargets(bot, [name])
  return {
    inventory: inventoryCounts(bot),
    stored: takeable(storedCounts(state.memory ?? {}), state.goal?.keep ?? [], bot.food <= HUNGER_URGENT),
    remembered: state.memory ? recallableKinds(state.memory, bot.entity.position, Number(bot.time.age)) : new Set(),
    blocks: new Proxy({}, { get: (_, name) => typeof name === 'string' ? dig(name).length : undefined }),
    mobs: new Proxy({}, { get: (_, name) => typeof name === 'string' ? hunt(name).length : undefined }),
    table: findTable(bot) ? 'near' : null,
    furnace: findFurnace(bot) ? 'near' : null,
    smelting: smeltingCounts(state.memory ?? {}),
    unlocked: (item) => state.recipeBook.recipesFor(item).length > 0,
    dig,
    hunt
  }
}
