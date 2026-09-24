// Candidates: concrete primitive actions the selector may pick from, grounded in the world.
//
// They come from the goal's leaves (what advances it now) and from the body's needs (threats,
// hunger, a weapon, drops nearby), plus waiting. Safety is enforced here, not left to the selector:
// while sheltering in the house (at night, or with a hostile at the door) nothing outside is
// offered. The selector did pick "go dig a log outside" at night in 5 of 9 trials.
//
// A candidate id names its target (a position or an entity id) so it stays the same until the
// next /act, which grounds the candidates again and runs the one with that id.

import { round, bearing, dayPhase } from './observe.mjs'
import { isInside, dangerOutside } from './home.mjs'
import { reachableThreats, bestWeapon, nearbyDrops, findTable } from './primitives.mjs'

const DROP_RADIUS = 16
const DROPS_OFFERED = 3
const THREATS_OFFERED = 2
const EXPLORE_DIRECTIONS = { north: [0, -1], east: [1, 0], south: [0, 1], west: [-1, 0] }

const fmt = (p) => `${p.x},${p.y},${p.z}`
const dist = (bot, p) => round(bot.entity.position.distanceTo(p))

export function ground (bot, state, knowledge, world, status) {
  const out = []
  for (const leaf of status?.leaves ?? []) out.push(...fromLeaf(bot, state, world, leaf))
  out.push(...forNeeds(bot, state, knowledge))
  const inside = isInside(bot, state.home)
  out.push({ id: inside ? 'wait inside' : 'wait', verb: 'wait', target: inside ? 'inside the house' : 'here', inPlace: true, inside, seconds: 10 })

  const unique = [...new Map(out.map((c) => [c.id, c])).values()]
  const sheltering = inside && (dayPhase(bot.time.timeOfDay) !== 'day' || dangerOutside(bot, state.home).length > 0)
  return sheltering ? unique.filter((c) => !needsOutside(c, state.home)) : unique
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
    case 'explore':
      return Object.entries(EXPLORE_DIRECTIONS)
        .filter(([, [dx, dz]]) => !leaf.away || awayFrom(bot, leaf.away, dx, dz))
        .map(([dir, [dx, dz]]) => ({
          id: `explore ${dir}`, verb: 'explore', target: dir, looking_for: leaf.sources.length ? leaf.sources.join('/') : leaf.item, dx, dz
        }))
    case 'craft': {
      const table = leaf.needsTable ? findTable(bot) : null
      return [{
        id: `craft ${leaf.item} x${leaf.times}`, verb: 'craft', target: leaf.item, item: leaf.item, times: leaf.times, needsTable: leaf.needsTable,
        ...(table ? { pos: table.position, distance: dist(bot, table.position) } : { inPlace: !leaf.needsTable })
      }]
    }
    case 'place':
      return [{ id: 'place crafting_table nearby', verb: 'place_table', target: 'crafting_table' }]
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
    case 'place_bed':
      return [{ id: 'place the bed in the house', verb: 'place_bed', target: 'bed', inPlace: true, distance: dist(bot, state.home.inside) }]
    case 'go_home':
      return [{ id: 'go home', verb: 'go_home', target: 'home', inPlace: true, distance: dist(bot, state.home.inside) }]
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
    if (best) out.push({ id: `eat ${best.name}`, verb: 'eat', target: best.name, item: best.name, inPlace: true })
  }
  const weapon = bestWeapon(bot)
  if (weapon && bot.heldItem?.name !== weapon.name) out.push({ id: `equip ${weapon.name}`, verb: 'equip', target: weapon.name, item: weapon.name, inPlace: true })
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

// What the selector sees of a candidate (no positions objects or entity references)
export function describe (c) {
  const { id, pos, entityId, inPlace, inside, dx, dz, block, needsTable, item, ...rest } = c
  return rest
}
