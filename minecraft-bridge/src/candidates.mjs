// Candidates: concrete primitive actions the selector may pick from, grounded in the world.
//
// They come from the goal's leaves (what advances it now) and from the body's needs (threats,
// hunger, a weapon, drops nearby, a hole in the wall), plus waiting. Safety is enforced here, not
// left to the selector: while sheltering in the house (at night, or with a hostile at the door)
// nothing outside is offered. The selector did pick "go dig a log outside" at night in 5 of 9 trials.
// What is held back is reported (it is why the goal does not advance), and by day there are ways
// out: fighting what waits at the door (the cleared goal) or digging an exit through another wall.
// Without them the bot stayed inside all morning while skeletons stood near the door.
//
// A candidate id names its target (a position or an entity id) so it stays the same until the
// next /act, which grounds the candidates again and runs the one with that id.

import vec3Pkg from 'vec3'
import { round, bearing, dayPhase, burningInDaylight, isDark, inventoryCounts } from './observe.mjs'
import { isInside, dangerOutside, exitSpots } from './home.mjs'
import { recall, visited, chests, chestWith } from './memory.mjs'
import { reachableThreats, bestWeapon, nearbyDrops, findTable, findFurnace, torchSpot, HEALTH_CRITICAL, HUNGER_URGENT, EXPLORE_DISTANCE } from './primitives.mjs'

const { Vec3 } = vec3Pkg
const DROP_RADIUS = 16
const DROPS_OFFERED = 3
const THREATS_OFFERED = 2
const EXPLORE_DIRECTIONS = { north: [0, -1], east: [1, 0], south: [0, 1], west: [-1, 0] }
// Eaten only when starving and nothing better is held: 4 hunger points against a likely short
// Hunger effect (frun2: it held rotten flesh and starved)
const LAST_RESORT_FOOD = ['rotten_flesh']

const fmt = (p) => `${p.x},${p.y},${p.z}`
const dist = (bot, p) => round(bot.entity.position.distanceTo(p))

// { candidates, withheld }: withheld says what the shelter rule held back, if anything
export function ground (bot, state, knowledge, world, status) {
  const out = []
  for (const leaf of status?.leaves ?? []) out.push(...fromLeaf(bot, state, world, leaf))
  out.push(...forNeeds(bot, state, knowledge))
  const home = state.home
  const inside = isInside(bot, home)
  const day = dayPhase(bot.time.timeOfDay) === 'day'
  const danger = dangerOutside(bot, home)
  const sheltering = inside && (!day || danger.length > 0)
  // The first of a kind wins: a goal's candidate carries what the goal allows (confront)
  const unique = [...out.reduce((m, c) => m.has(c.id) ? m : m.set(c.id, c), new Map()).values()]
  const safe = sheltering ? unique.filter((c) => c.confront || !needsOutside(c, home)) : unique
  const held = unique.length - safe.length
  let withheld = null
  if (held && !day) withheld = `staying inside for the night: ${held} actions outside are held back until morning`
  if (held && day) {
    withheld = `staying inside while ${danger.map(({ e }) => e.name).join(', ')} wait near the door: ${held} actions outside are held back ` +
      '(cleared() goes out to fight them; an exit can be dug through another wall)'
    if (!home.breach.length) {
      for (const spot of exitSpots(bot, home, danger.map(({ e }) => e))) {
        safe.push({ id: `exit through the ${spot.side} wall`, verb: 'exit_wall', target: `${spot.side} wall`, inPlace: true, spot, nearest_hostile: round(spot.distance) })
      }
    }
  }
  // Waiting is offered for what the time changes (healing, the morning, a mob burning in the sun),
  // or when nothing else can be done. Offered without a purpose, the selector waited inside while
  // nothing changed: 9 steps in a row by day, and 6 of 6 times with husks at the door and full health.
  safe.push(...(inside ? waitsInside(bot, danger) : []))
  if (!safe.length) {
    safe.push({ id: inside ? 'wait inside' : 'wait', verb: 'wait', target: inside ? 'inside the house' : 'here', inPlace: true, inside, seconds: 10, purpose: 'nothing else can be done now' })
  }
  return { candidates: safe, withheld }
}

// Natural regeneration needs a nearly full hunger bar
const REGEN_FOOD = 18
const MAX_HEALTH = 20

function waitsInside (bot, danger) {
  const wait = (id, purpose) => ({ id, verb: 'wait', target: 'inside the house', inPlace: true, inside: true, seconds: 10, purpose })
  const out = []
  if (dayPhase(bot.time.timeOfDay) !== 'day') out.push(wait('wait inside until morning', 'the night passes'))
  if (bot.health < MAX_HEALTH && bot.food >= REGEN_FOOD) out.push(wait('wait inside to heal', `health ${round(bot.health)}/${MAX_HEALTH} regenerates`))
  const burning = danger.filter(({ e }) => burningInDaylight(bot, e)).map(({ e }) => e.name)
  if (burning.length) out.push(wait('wait inside while they burn', `${burning.join(', ')} at the door burn in the sunlight`))
  return out
}

// Whether running the candidate takes the bot out of the house (it then leaves through the door
// first). Candidates without a position act around the bot outside (exploring, placing a table).
export function needsOutside (c, home) {
  if (c.inPlace) return false
  return !c.pos || !isInside({ entity: { position: c.pos } }, home)
}

function fromLeaf (bot, state, world, leaf) {
  switch (leaf.kind) {
    case 'dig':
      return leaf.sources.flatMap((name) => world.dig(name).map((p) => ({
        id: `dig ${name} at ${fmt(p)}`, verb: 'dig', target: name, distance: dist(bot, p), pos: p, block: name
      })))
    case 'kill':
      return leaf.sources.flatMap((name) => world.hunt(name).map(({ e, dist: d }) => ({
        id: `attack ${name} #${e.id}`, verb: 'attack', target: name, distance: round(d), pos: e.position, entityId: e.id, hostile: false
      })))
    case 'explore': {
      const lookingFor = leaf.sources.length ? leaf.sources.join('/') : leaf.item
      const me = bot.entity.position
      // Where it was seen before comes first; then directions, marking ground already covered
      const recalled = state.memory
        ? recall(state.memory, leaf.sources, me, Number(bot.time.age)).map((p) => ({
          id: `go to ${p.kind} seen at ${p.x},${p.z}`, verb: 'goto_memory', target: p.kind, pos: new Vec3(p.x, p.y, p.z),
          distance: p.distance, seen_minutes_ago: p.minutesAgo, count: p.count
        }))
        : []
      const directions = Object.entries(EXPLORE_DIRECTIONS)
        .filter(([, [dx, dz]]) => !leaf.away || awayFrom(bot, leaf.away, dx, dz))
        .map(([dir, [dx, dz]]) => ({
          id: `explore ${dir}`, verb: 'explore', target: dir, looking_for: lookingFor, dx, dz,
          been_there: !!state.memory && visited(state.memory, me.offset(dx * EXPLORE_DISTANCE, 0, dz * EXPLORE_DISTANCE))
        }))
        .sort((a, b) => a.been_there - b.been_there)
      return [...recalled, ...directions]
    }
    case 'craft': {
      const table = leaf.needsTable ? findTable(bot) : null
      return [{
        id: `craft ${leaf.item} x${leaf.times}`, verb: 'craft', target: leaf.item, item: leaf.item, times: leaf.times, needsTable: leaf.needsTable,
        ...(table ? { pos: table.position, distance: dist(bot, table.position) } : { inPlace: !leaf.needsTable })
      }]
    }
    case 'place':
      return [{ id: `place ${leaf.item} nearby`, verb: 'place_station', target: leaf.item, item: leaf.item }]
    case 'light':
      return [{ id: `place a torch at ${fmt(leaf.pos)} (dark ground)`, verb: 'place_torch_at', target: 'torch', pos: leaf.pos, distance: dist(bot, leaf.pos) }]
    case 'smelt': {
      const furnace = findFurnace(bot)
      if (!furnace) return []
      const pos = furnace.position
      const where = { pos, distance: dist(bot, pos) }
      if (!leaf.count) return [{ id: `take ${leaf.item} from the furnace`, verb: 'smelt', target: leaf.item, item: leaf.item, ...where }]
      const inv = inventoryCounts(bot)
      const input = leaf.inputs.find((m) => inv[m] > 0)
      const fuel = leaf.fuels.find((m) => inv[m] >= leaf.fuelCount) ?? leaf.fuels.find((m) => inv[m] > 0)
      if (!input || !fuel) return []
      return [{ id: `smelt ${leaf.count} ${input} into ${leaf.item}`, verb: 'smelt', target: leaf.item, item: leaf.item, input, count: leaf.count, fuel, fuelCount: leaf.fuelCount, ...where }]
    }
    case 'place_plan': {
      const plan = state.plan
      const s = plan.status(bot)
      const p = plan.origin ? plan.worldPos(leaf.block) : null
      return [{
        id: p ? `place ${leaf.block.block} at ${fmt(p)}` : `place ${leaf.block.block} (start the house here)`,
        verb: 'place_plan', target: `${leaf.block.block} of the house`, progress: `${s.placed}/${s.total}`,
        ...(p ? { pos: p, distance: dist(bot, p) } : {})
      }]
    }
    case 'withdraw': {
      const chest = state.memory && chestWith(state.memory, [leaf.item], bot.entity.position)
      if (!chest) return []
      const pos = new Vec3(chest.x, chest.y, chest.z)
      return [{ id: `take ${leaf.count} ${leaf.item} from the chest at ${fmt(pos)}`, verb: 'withdraw', target: leaf.item, item: leaf.item, count: leaf.count, pos, distance: dist(bot, pos) }]
    }
    case 'deposit':
      return leaf.items.flatMap(({ item, count }) => toChest(bot, state, item, count))
    case 'place_chest':
      return [{ id: 'place a chest in the house', verb: 'place_chest', target: 'chest', pos: state.home.inside, distance: dist(bot, state.home.inside) }]
    case 'place_bed':
      return [{ id: 'place the bed in the house', verb: 'place_bed', target: 'bed', inPlace: true, distance: dist(bot, state.home.inside) }]
    case 'go_home':
      return [{ id: 'go home', verb: 'go_home', target: 'home', inPlace: true, distance: dist(bot, state.home.inside) }]
    case 'clear': {
      // Armed or not: the selector weighs it against the other ways (the health it sees, waiting,
      // an exit through the wall); a fight stops at critical health
      const weapon = bestWeapon(bot)?.name ?? 'none (fist)'
      return leaf.mobs.map((e) => ({
        id: `attack ${e.name} #${e.id}`, verb: 'attack', target: e.name, distance: dist(bot, e.position), pos: e.position, entityId: e.id,
        hostile: true, weapon, confront: true
      }))
    }
    case 'sleep':
      return [{ id: 'sleep in the bed', verb: 'sleep', target: 'bed', inPlace: true, effect: 'skips the night' }]
    default:
      return []
  }
}

function forNeeds (bot, state, knowledge) {
  const out = []
  for (const { e, dist } of reachableThreats(bot, state).slice(0, THREATS_OFFERED)) {
    const weapon = bestWeapon(bot)?.name ?? 'none (fist)'
    out.push({ id: `attack ${e.name} #${e.id}`, verb: 'attack', target: e.name, distance: round(dist), pos: e.position, entityId: e.id, hostile: true, weapon })
    out.push({ id: `flee from ${e.name} #${e.id}`, verb: 'flee', target: e.name, distance: round(dist), entityId: e.id, inPlace: false, pos: e.position })
  }
  if (bot.food < 20) {
    // Only what the food goals count as food: rotten flesh and the like do harm
    const edible = new Set(knowledge.resolve('food').members)
    const foods = bot.inventory.items().filter((i) => edible.has(i.name))
    const best = foods.sort((a, b) => bot.registry.foodsByName[b.name].foodPoints - bot.registry.foodsByName[a.name].foodPoints)[0]
    const lastResort = bot.food <= HUNGER_URGENT && bot.inventory.items().find((i) => LAST_RESORT_FOOD.includes(i.name))
    const eat = best ?? lastResort
    if (eat) out.push({ id: `eat ${eat.name}`, verb: 'eat', target: eat.name, item: eat.name, inPlace: true })
  }
  out.push(...storeSpare(bot, state, knowledge))
  const weapon = bestWeapon(bot)
  if (weapon && bot.heldItem?.name !== weapon.name) out.push({ id: `equip ${weapon.name}`, verb: 'equip', target: weapon.name, item: weapon.name, inPlace: true })
  // A fight given up at critical health (e.g. for cleared) needs a way back whatever the goal. Only
  // with danger around: healing needs food, not the house, so by day with no threat the search for
  // food goes on (going home each step kept the bot from ever finding any, frun3 and after)
  const danger = reachableThreats(bot, state).length > 0 || dayPhase(bot.time.timeOfDay) !== 'day'
  if (state.home && bot.health <= HEALTH_CRITICAL && danger && !isInside(bot, state.home)) {
    out.push({ id: 'go home', verb: 'go_home', target: 'home', inPlace: true, distance: dist(bot, state.home.inside) })
  }
  // Lighting the dark keeps mobs from spawning; once lit, it is offered again only farther on
  if (isDark(bot) && bot.inventory.items().some((i) => i.name === 'torch') && torchSpot(bot)) {
    out.push({ id: 'place a torch here', verb: 'place_torch', target: 'torch', inPlace: true })
  }
  const hole = state.home?.breach[0]
  if (hole) out.push({ id: 'close the hole in the house wall', verb: 'repair_wall', target: 'house wall', inPlace: true, at: fmt(hole) })
  for (const { e, dist } of nearbyDrops(bot, state, DROP_RADIUS).slice(0, DROPS_OFFERED)) {
    const item = e.getDroppedItem()?.name ?? 'item'
    out.push({ id: `pick up ${item} #${e.id}`, verb: 'pickup', target: item, distance: round(dist), direction: bearing(bot.entity.position, e.position), pos: e.position, entityId: e.id })
  }
  return out
}

function awayFrom (bot, start, dx, dz) {
  const vx = bot.entity.position.x - start.x
  const vz = bot.entity.position.z - start.z
  return Math.hypot(vx, vz) < 1 || vx * dx + vz * dz >= 0
}

// Putting an item in the nearest chest (the house's)
function toChest (bot, state, item, count) {
  const chest = state.memory && chests(state.memory).sort((a, b) => dist(bot, new Vec3(a.x, a.y, a.z)) - dist(bot, new Vec3(b.x, b.y, b.z)))[0]
  if (!chest) return []
  const pos = new Vec3(chest.x, chest.y, chest.z)
  return [{ id: `put ${count} ${item} in the chest at ${fmt(pos)}`, verb: 'deposit', target: item, item, count, pos, distance: dist(bot, pos) }]
}

// A full inventory in the house: the biggest stacks the goal does not need can go in the chest
const FREE_SLOTS_WANTED = 8
const STORE_OFFERED = 3
const TOOL = /_(sword|pickaxe|axe|shovel|hoe)$/
const GOAL_GROUPS = { built: ['log', 'planks', 'door'], placed: ['bed', 'wool', 'planks'] }

function storeSpare (bot, state, knowledge) {
  if (!knowledge || !state.memory || !chests(state.memory).length || !isInside(bot, state.home)) return []
  if (bot.inventory.emptySlotCount() >= FREE_SLOTS_WANTED) return []
  const spec = state.goal?.spec
  const groups = ['food', ...(GOAL_GROUPS[spec?.predicate] ?? []), ...(spec?.item ? [spec.item] : [])]
  const keep = new Set(groups.flatMap((g) => knowledge.resolve(g).members))
  const counts = {}
  for (const i of bot.inventory.items()) counts[i.name] = (counts[i.name] ?? 0) + i.count
  return Object.entries(counts)
    .filter(([name]) => !keep.has(name) && !TOOL.test(name) && !['chest', 'crafting_table'].includes(name))
    .sort((a, b) => b[1] - a[1])
    .slice(0, STORE_OFFERED)
    .flatMap(([name, n]) => toChest(bot, state, name, n))
}

// What the selector sees of a candidate (no positions objects or entity references)
export function describe (c) {
  const { id, pos, entityId, inPlace, inside, dx, dz, block, needsTable, item, spot, confront, ...rest } = c
  return rest
}
