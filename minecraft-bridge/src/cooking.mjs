// Cooking meat in a furnace: cooked beef restores 8 hunger (12.8 saturation), raw 3 (1.8).
//
// Only where a furnace is at hand and fuel is held: without one (no stone for a furnace yet) raw
// meat stays food as before, so no food goal becomes harder than it was.

import { smeltingProduct, FUELS } from './knowledge.mjs'
import { inventoryCounts } from './observe.mjs'
import { findFurnace } from './primitives.mjs'

const COOK_AT_ONCE = 8 // one coal's worth; the rest waits in the furnace or for the next time

// { furnace, input, product, count, fuel, fuelCount } for the raw meat held, or null
export function cooking (bot, knowledge) {
  const inv = inventoryCounts(bot)
  const input = Object.keys(inv).find((name) => inv[name] > 0 && isRawMeat(knowledge, name))
  if (!input) return null
  const furnace = findFurnace(bot)
  if (!furnace) return null
  const count = Math.min(inv[input], COOK_AT_ONCE)
  for (const { spec, per } of FUELS) {
    const fuel = knowledge.resolve(spec).members.find((m) => inv[m] > 0 && m !== input)
    if (!fuel) continue
    const fuelCount = Math.min(inv[fuel], Math.ceil(count / per))
    return { furnace, input, product: smeltingProduct(input), count: Math.min(count, Math.floor(fuelCount * per)), fuel, fuelCount }
  }
  return null
}

// Meat the furnace cooks into better food
export function isRawMeat (knowledge, name) {
  const product = smeltingProduct(name)
  const foods = knowledge.md.foodsByName
  return !!(product && foods[name] && foods[product] && foods[product].foodPoints > foods[name].foodPoints)
}
