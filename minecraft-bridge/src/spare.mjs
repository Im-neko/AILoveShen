// 持ち物がいっぱいのときの、余っている物（docs/design/66_full_inventory.md）。
//
// 整地や採掘で土・丸石・花崗岩などが溜まり続け（2026-09-30: 土 447 と丸石 320 で 36 スロットが
// 埋まり、落とした肉が拾えず食料が集まらなかった）、家にチェストがなければ預けることもできなかった。
// 預けるのは家のチェスト（candidates.mjs storeSpare）、チェストがないときと外にいるときは、
// 余った分を捨てる（primitives.mjs toss）。どちらも「何が余りか」はここで決める。
import { TERRAIN } from './leveling.mjs'

// 空きスロットがこれ未満（0〜1）になったら、捨てる手を出す
export const TOSS_BELOW = 2
// 残しておく数（整地の穴埋めや足場に使う）。これを超えた分が余り
export const RESERVE = { dirt: 64, cobblestone: 64 }
// 捨てた物を拾い直さない時間（ゲームの tick: 5 分）と範囲
export const TOSS_IGNORE_TICKS = 5 * 60 * 20
export const TOSS_IGNORE_RADIUS = 16

const FLOWERS = /^(dandelion|poppy|blue_orchid|allium|azure_bluet|(red|orange|white|pink)_tulip|oxeye_daisy|cornflower|lily_of_the_valley|sunflower|lilac|rose_bush|peony)$/
// 目標に要る物は keep が守る。ここは「要らなくなりやすい物」だけ: 自然の地形（鉱石は除く）、丸石、花、羊毛
export const isTossable = (name) => (TERRAIN.test(name) && !name.endsWith('_ore')) ||
  name === 'cobblestone' || name === 'cobbled_deepslate' || FLOWERS.test(name) || name.endsWith('_wool')

// 目標が使う物（食料、目標の種類ごとの材料、目標の品物）。預ける物も捨てる物も、これは残す
const GOAL_GROUPS = { built: ['log', 'planks', 'door'], placed: ['bed', 'wool', 'planks'] }
export function goalKeep (knowledge, spec) {
  if (!knowledge) return new Set()
  const groups = ['food', ...(GOAL_GROUPS[spec?.predicate] ?? []), ...(spec?.item ? [spec.item] : [])]
  return new Set(groups.flatMap((g) => knowledge.resolve(g).members))
}

// 捨ててよい余り: [{ item, count }]（多い順）。keep は残す名前
export function spareStacks (bot, keep = new Set()) {
  const counts = {}
  for (const i of bot.inventory.items()) counts[i.name] = (counts[i.name] ?? 0) + i.count
  return Object.entries(counts)
    .filter(([name]) => isTossable(name) && !keep.has(name))
    .map(([item, n]) => ({ item, count: n - (RESERVE[item] ?? 0) }))
    .filter((s) => s.count > 0)
    .sort((a, b) => b.count - a.count)
}

// 持ち物の空きスロット数（数えられなければ、空いているとみなす）
export const emptySlots = (bot) => bot.inventory.emptySlotCount?.() ?? 36

// name を持ち物に入れられるか（空きスロットか、まだ入るスタックがある）
export function canHold (bot, name) {
  if (!name || emptySlots(bot) > 0) return true
  const max = bot.registry.itemsByName[name]?.stackSize ?? 64
  return bot.inventory.items().some((i) => i.name === name && i.count < max)
}

// 捨てた物: 名前ごとに捨てた場所と時刻を覚え、その近くの同じ物は拾う候補にも「目の前に落ちている物」にも
// 出さない（出すと、捨てた物を拾いに行って持ち物がまた埋まる）
export function noteTossed (state, bot, item) {
  const me = bot.entity.position
  state.tossed = { ...(state.tossed ?? {}), [item]: { x: me.x, z: me.z, at: Number(bot.time.age) } }
}

export function isTossed (state, bot, e) {
  const t = state?.tossed?.[e.getDroppedItem?.()?.name]
  if (!t || Number(bot.time.age) - t.at > TOSS_IGNORE_TICKS) return false
  return Math.hypot(e.position.x - t.x, e.position.z - t.z) <= TOSS_IGNORE_RADIUS
}
