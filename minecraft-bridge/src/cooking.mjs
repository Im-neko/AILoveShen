// かまどで肉を焼く: 焼いた牛肉は満腹度 8（隠し満腹度 12.8）、生は 3（1.8）回復する。
//
// 近くにかまどがあり、燃料を持っているときだけ焼く。かまどがなければ（まだかまど用の石がない）
// 生肉はこれまでどおり食料のままなので、食料の目標がこれまでより難しくなることはない。

import { smeltingProduct, FUELS } from './knowledge.mjs'
import { inventoryCounts } from './observe.mjs'
import { findFurnace } from './primitives.mjs'

const COOK_AT_ONCE = 8 // 石炭1個ぶん。残りはかまどの中か次の機会を待つ

// 持っている生肉についての { furnace, input, product, count, fuel, fuelCount }。なければ null
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

// かまどで焼くとよりよい食料になる肉
export function isRawMeat (knowledge, name) {
  const product = smeltingProduct(name)
  const foods = knowledge.md.foodsByName
  return !!(product && foods[name] && foods[product] && foods[product].foodPoints > foods[name].foodPoints)
}
