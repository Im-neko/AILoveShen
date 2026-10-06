// 回復の手段がないとき（体力が危険で、自然回復の満腹度に届かず、食べ物も持っていない）。この状態で
// 外に出ると、死ぬと持ち物が落ちて消えるだけで、家にいても回復しない（2026-10-01: 体力 1.5・満腹度 15・
// 食料 0 のまま、昼も夜も家と外を行き来した）。持ち物を先にチェストへ預けておけば、死ぬコストは
// ほとんどない（docs/design/76_nothing_to_lose.md）
import { FOOD_EXCLUDED } from './knowledge.mjs'
import { HEALTH_CRITICAL } from './primitives.mjs'
import { KEEP_WHEN_FIGHTING } from './wear.mjs'

// 自然回復には満腹度がほぼ満タンである必要がある
export const REGEN_FOOD = 18

// 体力がこれ以下に減っていて満腹度が 18 に届かないなら、回復のために食べて満腹度を 18 以上にする
// （自然回復は満腹度 18 以上のときだけ。ここまで減っていなければ、少しの無駄な食べ方はしない）
export const HEAL_BELOW = 16

export function needsFoodToHeal (bot) {
  return bot.health <= HEAL_BELOW && bot.food < REGEN_FOOD
}

// 食べられる物（腐った肉などの害のある物は数えない）を持っているか
export function carriesFood (bot) {
  const foods = bot.registry?.foodsByName ?? {}
  return (bot.inventory?.items?.() ?? []).some((i) => foods[i.name] && !FOOD_EXCLUDED.has(i.name))
}

export function cannotHeal (bot) {
  return bot.health <= HEALTH_CRITICAL && bot.food < REGEN_FOOD && !carriesFood(bot)
}

// 持っていて、死ぬと失う物のうち、武器・防具・松明（KEEP_WHEN_FIGHTING）以外の数
export function valuablesCarried (bot) {
  return (bot.inventory?.items?.() ?? []).filter((i) => !KEEP_WHEN_FIGHTING.test(i.name)).reduce((sum, i) => sum + i.count, 0)
}
