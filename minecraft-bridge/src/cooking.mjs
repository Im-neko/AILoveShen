// かまどで肉を焼く: 焼いた牛肉は満腹度 8（隠し満腹度 12.8）、生は 3（1.8）回復する。
//
// 近くにかまどがあり、燃料を持っているときだけ焼く。かまどがなければ（まだかまど用の石がない）
// 生肉はこれまでどおり食料のままなので、食料の目標がこれまでより難しくなることはない。

import { smeltingProduct, FUELS } from './knowledge.mjs'
import { inventoryCounts } from './observe.mjs'
import { findFurnace } from './primitives.mjs'

const COOK_AT_ONCE = 8 // 石炭1個ぶん。残りはかまどの中か次の機会を待つ

const FAR_COOK_MIN = 3 // 見えない所のかまどまで焼きに行くのは、これだけ生肉があるとき

// 持っている生肉についての { furnace, input, product, count, fuel, fuelCount }。なければ null。
// 見えるかまどを優先し、なければ最後に見たかまど（家のかまどなど）へ焼きに行く（手持ちの肉は
// なるべく焼く。state を渡したときだけ）
export function cooking (bot, knowledge, state = null) {
  const inv = inventoryCounts(bot)
  const input = Object.keys(inv).find((name) => inv[name] > 0 && isRawMeat(knowledge, name))
  if (!input) return null
  let furnace = findFurnace(bot)
  if (furnace && state) state.lastFurnace = { x: furnace.position.x, y: furnace.position.y, z: furnace.position.z }
  if (!furnace && state?.lastFurnace && inv[input] >= FAR_COOK_MIN) {
    const p = state.lastFurnace
    furnace = { position: bot.entity.position.clone().set(p.x, p.y, p.z), far: true }
  }
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
