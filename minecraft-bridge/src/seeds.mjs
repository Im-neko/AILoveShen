// 種の余り（docs/design/117）: 畑の運用で種が余る（小麦の種が家のチェストに 300）。残すのは、その作物の設計した畑の数 ×
// 1 スタック（持ち物とチェストの合計）。超えた分は捨てる（2026-10-08, user: 「種は畑の数*1スタック残してあとは捨てて良いです」）。
// 畑がまだなくても 1 畑分は残す（最初の畑に要る）。種そのもの（_seeds）だけ。ニンジンとジャガイモは作物を兼ねるので数えない。
import { CROPS } from './farming.mjs'
import { designedFarms } from './facilities.mjs'
import { storedCounts } from './memory.mjs'
import { inventoryCounts } from './observe.mjs'

const SEED_CROPS = Object.entries(CROPS).filter(([, c]) => c.seed.endsWith('_seeds')).map(([crop, c]) => ({ crop, seed: c.seed }))

export const isSurplusSeed = (item) => SEED_CROPS.some((s) => s.seed === item)

// 残す数: 畑の数 × 1 スタック（最低 1 畑分）
export function seedLimit (bot, state, item) {
  const crop = SEED_CROPS.find((s) => s.seed === item)?.crop
  const stack = bot.registry?.itemsByName?.[item]?.stackSize ?? 64
  return Math.max(1, designedFarms(state, crop).length) * stack
}

// 種ごとの今: [{ item, held, stored, limit, surplus }]（余りのあるものだけ）。keep は目標が使う物の名前（目標が種そのものを
// 集めているときは、捨てない）
export function seedSurplus (bot, state, keep = new Set()) {
  const held = inventoryCounts(bot)
  const stored = state.memory && state.home ? storedCounts(state.memory, state.home) : {}
  return SEED_CROPS.map(({ seed }) => {
    const limit = seedLimit(bot, state, seed)
    const h = held[seed] ?? 0
    const s = stored[seed] ?? 0
    return { item: seed, held: h, stored: s, limit, surplus: keep.has(seed) ? 0 : Math.max(0, h + s - limit) }
  }).filter((x) => x.surplus > 0)
}

// 持ち物から捨てる分: [{ item, count }]
export function seedsToToss (bot, state, keep) {
  return seedSurplus(bot, state, keep).map((x) => ({ item: x.item, count: Math.min(x.held, x.surplus) })).filter((x) => x.count > 0)
}

// チェストから出して捨てる分: [{ item, count }]（持ち物の余りを引いた残り）
export function seedsToTake (bot, state, keep) {
  return seedSurplus(bot, state, keep).map((x) => ({ item: x.item, count: Math.min(x.stored, x.surplus - Math.min(x.held, x.surplus)) })).filter((x) => x.count > 0)
}
