// Bounded actions: enumerate what is executable right now, and execute one to completion.
//
// Only actions whose preconditions hold are listed, so the decision maker can never pick
// something impossible. Executors take an AbortSignal (timeout, or damage taken) and must stop
// promptly once it fires: loops check it, and the caller cancels pathing and digging.

import pathfinderPkg from 'mineflayer-pathfinder'
import {
  THREAT_RADIUS, DROP_RADIUS, round, isLog, isPlanks, inventoryCounts, countMatching, nearbyEntities, threats
} from './observe.mjs'
import { buildStep, canBuildNow } from './build.mjs'
import { craftWithRecipeBook } from './craft.mjs'

const { Movements, goals } = pathfinderPkg
export const ACTION_TIMEOUT_MS = 20000
const LOG_SEARCH_RADIUS = 32
const MAX_LOG_HEIGHT = 4 // blocks above the ground the bot can chop without climbing
const TABLE_SEARCH_RADIUS = 32
const FLEE_DISTANCE = 16
const REACH = 4.5 // survival block reach from the eyes
const LEAVES_STEP_COST = 10
const EXPLORE_DISTANCE = 20
const EXPLORE_ATTEMPTS = 3
const EXPLORE_MIN_PROGRESS = 5

const WEAPONS = ['netherite_sword', 'diamond_sword', 'iron_sword', 'stone_sword', 'golden_sword', 'wooden_sword',
  'netherite_axe', 'diamond_axe', 'iron_axe', 'stone_axe', 'golden_axe', 'wooden_axe']

// Note: to cancel pathing use setGoal(null). pathfinder.stop() only raises a flag that is cleared
// by the next movement tick; called while idle it makes the next goto() fail immediately.
export function configureMovements (bot, state) {
  const m = new Movements(bot)
  // Never walk on top of the structure under construction: partial walls form steps the bot
  // would climb, and it then places the roof from the wall tops or gets stuck up there.
  m.exclusionAreasStep.push((block) => {
    const o = state.plan?.origin
    if (!o) return 0
    const p = block.position
    const inside = p.x >= o.x && p.x < o.x + state.plan.size.width && p.z >= o.z && p.z < o.z + state.plan.size.depth
    return inside && p.y > o.y + 1 ? 101 : 0
  })
  // Stay out of tree tops: walking on leaves leads onto canopies that are hard to leave.
  m.exclusionAreasStep.push((block) => isLeaves(bot.blockAt(block.position.offset(0, -1, 0))?.name) ? LEAVES_STEP_COST : 0)
  // Leaves are the only blocks the bot may break while walking (like a player pushing through a
  // canopy); never the house or terrain.
  m.exclusionAreasBreak.push((block) => isLeaves(block.name) ? 0 : 100)
  m.scafoldingBlocks = [] // never spend building material on pillaring
  m.allow1by1towers = false
  bot.pathfinder.setMovements(m)
}

const isFood = (bot, item) => !!bot.registry.foodsByName[item.name]
const isLeaves = (name) => !!name?.endsWith('_leaves')

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

export function findLog (bot) {
  const positions = bot.findBlocks({ matching: (b) => isLog(b.name), maxDistance: LOG_SEARCH_RADIUS, count: 64 })
  const me = bot.entity.position
  return positions
    .map((p) => bot.blockAt(p))
    .filter((b) => b && logReachable(bot, b))
    .sort((a, b) => a.position.distanceTo(me) - b.position.distanceTo(me))[0] ?? null
}

export function findTable (bot) {
  return bot.findBlock({ matching: bot.registry.blocksByName.crafting_table.id, maxDistance: TABLE_SEARCH_RADIUS })
}

// Drops the bot already failed to reach are skipped (state.unreachableDrops holds entity ids).
function nearestDrop (bot, state) {
  return nearbyEntities(bot).find(({ e, dist }) => e.name === 'item' && dist <= DROP_RADIUS &&
    Math.abs(e.position.y - bot.entity.position.y) < 4 && !state.unreachableDrops.has(e.id))
}

const plankNameFor = (logName) => logName.replace(/_log$/, '_planks')
const doorNameFor = (plankName) => plankName.replace(/_planks$/, '_door')

// Logs beyond those the plan still places as log blocks (e.g. corner pillars).
function spareLogs (bot, state) {
  const reserved = state.plan?.materialsNeeded(bot).log ?? 0
  return Math.max(0, countMatching(bot, isLog) - reserved)
}

function mostPlanks (bot) {
  const counts = Object.entries(inventoryCounts(bot)).filter(([n]) => isPlanks(n)).sort((a, b) => b[1] - a[1])
  return counts[0] ?? null
}

async function goNear (bot, pos, range) {
  try {
    await bot.pathfinder.goto(new goals.GoalNear(pos.x, pos.y, pos.z, range))
  } finally {
    bot.pathfinder.setGoal(null)
  }
}

export function availableActions (bot, state) {
  const acts = []
  const inv = inventoryCounts(bot)
  const t = threats(bot)
  if (t.length) {
    const h = t[0]
    acts.push({ id: 'attack_hostile', description: `Fight the nearest hostile mob (${h.e.name}) with the held item` })
    acts.push({ id: 'flee_hostile', description: `Run away from the nearest hostile mob (${h.e.name})` })
  }
  const weapon = WEAPONS.find((w) => inv[w])
  if (weapon && bot.heldItem?.name !== weapon) acts.push({ id: 'equip_weapon', description: `Hold ${weapon} in hand` })
  if (bot.food < 20 && bot.inventory.items().some((i) => isFood(bot, i))) acts.push({ id: 'eat', description: 'Eat food from the inventory' })
  if (nearestDrop(bot, state)) acts.push({ id: 'pickup_drop', description: 'Pick up the nearest dropped item' })
  if (findLog(bot)) acts.push({ id: 'collect_log', description: 'Chop the nearest reachable tree log' })
  const book = state.recipeBook
  const logs = bot.inventory.items().find((i) => isLog(i.name))
  if (logs && spareLogs(bot, state) > 0 && book.recipesFor(plankNameFor(logs.name)).length) {
    acts.push({ id: 'craft_planks', description: 'Craft logs into planks, keeping the logs the house needs' })
  }
  const table = findTable(bot)
  if (!inv.crafting_table && !table && countMatching(bot, isPlanks) >= 4 && book.recipesFor('crafting_table').length) {
    acts.push({ id: 'craft_crafting_table', description: 'Craft a crafting table from 4 planks' })
  }
  if (inv.crafting_table && !table) acts.push({ id: 'place_crafting_table', description: 'Place the crafting table next to the bot' })
  const planks = mostPlanks(bot)
  if (table && planks && planks[1] >= 6 && !Object.keys(inv).some((n) => n.endsWith('_door')) &&
      book.recipesFor(doorNameFor(planks[0])).length) {
    acts.push({ id: 'craft_door', description: 'Craft a wooden door at the crafting table' })
  }
  if (state.plan && !state.plan.status(bot).complete && canBuildNow(bot, state.plan)) {
    acts.push({ id: 'build_step', description: 'Place the next blocks of the house plan' })
  }
  acts.push({ id: 'explore', description: 'Walk about 20m in a random direction' })
  acts.push({ id: 'idle', description: 'Stay still and look around' })
  return acts
}

export const EXECUTORS = {
  async attack_hostile (bot, state, signal) {
    const target = threats(bot)[0]?.e
    if (!target) return 'no threat in range'
    const start = Date.now()
    while (bot.entities[target.id] && Date.now() - start < 10000 && !signal.aborted) {
      if (target.position.distanceTo(bot.entity.position) > 3) {
        bot.pathfinder.setGoal(new goals.GoalFollow(target, 2), true)
      } else {
        await bot.lookAt(target.position.offset(0, target.height * 0.8, 0), true)
        bot.attack(target)
      }
      await bot.waitForTicks(12)
    }
    bot.pathfinder.setGoal(null)
    return bot.entities[target.id] ? `${target.name} still alive` : `${target.name} gone (killed or despawned)`
  },
  async flee_hostile (bot, state, signal) {
    const h = threats(bot)[0]?.e
    if (!h) return 'no threat in range'
    bot.pathfinder.setGoal(new goals.GoalInvert(new goals.GoalFollow(h, FLEE_DISTANCE)), true)
    const start = Date.now()
    while (Date.now() - start < 8000 && !signal.aborted && bot.entities[h.id] && h.position.distanceTo(bot.entity.position) < FLEE_DISTANCE) {
      await bot.waitForTicks(5)
    }
    bot.pathfinder.setGoal(null)
    return `now ${round(h.position.distanceTo(bot.entity.position))}m from ${h.name}`
  },
  async equip_weapon (bot) {
    const inv = inventoryCounts(bot)
    const w = WEAPONS.find((n) => inv[n])
    await bot.equip(bot.inventory.items().find((i) => i.name === w), 'hand')
    return `holding ${w}`
  },
  async eat (bot) {
    const food = bot.inventory.items().find((i) => isFood(bot, i))
    const before = bot.food
    await bot.equip(food, 'hand')
    await bot.consume()
    return `ate ${food.name}, hunger ${before} -> ${bot.food}`
  },
  async pickup_drop (bot, state) {
    const d = nearestDrop(bot, state)?.e
    if (!d) return 'no drop'
    const before = bot.inventory.items().reduce((n, i) => n + i.count, 0)
    try {
      await goNear(bot, d.position, 0.5)
      await bot.waitForTicks(10)
    } catch (e) {
      state.unreachableDrops.add(d.id)
      throw e
    }
    const gained = bot.inventory.items().reduce((n, i) => n + i.count, 0) - before
    if (gained <= 0) {
      state.unreachableDrops.add(d.id)
      throw new Error('reached the drop but picked nothing up')
    }
    return `picked up ${gained} items`
  },
  async collect_log (bot, state, signal) {
    const log = findLog(bot)
    if (!log) return 'no reachable log'
    // Stand where the log is in reach, not merely near it: a log above head height is otherwise
    // "near" only from the leaves of the tree.
    try {
      await bot.pathfinder.goto(new goals.GoalLookAtBlock(log.position, bot.world, { reach: REACH }))
    } catch (e) {
      console.log(`[collect_log] path to ${log.position} from ${bot.entity.position} failed: ${e.message}`)
      throw e
    } finally {
      bot.pathfinder.setGoal(null)
    }
    signal.throwIfAborted()
    // Digging while airborne is 5x slower (e.g. right after chopping the block we stood on)
    for (let i = 0; i < 40 && !bot.entity.onGround; i++) await bot.waitForTicks(1)
    await bot.dig(bot.blockAt(log.position), true)
    await bot.waitForTicks(10)
    signal.throwIfAborted()
    const drop = nearestDrop(bot, state)
    if (drop && drop.dist < 6) await goNear(bot, drop.e.position, 0.5).catch(() => {})
    return `chopped ${log.name}; logs now ${countMatching(bot, isLog)}`
  },
  async craft_planks (bot, state, signal) {
    let crafted = 0
    let spare
    while ((spare = spareLogs(bot, state)) > 0) {
      signal.throwIfAborted()
      const logs = bot.inventory.items().find((i) => isLog(i.name))
      // makeAll moves a whole stack into the grid; a single request crafts one log
      const makeAll = spare >= logs.count
      crafted += await craftWithRecipeBook(bot, state.recipeBook, plankNameFor(logs.name), { makeAll })
    }
    return `crafted ${crafted} planks; planks now ${countMatching(bot, isPlanks)}, logs kept ${countMatching(bot, isLog)}`
  },
  async craft_crafting_table (bot, state) {
    await craftWithRecipeBook(bot, state.recipeBook, 'crafting_table')
    return 'crafted crafting_table'
  },
  async place_crafting_table (bot, state) {
    const item = bot.inventory.items().find((i) => i.name === 'crafting_table')
    const me = bot.entity.position.floored()
    const footprint = state.plan?.origin
      ? (p) => p.x >= state.plan.origin.x - 1 && p.x <= state.plan.origin.x + state.plan.size.width &&
               p.z >= state.plan.origin.z - 1 && p.z <= state.plan.origin.z + state.plan.size.depth
      : () => false
    for (const [dx, dz] of [[2, 0], [-2, 0], [0, 2], [0, -2], [2, 2], [-2, -2], [2, -2], [-2, 2]]) {
      const pos = me.offset(dx, 0, dz)
      const ground = bot.blockAt(pos.offset(0, -1, 0))
      const spot = bot.blockAt(pos)
      if (footprint(pos) || !ground || ground.boundingBox !== 'block' || !spot || spot.name !== 'air') continue
      await bot.equip(item, 'hand')
      await bot.placeBlock(ground, { x: 0, y: 1, z: 0 })
      return `placed crafting_table at ${pos}`
    }
    throw new Error('no free spot for the crafting table')
  },
  async craft_door (bot, state) {
    const table = findTable(bot)
    await goNear(bot, table.position, 3)
    const doorName = doorNameFor(mostPlanks(bot)[0])
    const gained = await craftWithRecipeBook(bot, state.recipeBook, doorName, { table })
    return `crafted ${gained} ${doorName}`
  },
  async build_step (bot, state, signal) {
    return await buildStep(bot, state.plan, signal)
  },
  async explore (bot, state, signal) {
    const start = bot.entity.position.clone()
    const errors = []
    for (let i = 0; i < EXPLORE_ATTEMPTS; i++) {
      signal.throwIfAborted()
      const a = Math.random() * Math.PI * 2
      const target = start.offset(Math.cos(a) * EXPLORE_DISTANCE, 0, Math.sin(a) * EXPLORE_DISTANCE)
      try {
        await bot.pathfinder.goto(new goals.GoalNearXZ(target.x, target.z, 3))
      } catch (e) {
        errors.push(e.message)
      } finally {
        bot.pathfinder.setGoal(null)
      }
      if (bot.entity.position.xzDistanceTo(start) >= EXPLORE_MIN_PROGRESS) {
        return `walked ${round(bot.entity.position.xzDistanceTo(start))}m to ${round(bot.entity.position.x)},${round(bot.entity.position.z)}`
      }
    }
    throw new Error(`could not walk away from ${round(start.x)},${round(start.z)}: ${errors.join('; ')}`)
  },
  async idle (bot, state, signal) {
    for (let i = 0; i < 3 && !signal.aborted; i++) { await bot.look(bot.entity.yaw + Math.PI / 2, 0); await bot.waitForTicks(20) }
    return 'looked around'
  }
}
