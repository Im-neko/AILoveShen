// かまどで肉を焼く: 焼いた牛肉は満腹度 8（隠し満腹度 12.8）、生は 3（1.8）回復する。
//
// 家のかまど（家がまだないときだけ地上の近くのかまど）があり、燃料を持っているときだけ焼く。かまどがなければ（まだかまど用の石がない）
// 生肉はこれまでどおり食料のままなので、食料の目標がこれまでより難しくなることはない。

import { smeltingProduct, FUELS } from './knowledge.mjs'
import { inventoryCounts } from './observe.mjs'
import { knownFurnace } from './primitives.mjs'

const COOK_AT_ONCE = 8 // 石炭1個ぶん。残りはかまどの中か次の機会を待つ

export const FAR_COOK_MIN = 3 // 見えない所のかまどまで焼きに行くのは、これだけ生肉があるとき

// 持っている生肉についての { furnace, input, product, count, fuel, fuelCount }。なければ null。
// 見えるかまどを優先し、なければ最後に見たかまど（家のかまどなど）へ焼きに行く（手持ちの肉は
// なるべく焼く。state を渡したときだけ）
export function cooking (bot, knowledge, state = null) {
  const inv = inventoryCounts(bot)
  const input = Object.keys(inv).find((name) => inv[name] > 0 && isRawMeat(knowledge, name))
  if (!input) return null
  // 家のかまどだけ（primitives.mjs の findFurnace）。見えれば焼く、遠ければ FAR_COOK_MIN 個以上あるときに歩いて焼きに行く
  const known = knownFurnace(bot, state)
  const furnace = known && (known.near || (state && inv[input] >= FAR_COOK_MIN)) ? { position: known.position, ...(known.near ? {} : { far: true }) } : null
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

// 手持ちの食べ物の個数（腐った肉などは数えない: have(food) と同じ数え方）。Python が、空腹になる前に
// 集める（食料のストック）かを決めるのに使う
export function foodCount (bot, knowledge) {
  return foodIn(inventoryCounts(bot), knowledge)
}

// 食べ物の満腹度の合計（個数 × 回復量。腐った肉などは数えない）。生肉は、かまどが分かっているなら焼いた
// 値で数え、なければ生の値で数える（かまどがないのに焼けるつもりで見積もらない）。ストックの量は個数でなく
// これで決める（じゃがいも 8 個は満腹度 8、焼いた肉 8 個は 64）
export function foodPointsIn (counts, knowledge, furnaceKnown = false) {
  const foods = knowledge.md.foodsByName
  let sum = 0
  for (const name of knowledge.resolve('food').members) {
    const n = counts[name] ?? 0
    if (!n) continue
    const cooked = furnaceKnown && isRawMeat(knowledge, name) ? foods[smeltingProduct(name)] : null
    sum += n * (cooked ?? foods[name]).foodPoints
  }
  return sum
}

// 名前ごとの個数（持ち物、チェストの中身の合計）のうち、食べ物の個数
export function foodIn (counts, knowledge) {
  return knowledge.resolve('food').members.reduce((sum, name) => sum + (counts[name] ?? 0), 0)
}

// かまどで焼くとよりよい食料になる肉
export function isRawMeat (knowledge, name) {
  const product = smeltingProduct(name)
  const foods = knowledge.md.foodsByName
  return !!(product && foods[name] && foods[product] && foods[product].foodPoints > foods[name].foodPoints)
}
