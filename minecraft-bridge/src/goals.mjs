// Goals: the vocabulary Gemini sets goals in, whether a goal is met, and what can be done for it.
//
// A goal is judged from the world only (never from what the models say). Item needs are
// decomposed by the solver; the house, the home and the night add their own steps.
//
//   have(item, count)        hold `count` of an item or group (planks, log, door, bed, wool, food, ...)
//   built()                  every block of the house plan is in place
//   placed(item, where)      a bed in the home (the only placement supported so far)
//   at_home()                inside the house with the door closed
//   through_night()          the night has passed (inside the house, or asleep)
//   explored(distance)       this far (horizontally) from where the goal was set
//   cleared()                no hostile waits near the door (by day only: go out and fight them)
//   stored(item, count)      the chests hold `count` of an item or group (as last opened)

import vec3Pkg from 'vec3'
import { solve } from './solver.mjs'
import { dayPhase, inventoryCounts, EXPLODES, isDark } from './observe.mjs'
import { isInside, isDoorOpen, hasBed, bedSpot, dangerOutside } from './home.mjs'
import { chests, storedCounts } from './memory.mjs'
import { darkGround } from './lighting.mjs'
import { reachableThreats, SLEEP_FROM, SLEEP_UNTIL, HEALTH_CRITICAL, HUNGER_URGENT } from './primitives.mjs'

const { Vec3 } = vec3Pkg
const MAX_COUNT = 256
const MIN_EXPLORE = 8
const MIN_LIT = 8
const MAX_LIT = 32
const MAX_EXPLORE = 256
const EXPLORE_STEP = 20 // about how far one explore step gets
const DAY_TICKS = 24000
const MORNING = 0 // time of day the sun is up again (dawn ends at 24000 = 0)
const TICKS_PER_MINUTE = 1200

export const PREDICATES = ['have', 'built', 'placed', 'at_home', 'through_night', 'explored', 'cleared', 'stored', 'lit']
// Judged from the state of the world alone, so they can be the completion conditions of mid goals
// (the others depend on the moment or on where the goal was set)
export const CONDITION_PREDICATES = ['have', 'built', 'placed', 'stored', 'lit']

// Validates a goal spec and returns the goal state to keep; throws with the reason
// A goal that is valid but cannot be pursued until something exists (the home, the plan)
export class NotYetError extends Error {}

// Every goal remembers where it was set: its searches go away from there, never back and forth
// keep: what the chests keep for the mid goals ([{item, count}], their stored() conditions); the
// solver never takes it out, except food when starving (world.mjs)
export function makeGoal (spec, bot, state, knowledge) {
  const p = bot.entity.position
  return { ...validGoal(spec, bot, state, knowledge), exploreFrom: { x: p.x, y: p.y, z: p.z }, keep: validKeep(spec.keep ?? [], knowledge) }
}

function validKeep (keep, knowledge) {
  if (!Array.isArray(keep)) throw new Error('keep must be a list of {item, count}')
  const food = new Set(knowledge.resolve('food').members)
  return keep.map(({ item, count }) => {
    const n = Number(count)
    if (!Number.isInteger(n) || n < 1) throw new Error(`keep: count must be a positive integer, got ${count}`)
    const { members } = knowledge.resolve(String(item))
    return { item: String(item), count: n, members, food: members.every((m) => food.has(m)) }
  })
}

function validGoal (spec, bot, state, knowledge) {
  const { predicate } = spec
  const needHome = () => { if (!state.home) throw new NotYetError('there is no home yet (build the house first)') }
  switch (predicate) {
    case 'have': {
      const count = Number(spec.count)
      if (!Number.isInteger(count) || count < 1 || count > MAX_COUNT) throw new Error(`count must be 1-${MAX_COUNT}`)
      knowledge.resolve(String(spec.item))
      return { spec: { predicate, item: String(spec.item), count } }
    }
    case 'stored': {
      const count = Number(spec.count)
      if (!Number.isInteger(count) || count < 1 || count > MAX_COUNT) throw new Error(`count must be 1-${MAX_COUNT}`)
      knowledge.resolve(String(spec.item))
      needHome() // the chest goes in the house
      return { spec: { predicate, item: String(spec.item), count } }
    }
    case 'built':
      if (!state.plan) throw new NotYetError('there is no house plan')
      return { spec: { predicate } }
    case 'placed':
      if (spec.item !== 'bed' || spec.where !== 'home') throw new Error('only placed(bed, home) is supported')
      needHome()
      return { spec: { predicate, item: 'bed', where: 'home' } }
    case 'at_home':
      needHome()
      return { spec: { predicate } }
    case 'through_night':
      needHome()
      return { spec: { predicate }, sawNight: dayPhase(bot.time.timeOfDay) !== 'day' }
    case 'explored': {
      const distance = Number(spec.distance)
      if (!Number.isInteger(distance) || distance < MIN_EXPLORE || distance > MAX_EXPLORE) {
        throw new Error(`distance must be ${MIN_EXPLORE}-${MAX_EXPLORE}`)
      }
      const p = bot.entity.position
      return { spec: { predicate, distance }, start: { x: p.x, y: p.y, z: p.z } }
    }
    case 'lit': {
      needHome()
      const distance = Number(spec.distance)
      if (!Number.isInteger(distance) || distance < MIN_LIT || distance > MAX_LIT) throw new Error(`distance (the radius) must be ${MIN_LIT}-${MAX_LIT}`)
      return { spec: { predicate, distance } }
    }
    case 'cleared': {
      needHome()
      // In the dark more keep spawning: the night is waited out inside (through_night)
      const phase = dayPhase(bot.time.timeOfDay)
      if (phase !== 'day') throw new Error(`it is ${phase}: hostile mobs keep spawning in the dark; stay inside until morning`)
      return { spec: { predicate } }
    }
    default:
      throw new Error(`unknown predicate ${predicate}; use one of ${PREDICATES.join(', ')}`)
  }
}

// { spec, met, remaining, lines, blocked, leaves } for the current goal
export function evaluate (bot, state, knowledge, world) {
  const goal = state.goal
  const out = { spec: goal.spec, met: false, remaining: 0, lines: [], blocked: [], leaves: [] }
  const addSolved = (needs, w = world) => {
    const r = solve(knowledge, w, needs)
    out.lines.push(...r.lines)
    out.blocked.push(...r.blocked)
    // Searching for what is not nearby: away from where the goal was set (it walked back and
    // forth around the start before, never finding food 20m further)
    out.leaves.push(...r.leaves.map((l) => l.kind === 'explore' && !l.away && goal.exploreFrom ? { ...l, away: goal.exploreFrom } : l))
    out.remaining += r.remaining
    return r
  }
  const phase = dayPhase(bot.time.timeOfDay)
  const inside = isInside(bot, state.home)
  switch (goal.spec.predicate) {
    case 'have': {
      const r = addSolved([{ spec: goal.spec.item, count: goal.spec.count }])
      out.met = r.met
      break
    }
    case 'built': {
      const plan = state.plan
      const status = plan.status(bot)
      out.met = status.complete
      if (out.met) break
      // Logs for log blocks first, so none of them become planks
      const need = plan.materialsNeeded(bot)
      addSolved(['log', 'door', 'planks'].filter((k) => need[k]).map((k) => ({ spec: k, count: need[k] })))
      out.lines.unshift(`house blocks placed ${status.placed}/${status.total}`)
      out.remaining += status.total - status.placed
      const next = plan.pending(bot)[0]
      const held = next && Object.keys(inventoryCounts(bot)).some((n) => knowledge.isMember(next.block, n))
      if (!plan.origin && !plan.canSearchSite(bot)) {
        out.blocked.push('no flat site for the house near here')
        out.leaves.push({ kind: 'explore', item: 'a flat site', sources: [] })
      } else if (held) {
        out.leaves.push({ kind: 'place_plan', block: next })
      }
      break
    }
    case 'placed': {
      out.met = hasBed(bot, state.home)
      out.lines.push(`a bed in the house: ${out.met ? 'yes' : 'no'}`)
      if (out.met) break
      const bed = Object.keys(inventoryCounts(bot)).some((n) => n.endsWith('_bed'))
      if (!bed) addSolved([{ spec: 'bed', count: 1 }])
      else if (bedSpot(bot, state.home)) out.leaves.push({ kind: 'place_bed' })
      else out.blocked.push('no free spot for the bed in the house')
      out.remaining += 1
      break
    }
    case 'at_home':
      out.met = inside && !isDoorOpen(bot, state.home) && !state.home.breach.length
      if (!out.met) {
        out.leaves.push({ kind: 'go_home' })
        out.remaining = 1
      }
      out.lines.push(`inside the house with the door closed: ${out.met ? 'yes' : 'no'}`)
      break
    case 'through_night': {
      if (phase !== 'day') goal.sawNight = true
      out.met = goal.sawNight && phase === 'day'
      out.lines.push(`the night has passed: ${out.met ? 'yes' : `no (${phase})`}`)
      if (out.met) break
      // Minutes until morning: waiting shows as progress, so it is not taken for a stall
      const tod = bot.time.timeOfDay
      out.remaining = Math.max(1, Math.ceil(((MORNING - tod + DAY_TICKS) % DAY_TICKS) / TICKS_PER_MINUTE))
      if (!inside) out.leaves.push({ kind: 'go_home' })
      else if (hasBed(bot, state.home) && tod >= SLEEP_FROM && tod <= SLEEP_UNTIL && !bot.isSleeping) out.leaves.push({ kind: 'sleep' })
      break
    }
    case 'explored': {
      const start = new Vec3(goal.start.x, goal.start.y, goal.start.z)
      const d = bot.entity.position.xzDistanceTo(start)
      out.met = d >= goal.spec.distance
      out.lines.push(`explored ${Math.floor(d)}/${goal.spec.distance}m`)
      if (!out.met) {
        out.remaining = Math.ceil((goal.spec.distance - d) / EXPLORE_STEP)
        out.leaves.push({ kind: 'explore', item: 'new places', sources: [], away: start })
      }
      break
    }
    case 'stored': {
      const { members } = knowledge.resolve(goal.spec.item)
      const stored = storedCounts(state.memory ?? {})
      const n = goal.spec.count
      const inChests = members.reduce((s, m) => s + (stored[m] ?? 0), 0)
      out.met = inChests >= n
      out.lines.push(`${goal.spec.item} in the chests (${Math.min(inChests, n)}/${n})`)
      if (out.met) break
      const want = n - inChests
      out.remaining += want
      // What the chests hold is never taken out to be put back
      // The items are gathered with or without a chest, so the remaining work only goes down as
      // the chest is placed and filled
      const gathering = { ...world, stored: {} }
      if (!chests(state.memory ?? {}).length) {
        if (addSolved([{ spec: 'chest', count: 1 }], gathering).met) out.leaves.push({ kind: 'place_chest' })
        out.remaining += 1
      } else {
        const inv = inventoryCounts(bot)
        const held = members.filter((m) => inv[m] > 0).map((m) => ({ item: m, count: Math.min(inv[m], want) }))
        if (held.length) out.leaves.push({ kind: 'deposit', items: held })
      }
      addSolved([{ spec: goal.spec.item, count: want }], gathering)
      break
    }
    case 'lit': {
      const { dark, unloaded } = darkGround(bot, state, goal.spec.distance)
      out.met = dark.length === 0 && unloaded === 0
      out.remaining = dark.length + unloaded
      if (unloaded) {
        out.lines.push(`the ground within ${goal.spec.distance} of the home: out of view`)
        out.blocked.push('the home is too far to see its grounds; go back to it')
        out.leaves.push({ kind: 'go_home' })
        break
      }
      out.lines.push(`dark ground within ${goal.spec.distance} of the home: ${dark.length ? `${dark.length} spots` : 'none'}`)
      if (out.met) break
      const torches = inventoryCounts(bot).torch ?? 0
      if (torches) out.leaves.push({ kind: 'light', pos: dark[0] })
      else addSolved([{ spec: 'torch', count: 4 }])
      break
    }
    case 'cleared': {
      const danger = dangerOutside(bot, state.home)
      out.met = danger.length === 0
      out.remaining = danger.length
      out.lines.push(`hostiles near the door: ${danger.length ? danger.map(({ e }) => e.name).join(', ') : 'none'}`)
      if (out.met) break
      if (phase !== 'day') out.blocked.push(`it is ${phase}: stay inside until morning`)
      // A creeper blows up the doorway when fought in melee there (it happened in M1)
      else if (danger.some(({ e }) => EXPLODES.has(e.name))) out.blocked.push('a creeper is near the door: it explodes when fought in melee; wait for it to leave')
      else out.leaves.push({ kind: 'clear', mobs: danger.map(({ e }) => e) })
      break
    }
  }
  return out
}

// Judges conditions without setting a goal: [{ spec, met, lines }]; throws for an invalid one.
// One that cannot be judged yet (a bed in a home not built) is not met.
export function checkConditions (specs, bot, state, knowledge, world) {
  return specs.map((spec) => {
    if (!CONDITION_PREDICATES.includes(spec.predicate)) {
      throw new Error(`${spec.predicate} cannot be a condition; use one of ${CONDITION_PREDICATES.join(', ')}`)
    }
    let goal
    try {
      goal = makeGoal(spec, bot, state, knowledge)
    } catch (e) {
      if (e instanceof NotYetError) return { spec, met: false, lines: [e.message] }
      throw e
    }
    const r = evaluate(bot, { ...state, goal }, knowledge, world)
    return { spec: goal.spec, met: r.met, lines: r.lines }
  })
}

// What the body needs, with numbers and severity only: no action names (the selector judged
// better with these stated, and worse when told what to do)
export function needs (bot, state) {
  const out = []
  if (bot.health <= HEALTH_CRITICAL) out.push(`health critical (${Math.round(bot.health)}/20)`)
  if (bot.food <= HUNGER_URGENT) out.push(`hunger urgent (${bot.food}/20): healing stops below 18 and sprinting below 7`)
  const phase = dayPhase(bot.time.timeOfDay)
  if (phase === 'dusk') out.push('night is coming: hostile mobs spawn outside in the dark')
  if (phase === 'night' && !isInside(bot, state.home)) out.push('it is night and you are outside')
  if (isDark(bot)) out.push('dark here (covered, no light near): hostile mobs can spawn around you')
  for (const { e, dist } of reachableThreats(bot, state)) out.push(`hostile ${e.name} ${Math.round(dist)}m away`)
  for (const { e } of dangerOutside(bot, state.home)) out.push(`${e.name} waiting outside the door`)
  for (const b of state.home?.breach ?? []) out.push(`the house wall has a hole at ${b.x},${b.y},${b.z}`)
  return out.length ? out : ['none']
}
