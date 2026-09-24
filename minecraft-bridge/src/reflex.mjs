// Reflexes: a hostile mob close to the bot is handled at once, without waiting for a decision,
// and the bot keeps its head above water.
//
// It runs on every few physics ticks, both while an action runs and between steps (the bot was
// killed standing still between steps once). A running action is preempted and awaited until it
// has stopped, then the bot fights (with a weapon, one attacker, not a creeper) or flees. The
// result goes into the action history so the decision makers see what happened.

import { round } from './observe.mjs'
import { reachableThreats, bestWeapon, fight, flee } from './actions.mjs'

const REFLEX_RADIUS = 5
const CREEPER_RADIUS = 7 // flee before it starts to swell
const CHECK_EVERY_TICKS = 4

function dangers (bot, state) {
  return reachableThreats(bot, state).filter(({ e, dist }) => dist <= (e.name === 'creeper' ? CREEPER_RADIUS : REFLEX_RADIUS))
}

// hooks.preempt(reason): stops the running action (if any) and resolves once it has stopped;
// resolves false when the running action must not be preempted.
// hooks.record(entry): appends to the action history.
export function startReflex (bot, state, hooks) {
  let ticks = 0
  let floating = false
  bot.on('physicsTick', () => {
    // Mineflayer does not swim on its own: left idle in water (between steps, or with the agent
    // stopped) the bot sank and drowned. Hold jump to stay at the surface unless pathing.
    const float = !!bot.entity?.isInWater && !bot.pathfinder.isMoving()
    if (float !== floating) {
      bot.setControlState('jump', float)
      floating = float
    }
    if (++ticks % CHECK_EVERY_TICKS || state.reflex || !bot.entity || bot.health <= 0) return
    const near = dangers(bot, state)
    if (near.length) react(near).catch((e) => console.log(`[reflex] error: ${e.message}`))
  })

  async function react (near) {
    state.reflex = true
    const t0 = Date.now()
    const { e, dist } = near[0]
    try {
      if (!(await hooks.preempt(`reflex: ${e.name} ${round(dist)}m away`))) return
      const weapon = bestWeapon(bot)
      const fightIt = weapon && e.name !== 'creeper' && near.length === 1
      const signal = new AbortController().signal
      let ok = true
      let result
      try {
        if (fightIt) {
          await bot.equip(weapon, 'hand')
          result = await fight(bot, e, signal)
        } else {
          result = await flee(bot, e, signal)
        }
      } catch (err) {
        ok = false
        result = `failed: ${err.message}`
      } finally {
        bot.clearControlStates()
      }
      const id = fightIt ? 'reflex_fight' : 'reflex_flee'
      console.log(`[reflex] ${id} ${e.name} (${round(dist)}m, ${near.length} near): ${result}`)
      hooks.record({ action: id, ok, result: `${e.name}: ${result}`, seconds: round((Date.now() - t0) / 1000) })
    } finally {
      state.reflex = false
    }
  }
}
