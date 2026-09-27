// 道具が壊れたこと（使い切って持ち物から消えたこと）を見張る。壊れたらすぐ記録し、観測に出し、
// 同じ種類の道具がなければ作り直す候補を出す（ツルハシが壊れても、しばらく持っているつもりで
// 素手で石を掘り続けていた）

const TOOL = /_(pickaxe|axe|shovel|hoe|sword)$|^shears$/
const NEAR_BREAK = 3 // 残りの耐久がこれ以下で消えたら、壊れたとみなす（チェストへ移したのではない）
const KEEP = 5
export const RECENT_MS = 10 * 60 * 1000

export const toolKind = (name) => name.match(/_(pickaxe|axe|shovel|hoe|sword)$/)?.[1] ?? (name === 'shears' ? 'shears' : null)

export function isBreak (oldItem, newItem) {
  if (!oldItem || !TOOL.test(oldItem.name) || newItem?.name === oldItem.name) return false
  const max = oldItem.maxDurability
  return !!max && (oldItem.durabilityUsed ?? 0) >= max - NEAR_BREAK
}

export function watchWear (bot, state) {
  bot.inventory.on('updateSlot', (slot, oldItem, newItem) => {
    if (!isBreak(oldItem, newItem)) return
    state.brokenTools = [...(state.brokenTools ?? []), { item: oldItem.name, at: Date.now() }].slice(-KEEP)
    console.log(`[bot] ${oldItem.name} が壊れた`)
  })
}

const hasKind = (bot, kind) => bot.inventory.items().some((i) => toolKind(i.name) === kind)

// 最近壊れた道具（新しい順）。replaced: 同じ種類の道具をもう持っている
export function recentBreaks (bot, state, now = Date.now()) {
  return (state.brokenTools ?? [])
    .filter((b) => now - b.at <= RECENT_MS)
    .reverse()
    .map((b) => ({ item: b.item, seconds_ago: Math.round((now - b.at) / 1000), replaced: hasKind(bot, toolKind(b.item)) }))
}

// 作り直す道具（壊れて、同じ種類をまだ持っていないもの。種類ごとに一番新しいもの）
export function toReplace (bot, state, now = Date.now()) {
  const seen = new Set()
  return recentBreaks(bot, state, now).filter((b) => {
    const kind = toolKind(b.item)
    if (b.replaced || seen.has(kind)) return false
    seen.add(kind)
    return true
  }).map((b) => ({ item: b.item, kind: toolKind(b.item) }))
}

// 道具の格（上ほど良い）。金は脆いので上に数えない
export const TIERS = ['wooden', 'stone', 'iron', 'diamond', 'netherite']
const UPGRADE_KINDS = ['pickaxe', 'axe', 'shovel', 'sword']
export const tierOf = (name) => TIERS.indexOf(name.split('_')[0])

// 持っている道具の種類ごとに、今より上の格（作る候補を調べる順: 上から）。2026-09-27: 石のつるはしを
// 作れるのに、木のつるはしを使い続けていた
export function upgradesToTry (bot) {
  const best = {}
  for (const i of bot.inventory?.items?.() ?? []) {
    const kind = toolKind(i.name)
    if (!UPGRADE_KINDS.includes(kind)) continue
    best[kind] = Math.max(best[kind] ?? -1, tierOf(i.name))
  }
  return Object.entries(best).flatMap(([kind, tier]) =>
    TIERS.slice(tier + 1, TIERS.indexOf('diamond') + 1).reverse().map((t) => ({ item: `${t}_${kind}`, kind, from: TIERS[tier] })).slice(0, 3))
}

// 夜に戦いに出る前にチェストに預けない物: 武器、防具、盾、食べ物、松明
export const KEEP_WHEN_FIGHTING = /_(sword|axe)$|_(helmet|chestplate|leggings|boots)$|^shield$|^torch$|^(cooked_|bread$|apple$|baked_potato$|carrot$|golden_carrot$|golden_apple$)/

// 行動の結果に添える一言（この行動の間に壊れたもの）
export function brokeSince (state, since) {
  const broke = (state.brokenTools ?? []).filter((b) => b.at >= since).map((b) => b.item)
  return broke.length ? `; the ${broke.join(', ')} broke` : ''
}
