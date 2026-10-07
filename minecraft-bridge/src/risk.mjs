// リスク評価（docs/design/94）。外に出る・戦う・遠くへ行くときの危なさを、1 か所で数字にする。
//
// 今まで、外に出てよいかは「夜か、ドアの 8 m 以内に敵対モブがいるか」の二択で、持ち物も敵の強さも
// 見なかった（素手で土 1 個の配信者が、昼にピリジャー 1 体で 1 時間以上家に閉じこもった: 2026-10-07）。
// ここは禁止ではなく、危なさと、失うもの（死んだら落とす持ち物）を数字と理由にして、選ぶ側（LLM）に見せる。
// 家の中にいても、外に出たときの危なさを出す（壁の外の敵も数える）。
//
//   danger = 体力 + 満腹度 + 時間帯 + 近くの敵（装備で割り引く）+ 家から遠い
//   risk   = danger × 失うものの重み（何も持たなければ 0.4。死んでも戻る時間だけなので 0 にはしない）
//
// 数字は仮: 配信の死亡の記録で調整する（docs/design/94 §5）。

import { dayPhase, isHostile, nearbyEntities, isUnderground } from './observe.mjs'

export const LEVELS = ['low', 'medium', 'high', 'extreme']
// これ以上で、外に出る手をコードが止める（家の中にいるとき）。止めるのは、持ち物を失うどころか死ぬ見込みが
// 高いときだけ。none にすると止めない
export const SHELTER_LEVEL = 'extreme'
const LEVEL_AT = { medium: 2, high: 4, extreme: 7 }

// 敵 1 体の脅威。遠距離・爆発・怪力は重い
const THREAT = {
  creeper: 3,
  ravager: 4,
  vindicator: 2.5,
  evoker: 3,
  witch: 2,
  skeleton: 2,
  stray: 2,
  pillager: 2,
  blaze: 3,
  ghast: 3,
  phantom: 1.5,
  zombie: 1,
  husk: 1,
  drowned: 1,
  zombie_villager: 1,
  spider: 1,
  cave_spider: 1.2,
  slime: 0.7
}
const DEFAULT_THREAT = 1.2
const NEAR_M = 6
const MID_M = 10
const FAR_M = 16
const FAR_FROM_HOME_M = 40

// 武器の段階（剣）と、斧は 0.75 倍。素手は 0
const TIER = { wooden: 1, golden: 1, stone: 2, iron: 3, diamond: 4, netherite: 5 }
const ARMOR = { leather: 0.3, chainmail: 0.5, golden: 0.4, iron: 0.6, turtle: 0.3, diamond: 0.9, netherite: 1 }

// 持ち物の値打ち（死んで落とす・失うもの）。道具と防具が大半。素材は少し、食料と松明はほぼ 0
const TOOL_VALUE = { wooden: 1, golden: 1.5, stone: 2, iron: 5, diamond: 15, netherite: 25 }
const ARMOR_VALUE = { leather: 1, chainmail: 3, golden: 2, iron: 4, turtle: 3, diamond: 12, netherite: 18 }
const ITEM_VALUE = { iron_ingot: 1, raw_iron: 0.5, gold_ingot: 1, diamond: 5, emerald: 1, bucket: 3, water_bucket: 3, shield: 2, bow: 2, crossbow: 3, bed: 1, white_bed: 1 }
const NOTHING_BELOW = 2
const LITTLE_BELOW = 8
const WEIGHT = { nothing: 0.4, little: 0.7, valuable: 1 }

// 失うもの（死んで落とす、チェストに預ければ守れる物）の値打ち。剣・斧と着ている防具は戦うために
// 持っていく物（預けない。strength で数える）なので入れない
export function inventoryValue (bot) {
  let value = 0
  const worn = new Set((bot.inventory?.slots ?? []).filter((it) => it && it.slot >= 5 && it.slot <= 8).map((it) => it.name))
  for (const it of bot.inventory?.items?.() ?? []) {
    const tool = it.name.match(/^(\w+)_(pickaxe|shovel|hoe)$/)
    if (tool) { value += TOOL_VALUE[tool[1]] ?? 1; continue }
    if (/_(sword|axe)$/.test(it.name) || worn.has(it.name)) continue
    const armor = it.name.match(/^(\w+)_(helmet|chestplate|leggings|boots)$/)
    if (armor) { value += (ARMOR_VALUE[armor[1]] ?? 1) * (armor[2] === 'chestplate' || armor[2] === 'leggings' ? 1.5 : 1); continue }
    const each = ITEM_VALUE[it.name]
    if (each) value += Math.min(each * it.count, 20)
  }
  return value
}

// 武器と防具から、敵をどれだけ割り引けるか（0 以上）
export function strength (bot) {
  let weapon = 0
  for (const it of bot.inventory?.items?.() ?? []) {
    const m = it.name.match(/^(\w+)_(sword|axe)$/)
    if (m) weapon = Math.max(weapon, (TIER[m[1]] ?? 1) * (m[2] === 'sword' ? 1 : 0.75))
  }
  let armor = 0
  const slots = bot.inventory?.slots ?? []
  for (const it of slots) {
    if (!it) continue
    const a = it.name.match(/^(\w+)_(helmet|chestplate|leggings|boots)$/)
    // 持ち物の欄にあるだけの防具（着ていない）は数えない: 防具の欄は 5〜8
    if (a && it.slot >= 5 && it.slot <= 8) armor += ARMOR[a[1]] ?? 0.3
  }
  const shield = slots.some((it) => it && it.name === 'shield' && it.slot === 45) ? 0.3 : 0
  return { weapon, armor: armor + shield, total: weapon * 0.7 + armor + shield }
}

const healthPenalty = (h) => (h >= 18 ? 0 : h >= 14 ? 1 : h >= 10 ? 2 : h >= 6 ? 4 : 6)
const foodPenalty = (f) => (f <= 0 ? 2 : f <= 6 ? 1 : 0)
const timePenalty = (phase) => (phase === 'night' ? 2 : phase === 'dusk' ? 1 : 0)

function exposure (bot) {
  const out = []
  for (const { e, dist } of nearbyEntities(bot)) {
    if (dist > FAR_M) break
    if (!isHostile(bot, e)) continue
    const weight = dist <= NEAR_M ? 1 : dist <= MID_M ? 0.7 : 0.4
    out.push({ name: e.name, distance: Math.round(dist * 10) / 10, threat: (THREAT[e.name] ?? DEFAULT_THREAT) * weight })
  }
  return out
}

// 外に出る・戦うときの危なさ: { level, score, danger, stakes, value, factors, context, advice }
export function assessRisk (bot, state) {
  const phase = dayPhase(bot.time.timeOfDay)
  const mobs = exposure(bot)
  const power = strength(bot)
  const raw = mobs.reduce((s, m) => s + m.threat, 0)
  // 武器と防具で敵の脅威を最大 6 割まで割り引く
  const discount = Math.min(0.6, power.total * 0.12)
  const threat = raw * (1 - discount)
  const door = state?.home?.door
  const far = door && !isUnderground(bot) && bot.entity.position.distanceTo(door) > FAR_FROM_HOME_M ? 1 : 0
  const parts = {
    health: healthPenalty(bot.health),
    food: foodPenalty(bot.food),
    time: timePenalty(phase),
    enemies: threat,
    far
  }
  const danger = Object.values(parts).reduce((s, n) => s + n, 0)
  const value = inventoryValue(bot)
  const stakes = value < NOTHING_BELOW ? 'nothing' : value < LITTLE_BELOW ? 'little' : 'valuable'
  const score = Math.round(danger * WEIGHT[stakes] * 10) / 10
  const level = score >= LEVEL_AT.extreme ? 'extreme' : score >= LEVEL_AT.high ? 'high' : score >= LEVEL_AT.medium ? 'medium' : 'low'

  const factors = []
  if (parts.health) factors.push(`health ${Math.round(bot.health)}/20`)
  if (parts.food) factors.push(`food ${bot.food}/20`)
  if (parts.time) factors.push(phase)
  if (mobs.length) factors.push(`hostiles: ${mobs.slice(0, 3).map((m) => `${m.name} ${m.distance}m`).join(', ')}${mobs.length > 3 ? ` +${mobs.length - 3}` : ''}`)
  if (far) factors.push(`${FAR_FROM_HOME_M}m+ from home`)
  // 数字を動かす理由（factors）と、背景（context: 装備と失うもの）
  const context = [
    power.total > 0 ? `${power.weapon ? 'weapon' : 'no weapon'}${power.armor ? ' + armor' : ''}` : 'bare hands',
    stakes === 'nothing' ? 'almost nothing to lose' : stakes === 'little' ? 'little to lose' : 'valuables at stake'
  ]

  const advice = []
  if (stakes === 'valuable') advice.push('stash valuables in the chest')
  if (parts.health >= 2 || parts.food) advice.push('eat or rest')
  if (!power.weapon && mobs.length && stakes !== 'nothing') advice.push('equip a weapon')
  if (mobs.some((m) => m.name === 'creeper')) advice.push('keep the creeper away from home')

  return { level, score, danger: Math.round(danger * 10) / 10, stakes, value: Math.round(value), factors, context, advice }
}

export const atLeast = (risk, level) => LEVELS.indexOf(risk.level) >= LEVELS.indexOf(level)

// 1 行の説明（拒否の理由や、進められない理由に添える）。LLM に渡る道具の結果は 300 字で切られる
// （game_prompt_template_builder の TOOL_RESULT_CHARS）ので、先にすること（advice）を前に、短く
export function describeRisk (risk) {
  const first = risk.advice.length ? `do first: ${risk.advice.slice(0, 2).join(', ')}; ` : ''
  return `risk ${risk.level}, ${first}because ${risk.factors.join(', ')} (${risk.context.join(', ')})`
}
