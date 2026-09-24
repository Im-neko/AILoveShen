// Primitive actions and the movement rules they share.
//
// Each primitive runs one bounded step against a concrete target (a block, an entity, an item) and
// reports the outcome checked against the world; failures are thrown, never reported as success.
// Executors take an AbortSignal (timeout, or damage taken) and stop promptly once it fires: loops
// check it, and the caller cancels pathing and digging.

import { once } from 'node:events'
import pathfinderPkg from 'mineflayer-pathfinder'
import { THREAT_RADIUS, round, inventoryCounts, nearbyEntities, threats, isHostile } from './observe.mjs'
import { findSite, placeOne } from './build.mjs'
import { craftWithRecipeBook } from './craft.mjs'
import { shelteredFrom, enterHome, isDoorOpen, bedSpot, inHouse, exitThroughWall, repairWall } from './home.mjs'

const { Movements, goals } = pathfinderPkg
export const REACH = 4.5 // survival block reach from the eyes
// Health this low is critical: stated as a need, and a fight or flight stops here
export const HEALTH_CRITICAL = 8
export const TABLE_SEARCH_RADIUS = 32
const HOSTILE_AVOID_RADIUS = 5
const HOSTILE_STEP_COST = 20
const HOSTILE_CACHE_MS = 1000
const LEAVES_STEP_COST = 10
const FLEE_DISTANCE = 16
const FIGHT_TIMEOUT_MS = 10000
const FLEE_TIMEOUT_MS = 8000
const PICKUP_WAIT_MS = 1500
const DROP_COLLECT_RADIUS = 8
const WAIT_TICKS = 200
const GLANCE_TICKS = 50
const GLANCE_ANGLE = Math.PI / 3
const EXPLORE_DISTANCE = 24
const EXPLORE_MIN_PROGRESS = 5
const RECIPE_UNLOCK_TICKS = 40
// Sleeping is possible from this time of day (Mineflayer's own check: 12541..23458)
export const SLEEP_FROM = 12541
export const SLEEP_UNTIL = 23458
const TIME_UPDATE_TIMEOUT_MS = 3000 // the server sends the time every second

const WEAPONS = ['netherite_sword', 'diamond_sword', 'iron_sword', 'stone_sword', 'golden_sword', 'wooden_sword',
  'netherite_axe', 'diamond_axe', 'iron_axe', 'stone_axe', 'golden_axe', 'wooden_axe']
export const isLeaves = (name) => !!name?.endsWith('_leaves')

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
  // Keep away from hostile mobs: paths through their surroundings cost more, so walking home
  // detours around a creeper instead of running into it again right after fleeing.
  let hostiles = []
  let hostilesAt = 0
  m.exclusionAreasStep.push((block) => {
    if (Date.now() - hostilesAt > HOSTILE_CACHE_MS) {
      hostiles = nearbyEntities(bot).filter(({ e, dist }) => dist <= THREAT_RADIUS * 2 && isHostile(bot, e)).map(({ e }) => e.position.clone())
      hostilesAt = Date.now()
    }
    return hostiles.some((h) => h.distanceTo(block.position) <= HOSTILE_AVOID_RADIUS) ? HOSTILE_STEP_COST : 0
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

// Reach any spot at least `distance` from the entity, re-planning whenever it has moved a couple of
// blocks. GoalInvert(GoalFollow(e, d)) looks equivalent but only re-plans after the entity moved d
// blocks: the bot ran to a spot far from where the mob *was*, then stood still while it closed in.
const REPLAN_MOVE = 2
class GoalAwayFrom extends goals.Goal {
  constructor (entity, distance) {
    super()
    this.entity = entity
    this.distance = distance
    this.from = entity.position.clone()
  }

  heuristic (node) {
    return Math.max(0, this.distance - Math.hypot(node.x - this.from.x, node.z - this.from.z))
  }

  isEnd (node) {
    return Math.hypot(node.x - this.from.x, node.z - this.from.z) >= this.distance
  }

  hasChanged () {
    if (this.entity.position.distanceTo(this.from) < REPLAN_MOVE) return false
    this.from = this.entity.position.clone()
    return true
  }

  isValid () {
    return this.entity.isValid !== false
  }
}

async function goto (bot, goal) {
  try {
    await bot.pathfinder.goto(goal)
  } finally {
    bot.pathfinder.setGoal(null)
  }
}

const goNear = (bot, pos, range) => goto(bot, new goals.GoalNear(pos.x, pos.y, pos.z, range))

// Threats that can reach the bot: none from outside while it is in the closed house
export function reachableThreats (bot, state) {
  return threats(bot).filter(({ e }) => !shelteredFrom(bot, state.home, e))
}

export const bestWeapon = (bot) => {
  const inv = inventoryCounts(bot)
  const name = WEAPONS.find((w) => inv[w])
  return name ? bot.inventory.items().find((i) => i.name === name) : null
}

export async function fight (bot, target, signal) {
  const start = Date.now()
  try {
    while (bot.entities[target.id] && Date.now() - start < FIGHT_TIMEOUT_MS && !signal.aborted) {
      if (target.position.distanceTo(bot.entity.position) > 3) {
        bot.pathfinder.setGoal(new goals.GoalFollow(target, 2), true)
      } else {
        await bot.lookAt(target.position.offset(0, target.height * 0.8, 0), true)
        bot.attack(target)
      }
      await bot.waitForTicks(12)
    }
  } finally {
    bot.pathfinder.setGoal(null)
  }
  return bot.entities[target.id] ? `${target.name} still alive` : `${target.name} gone (killed or despawned)`
}

export async function flee (bot, h, signal) {
  bot.pathfinder.setGoal(new GoalAwayFrom(h, FLEE_DISTANCE + REPLAN_MOVE), true)
  const start = Date.now()
  try {
    while (Date.now() - start < FLEE_TIMEOUT_MS && !signal.aborted && bot.entities[h.id] && h.position.distanceTo(bot.entity.position) < FLEE_DISTANCE) {
      await bot.waitForTicks(5)
    }
  } finally {
    bot.pathfinder.setGoal(null)
  }
  return `now ${round(h.position.distanceTo(bot.entity.position))}m from ${h.name}`
}

// Resolves when the bot picks something up, or after ms
function waitForCollect (bot, ms) {
  return new Promise((resolve) => {
    const onCollect = (collector) => {
      if (collector !== bot.entity) return
      clearTimeout(timer)
      bot.off('playerCollect', onCollect)
      resolve(true)
    }
    const timer = setTimeout(() => { bot.off('playerCollect', onCollect); resolve(false) }, ms)
    bot.on('playerCollect', onCollect)
  })
}

export function nearbyDrops (bot, state, radius) {
  return nearbyEntities(bot).filter(({ e, dist }) => e.name === 'item' && dist <= radius &&
    Math.abs(e.position.y - bot.entity.position.y) < 4 && !state.unreachableDrops.has(e.id))
}

// Drops can be picked up only after a short delay; standing on them already, the walk ends at
// once. Collect the drops that fell nearby and wait for each pickup.
async function collectNearbyDrops (bot, state) {
  for (let i = 0; i < 3; i++) {
    await bot.waitForTicks(10)
    const drop = nearbyDrops(bot, state, DROP_COLLECT_RADIUS)[0]
    if (!drop) return
    const collected = waitForCollect(bot, PICKUP_WAIT_MS)
    await goNear(bot, drop.e.position, 0.5).catch(() => {})
    await collected
  }
}

const itemCount = (bot, name) => bot.inventory.items().filter((i) => i.name === name).reduce((n, i) => n + i.count, 0)
const totalItems = (bot) => bot.inventory.items().reduce((n, i) => n + i.count, 0)

export function findTable (bot) {
  return bot.findBlock({ matching: bot.registry.blocksByName.crafting_table.id, maxDistance: TABLE_SEARCH_RADIUS })
}

// Equip the fastest tool for the block, if any is held
async function equipToolFor (bot, block) {
  const tool = bot.pathfinder.bestHarvestTool(block)
  if (tool) await bot.equip(tool, 'hand')
}

// Executors: (bot, state, candidate, signal) -> result string; the candidate holds the target
export const PRIMITIVES = {
  async dig (bot, state, c, signal) {
    const block = bot.blockAt(c.pos)
    if (!block || block.name !== c.block) throw new Error(`${c.block} is gone from ${c.pos}`)
    const before = totalItems(bot)
    // Stand where the block is in reach, not merely near it: a log above head height is otherwise
    // "near" only from the leaves of the tree.
    await goto(bot, new goals.GoalLookAtBlock(c.pos, bot.world, { reach: REACH }))
    signal.throwIfAborted()
    // Digging while airborne is 5x slower (e.g. right after chopping the block we stood on)
    for (let i = 0; i < 40 && !bot.entity.onGround; i++) await bot.waitForTicks(1)
    await equipToolFor(bot, block)
    await bot.dig(bot.blockAt(c.pos), true)
    signal.throwIfAborted()
    await collectNearbyDrops(bot, state)
    const gained = totalItems(bot) - before
    if (gained <= 0) throw new Error(`dug ${c.block} but picked nothing up`)
    return `dug ${c.block}, picked up ${gained} items`
  },
  async pickup (bot, state, c) {
    const drop = bot.entities[c.entityId]
    if (!drop) throw new Error('the item is gone')
    const before = totalItems(bot)
    const collected = waitForCollect(bot, PICKUP_WAIT_MS)
    try {
      await goNear(bot, drop.position, 0.5)
    } catch (e) {
      state.unreachableDrops.add(c.entityId)
      throw e
    }
    await collected
    const gained = totalItems(bot) - before
    if (gained <= 0) {
      state.unreachableDrops.add(c.entityId)
      throw new Error('reached the item but picked nothing up')
    }
    return `picked up ${gained} items`
  },
  async attack (bot, state, c, signal) {
    const target = bot.entities[c.entityId]
    if (!target) throw new Error(`${c.target} is gone`)
    const weapon = bestWeapon(bot)
    if (weapon) await bot.equip(weapon, 'hand')
    const before = totalItems(bot)
    const result = await fight(bot, target, signal)
    if (bot.entities[c.entityId]) throw new Error(result)
    if (!c.hostile) await collectNearbyDrops(bot, state)
    const gained = totalItems(bot) - before
    return gained > 0 ? `${result}; picked up ${gained} items` : result
  },
  async flee (bot, state, c, signal) {
    const h = bot.entities[c.entityId]
    if (!h) return `${c.target} is gone`
    return await flee(bot, h, signal)
  },
  async eat (bot, state, c) {
    const food = bot.inventory.items().find((i) => i.name === c.item)
    if (!food) throw new Error(`no ${c.item}`)
    const before = bot.food
    await bot.equip(food, 'hand')
    await bot.consume()
    return `ate ${c.item}, hunger ${before} -> ${bot.food}`
  },
  async equip (bot, state, c) {
    const item = bot.inventory.items().find((i) => i.name === c.item)
    if (!item) throw new Error(`no ${c.item}`)
    await bot.equip(item, 'hand')
    return `holding ${c.item}`
  },
  async craft (bot, state, c, signal) {
    // Holding a new ingredient unlocks recipes; the server sends them right after
    for (let i = 0; i < RECIPE_UNLOCK_TICKS && !state.recipeBook.recipesFor(c.item).length; i++) await bot.waitForTicks(1)
    let table = null
    if (c.needsTable) {
      table = findTable(bot)
      if (!table) throw new Error('no crafting table nearby')
      await goNear(bot, table.position, 3)
    }
    const before = itemCount(bot, c.item)
    for (let i = 0; i < c.times; i++) {
      signal.throwIfAborted()
      await craftWithRecipeBook(bot, state.recipeBook, c.item, { table })
    }
    return `crafted ${itemCount(bot, c.item) - before} ${c.item}`
  },
  async place_table (bot, state, c) {
    const item = bot.inventory.items().find((i) => i.name === 'crafting_table')
    if (!item) throw new Error('no crafting_table')
    const me = bot.entity.position.floored()
    for (const [dx, dz] of [[2, 0], [-2, 0], [0, 2], [0, -2], [2, 2], [-2, -2], [2, -2], [-2, 2]]) {
      const pos = me.offset(dx, 0, dz)
      const ground = bot.blockAt(pos.offset(0, -1, 0))
      const spot = bot.blockAt(pos)
      // Never inside the house or on its planned footprint
      if (inHouse(state, pos, 1) || !ground || ground.boundingBox !== 'block' || !spot || spot.name !== 'air') continue
      await bot.equip(item, 'hand')
      await bot.placeBlock(ground, { x: 0, y: 1, z: 0 })
      return `placed crafting_table at ${pos}`
    }
    throw new Error('no free spot for the crafting table')
  },
  async place_plan (bot, state, c) {
    const plan = state.plan
    if (!plan.origin) {
      const origin = findSite(bot, plan.size)
      if (!origin) {
        plan.siteSearchFailedAt = bot.entity.position.clone()
        throw new Error(`no flat ${plan.size.width}x${plan.size.depth} site nearby; explore elsewhere`)
      }
      plan.origin = origin
    }
    const b = plan.pending(bot)[0]
    if (!b) return 'the plan is complete'
    await placeOne(bot, plan, b)
    const s = plan.status(bot)
    return `placed ${b.block} (${s.placed}/${s.total})`
  },
  async place_bed (bot, state) {
    await enterHome(bot, state.home)
    const spot = bedSpot(bot, state.home)
    if (!spot) throw new Error('no free spot for the bed in the house')
    const { inside } = state.home
    await goto(bot, new goals.GoalBlock(inside.x, inside.y, inside.z))
    // The bed's head goes one block further in the direction the player faces. The rotation reaches
    // the server only with the next movement packet: placed right away, the server used the yaw
    // from closing the door and the bed's head blocked the cell inside the door.
    const bed = bot.inventory.items().find((i) => i.name.endsWith('_bed'))
    await bot.equip(bed, 'hand')
    await bot.lookAt(spot.foot.offset(0.5, 0, 0.5), true)
    await bot.waitForTicks(2)
    await bot.placeBlock(bot.blockAt(spot.foot.offset(0, -1, 0)), { x: 0, y: 1, z: 0 })
    // placeBlock waits for the clicked cell only; the head's update comes separately
    const head = spot.foot.plus(spot.inward)
    for (let i = 0; i < 20 && !bot.blockAt(head)?.name.endsWith('_bed'); i++) await bot.waitForTicks(1)
    if (!bot.blockAt(spot.foot)?.name.endsWith('_bed')) throw new Error('the bed was not placed')
    if (!bot.blockAt(head)?.name.endsWith('_bed')) throw new Error(`the bed was placed facing the wrong way (head not at ${head})`)
    state.home.bed = spot.foot
    return `placed ${bot.blockAt(spot.foot).name} in the house`
  },
  async sleep (bot, state, c, signal) {
    await enterHome(bot, state.home)
    await bot.sleep(bot.blockAt(state.home.bed))
    // The night is skipped once every player sleeps; the server then wakes the bot
    while (bot.isSleeping && !signal.aborted) await bot.waitForTicks(10)
    if (bot.isSleeping) await bot.wake()
    // The server wakes the bot before it sends the new time: wait for the next update, then check
    // the night really passed (being woken by a monster also ends the sleep)
    await once(bot, 'time', { signal: AbortSignal.timeout(TIME_UPDATE_TIMEOUT_MS) })
    const tod = bot.time.timeOfDay
    if (tod >= SLEEP_FROM && tod <= SLEEP_UNTIL) throw new Error(`woke up but the night was not skipped (time ${tod})`)
    return `slept through the night; time ${tod}`
  },
  async exit_wall (bot, state, c, signal) {
    await exitThroughWall(bot, state.home, c.spot, signal)
    signal.throwIfAborted()
    await collectNearbyDrops(bot, state) // the dug blocks, to close the wall with
    await repairWall(bot, state.home)
    return `left the house through the ${c.target} and closed it behind`
  },
  async repair_wall (bot, state) {
    await repairWall(bot, state.home)
    return 'the house wall is closed again'
  },
  async go_home (bot, state) {
    await enterHome(bot, state.home)
    return 'inside the house with the door closed'
  },
  async wait (bot, state, c, signal) {
    if (c.inside) {
      await enterHome(bot, state.home) // closes the door if it was left open
      // Face the door and keep still, glancing aside now and then (turning every second made the
      // stream view spin the whole night)
      await bot.lookAt(state.home.door.offset(0.5, 1.2, 0.5))
    }
    const facing = bot.entity.yaw
    for (let i = 0; i < WAIT_TICKS / GLANCE_TICKS && !signal.aborted; i++) {
      await bot.waitForTicks(GLANCE_TICKS)
      await bot.look(facing + (i % 2 ? 0 : (Math.random() - 0.5) * GLANCE_ANGLE), 0)
    }
    const door = state.home ? `, door ${isDoorOpen(bot, state.home) ? 'open' : 'closed'}` : ''
    return `waited (time ${bot.time.timeOfDay}${door})`
  },
  async explore (bot, state, c) {
    const start = bot.entity.position.clone()
    const target = start.offset(c.dx * EXPLORE_DISTANCE, 0, c.dz * EXPLORE_DISTANCE)
    let error = ''
    try {
      await goto(bot, new goals.GoalNearXZ(target.x, target.z, 3))
    } catch (e) {
      error = e.message
    }
    const moved = bot.entity.position.xzDistanceTo(start)
    if (moved < EXPLORE_MIN_PROGRESS) throw new Error(`could not walk ${c.target} from ${round(start.x)},${round(start.z)}${error ? `: ${error}` : ''}`)
    return `walked ${round(moved)}m ${c.target}`
  }
}

// Which primitive answers damage itself: taking damage does not interrupt it
export const DAMAGE_TOLERANT = new Set(['attack', 'flee'])

export const TIMEOUTS_MS = { go_home: 45000, place_bed: 45000, sleep: 45000, explore: 30000, exit_wall: 30000 }
export const DEFAULT_TIMEOUT_MS = 20000

