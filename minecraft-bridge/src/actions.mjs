// Bounded actions: enumerate what is executable right now, and execute one to completion.
//
// Only actions whose preconditions hold are listed, so the decision maker can never pick
// something impossible. Executors take an AbortSignal (timeout, or damage taken) and must stop
// promptly once it fires: loops check it, and the caller cancels pathing and digging.

import { once } from 'node:events'
import pathfinderPkg from 'mineflayer-pathfinder'
import {
  THREAT_RADIUS, DROP_RADIUS, round, dayPhase, isLog, isPlanks, inventoryCounts, countMatching, nearbyEntities, threats, isHostile
} from './observe.mjs'
import { buildStep, canBuildNow } from './build.mjs'
import { craftWithRecipeBook } from './craft.mjs'
import { isInside, shelteredFrom, enterHome, isDoorOpen, dangerOutside, bedSpot, hasBed } from './home.mjs'

const { Movements, goals } = pathfinderPkg
export const ACTION_TIMEOUT_MS = 20000
// Actions that go home first may walk back from far away; stays below the Python client's HTTP
// timeout (60s)
export const ACTION_TIMEOUTS_MS = { go_home: 45000, place_bed: 45000, sleep: 45000 }
const LOG_SEARCH_RADIUS = 32
const MAX_LOG_HEIGHT = 4 // blocks above the ground the bot can chop without climbing
const TABLE_SEARCH_RADIUS = 32
const FLEE_DISTANCE = 16
const FIGHT_TIMEOUT_MS = 10000
const FLEE_TIMEOUT_MS = 8000
const ANIMAL_SEARCH_RADIUS = 32
const FOOD_STOCK = 4 // hunting stops being offered with this many food items
const STAY_INSIDE_TICKS = 200
const PICKUP_WAIT_MS = 1500
const GLANCE_TICKS = 50
const GLANCE_ANGLE = Math.PI / 3
const HOSTILE_AVOID_RADIUS = 5
const HOSTILE_STEP_COST = 20
const HOSTILE_CACHE_MS = 1000
const REACH = 4.5 // survival block reach from the eyes
const LEAVES_STEP_COST = 10
const EXPLORE_DISTANCE = 20
const EXPLORE_ATTEMPTS = 3
const EXPLORE_MIN_PROGRESS = 5

// Rabbits are left out: they outrun the bot
const FOOD_ANIMALS = new Set(['cow', 'pig', 'sheep', 'chicken', 'mooshroom'])
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

// Drops can be picked up only after a short delay; standing on them already, the walk ends at
// once. Collect the drops nearby and wait for each pickup.
async function collectNearbyDrops (bot, state) {
  for (let i = 0; i < 3; i++) {
    await bot.waitForTicks(10)
    const drop = nearestDrop(bot, state)
    if (!drop || drop.dist > 8) return
    const collected = waitForCollect(bot, PICKUP_WAIT_MS)
    await goNear(bot, drop.e.position, 0.5).catch(() => {})
    await collected
  }
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

function nearestAnimal (bot) {
  const me = bot.entity.position
  return nearbyEntities(bot).find(({ e, dist }) => FOOD_ANIMALS.has(e.name) && dist <= ANIMAL_SEARCH_RADIUS && Math.abs(e.position.y - me.y) < 4)?.e ?? null
}

const foodCount = (bot) => bot.inventory.items().filter((i) => isFood(bot, i)).reduce((n, i) => n + i.count, 0)
export const HOME_ACTIONS = new Set(['go_home', 'stay_inside', 'place_bed', 'sleep'])

const WOOL_PER_BED = 3
const PLANKS_PER_BED = 3
// Sleeping is possible from this time of day (Mineflayer's own check: 12541..23458)
const SLEEP_FROM = 12541
const SLEEP_UNTIL = 23458
const TIME_UPDATE_TIMEOUT_MS = 3000 // the server sends the time every second

// The wool color held most, e.g. ['white_wool', 2]
function mostWool (bot) {
  return Object.entries(inventoryCounts(bot)).filter(([n]) => n.endsWith('_wool')).sort((a, b) => b[1] - a[1])[0] ?? null
}
const bedItem = (bot) => bot.inventory.items().find((i) => i.name.endsWith('_bed'))
const needsBed = (bot, state) => !hasBed(bot, state.home) && !bedItem(bot)

function nearestSheep (bot) {
  const me = bot.entity.position
  return nearbyEntities(bot).find(({ e, dist }) => e.name === 'sheep' && dist <= ANIMAL_SEARCH_RADIUS && Math.abs(e.position.y - me.y) < 4)?.e ?? null
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

// Planks beyond what the house plan still needs (the sword takes 2 planks + a stick from 2 planks)
function spareForSword (bot, state) {
  const needed = state.plan && !state.plan.status(bot).complete ? (state.plan.materialsNeeded(bot).planks ?? 0) : 0
  const sticks = countMatching(bot, (n) => n === 'stick')
  return countMatching(bot, isPlanks) - needed >= (sticks ? 2 : 4)
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
  const t = reachableThreats(bot, state)
  if (t.length) {
    const h = t[0]
    acts.push({ id: 'attack_hostile', description: `Fight the nearest hostile mob (${h.e.name}) with the held item` })
    acts.push({ id: 'flee_hostile', description: `Run away from the nearest hostile mob (${h.e.name})` })
  }
  const weapon = bestWeapon(bot)
  if (weapon && bot.heldItem?.name !== weapon.name) acts.push({ id: 'equip_weapon', description: `Hold ${weapon.name} in hand` })
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
  if (table && !weapon && spareForSword(bot, state) && book.recipesFor('stick').length) {
    acts.push({ id: 'craft_sword', description: 'Craft a wooden sword at the crafting table' })
  }
  if (foodCount(bot) < FOOD_STOCK && nearestAnimal(bot)) {
    acts.push({ id: 'hunt_animal', description: `Hunt the nearest ${nearestAnimal(bot).name} for food` })
  }
  if (needsBed(bot, state) && (mostWool(bot)?.[1] ?? 0) < WOOL_PER_BED && nearestSheep(bot)) {
    acts.push({ id: 'hunt_sheep', description: 'Hunt the nearest sheep for wool (a bed takes 3 of one color)' })
  }
  const wool = mostWool(bot)
  if (table && needsBed(bot, state) && wool && wool[1] >= WOOL_PER_BED && planks && planks[1] >= PLANKS_PER_BED) {
    acts.push({ id: 'craft_bed', description: `Craft a bed from 3 ${wool[0]} at the crafting table` })
  }
  if (bedItem(bot) && state.home && !hasBed(bot, state.home) && bedSpot(bot, state.home)) {
    acts.push({ id: 'place_bed', description: 'Place the bed inside the house' })
  }
  const tod = bot.time.timeOfDay
  if (hasBed(bot, state.home) && tod >= SLEEP_FROM && tod <= SLEEP_UNTIL && !bot.isSleeping) {
    acts.push({ id: 'sleep', description: 'Sleep in the bed to skip the night' })
  }
  const phase = dayPhase(bot.time.timeOfDay)
  const inside = isInside(bot, state.home)
  if (state.home && !inside && (phase === 'dusk' || phase === 'night')) {
    acts.push({ id: 'go_home', description: 'Go into the house and close the door' })
  }
  const waiting = dangerOutside(bot, state.home)
  if (inside && (phase !== 'day' || waiting.length)) {
    const why = waiting.length ? `${waiting[0].e.name} waiting outside` : 'until morning'
    acts.push({ id: 'stay_inside', description: `Wait inside the closed house (${why})` })
  }
  acts.push({ id: 'explore', description: 'Walk about 20m in a random direction' })
  acts.push({ id: 'idle', description: 'Stay still and look around' })
  return acts
}

export const EXECUTORS = {
  async attack_hostile (bot, state, signal) {
    const target = reachableThreats(bot, state)[0]?.e
    if (!target) return 'no threat in range'
    return await fight(bot, target, signal)
  },
  async flee_hostile (bot, state, signal) {
    const h = reachableThreats(bot, state)[0]?.e
    if (!h) return 'no threat in range'
    return await flee(bot, h, signal)
  },
  async equip_weapon (bot) {
    const w = bestWeapon(bot)
    await bot.equip(w, 'hand')
    return `holding ${w.name}`
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
  async craft_sword (bot, state, signal) {
    const table = findTable(bot)
    if (countMatching(bot, (n) => n === 'stick') < 1) {
      await craftWithRecipeBook(bot, state.recipeBook, 'stick')
      // Holding a stick unlocks the sword recipe; the server sends it right after
      for (let i = 0; i < 40 && !state.recipeBook.recipesFor('wooden_sword').length; i++) await bot.waitForTicks(1)
    }
    signal.throwIfAborted()
    await goNear(bot, table.position, 3)
    await craftWithRecipeBook(bot, state.recipeBook, 'wooden_sword', { table })
    await bot.equip(bestWeapon(bot), 'hand')
    return 'crafted and holding wooden_sword'
  },
  async hunt_animal (bot, state, signal) {
    const animal = nearestAnimal(bot)
    if (!animal) return 'no animal nearby'
    const before = foodCount(bot)
    const weapon = bestWeapon(bot)
    if (weapon) await bot.equip(weapon, 'hand')
    const result = await fight(bot, animal, signal)
    await collectNearbyDrops(bot, state)
    if (foodCount(bot) <= before) throw new Error(`no food gained (${result})`)
    return `${result}; food items ${before} -> ${foodCount(bot)}`
  },
  async hunt_sheep (bot, state, signal) {
    const sheep = nearestSheep(bot)
    if (!sheep) return 'no sheep nearby'
    const before = mostWool(bot)?.[1] ?? 0
    const weapon = bestWeapon(bot)
    if (weapon) await bot.equip(weapon, 'hand')
    const result = await fight(bot, sheep, signal)
    await collectNearbyDrops(bot, state)
    const after = mostWool(bot)?.[1] ?? 0
    if (after <= before) throw new Error(`no wool gained (${result})`)
    return `${result}; ${mostWool(bot)[0]} ${after}`
  },
  async craft_bed (bot, state) {
    const table = findTable(bot)
    const bedName = mostWool(bot)[0].replace(/_wool$/, '_bed')
    await goNear(bot, table.position, 3)
    await craftWithRecipeBook(bot, state.recipeBook, bedName, { table })
    return `crafted ${bedName}`
  },
  async place_bed (bot, state) {
    await enterHome(bot, state.home)
    const spot = bedSpot(bot, state.home)
    if (!spot) throw new Error('no free spot for the bed in the house')
    const { inside } = state.home
    try {
      await bot.pathfinder.goto(new goals.GoalBlock(inside.x, inside.y, inside.z))
    } finally {
      bot.pathfinder.setGoal(null)
    }
    // The bed extends away from the player, in the direction it faces
    await bot.lookAt(spot.foot.offset(0.5, 0, 0.5), true)
    await bot.equip(bedItem(bot), 'hand')
    await bot.placeBlock(bot.blockAt(spot.foot.offset(0, -1, 0)), { x: 0, y: 1, z: 0 })
    if (!bot.blockAt(spot.foot)?.name.endsWith('_bed')) throw new Error('the bed was not placed')
    state.home.bed = spot.foot
    return `placed ${bot.blockAt(spot.foot).name} in the house`
  },
  async sleep (bot, state, signal) {
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
  async go_home (bot, state) {
    await enterHome(bot, state.home)
    return 'inside the house with the door closed'
  },
  async stay_inside (bot, state, signal) {
    await enterHome(bot, state.home) // closes the door if it was left open
    // Face the door and keep still, glancing aside now and then (turning every second made the
    // stream view spin the whole night)
    await bot.lookAt(state.home.door.offset(0.5, 1.2, 0.5))
    const facing = bot.entity.yaw
    for (let i = 0; i < STAY_INSIDE_TICKS / GLANCE_TICKS && !signal.aborted; i++) {
      await bot.waitForTicks(GLANCE_TICKS)
      await bot.look(facing + (i % 2 ? 0 : (Math.random() - 0.5) * GLANCE_ANGLE), 0)
    }
    return `waited inside (time ${bot.time.timeOfDay}, door ${isDoorOpen(bot, state.home) ? 'open' : 'closed'})`
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
