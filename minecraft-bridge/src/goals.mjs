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

import vec3Pkg from 'vec3'
import { solve } from './solver.mjs'
import { dayPhase, inventoryCounts } from './observe.mjs'
import { isInside, isDoorOpen, hasBed, bedSpot, dangerOutside } from './home.mjs'
import { reachableThreats, SLEEP_FROM, SLEEP_UNTIL } from './primitives.mjs'

const { Vec3 } = vec3Pkg
const MAX_COUNT = 256
const MIN_EXPLORE = 8
const MAX_EXPLORE = 256
const EXPLORE_STEP = 20 // about how far one explore step gets
const DAY_TICKS = 24000
const MORNING = 0 // time of day the sun is up again (dawn ends at 24000 = 0)
const TICKS_PER_MINUTE = 1200

export const PREDICATES = ['have', 'built', 'placed', 'at_home', 'through_night', 'explored']

// Validates a goal spec and returns the goal state to keep; throws with the reason
export function makeGoal (spec, bot, state, knowledge) {
  const { predicate } = spec
  const needHome = () => { if (!state.home) throw new Error('there is no home yet (build the house first)') }
  switch (predicate) {
    case 'have': {
      const count = Number(spec.count)
      if (!Number.isInteger(count) || count < 1 || count > MAX_COUNT) throw new Error(`count must be 1-${MAX_COUNT}`)
      knowledge.resolve(String(spec.item))
      return { spec: { predicate, item: String(spec.item), count } }
    }
    case 'built':
      if (!state.plan) throw new Error('there is no house plan')
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
    default:
      throw new Error(`unknown predicate ${predicate}; use one of ${PREDICATES.join(', ')}`)
  }
}

// { spec, met, remaining, lines, blocked, leaves } for the current goal
export function evaluate (bot, state, knowledge, world) {
  const goal = state.goal
  const out = { spec: goal.spec, met: false, remaining: 0, lines: [], blocked: [], leaves: [] }
  const addSolved = (needs) => {
    const r = solve(knowledge, world, needs)
    out.lines.push(...r.lines)
    out.blocked.push(...r.blocked)
    out.leaves.push(...r.leaves)
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
      out.met = inside && !isDoorOpen(bot, state.home)
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
  }
  return out
}

// What the body needs, with numbers and severity only: no action names (the selector judged
// better with these stated, and worse when told what to do)
export function needs (bot, state) {
  const out = []
  if (bot.health <= 8) out.push(`health critical (${Math.round(bot.health)}/20)`)
  if (bot.food <= 6) out.push(`hunger urgent (${bot.food}/20): healing stops below 18 and sprinting below 7`)
  const phase = dayPhase(bot.time.timeOfDay)
  if (phase === 'dusk') out.push('night is coming: hostile mobs spawn outside in the dark')
  if (phase === 'night' && !isInside(bot, state.home)) out.push('it is night and you are outside')
  for (const { e, dist } of reachableThreats(bot, state)) out.push(`hostile ${e.name} ${Math.round(dist)}m away`)
  for (const { e } of dangerOutside(bot, state.home)) out.push(`${e.name} waiting outside the door`)
  return out.length ? out : ['none']
}
