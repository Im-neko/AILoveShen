// 掘ってよい所か: dig が断る規則の全部を、ここ 1 か所に持つ（docs/design/182）。
//
// 断る規則が dig の中、find_blocks、寄り道、覚えている場所の一覧にばらばらに写されていて、写し漏れた
// 一覧が「行ける・掘れる」と見せた所へ、LLM が行っては dig に断られるのをくり返した（2026-10-16: 採掘場の
// 階段の柱の鉄鉱石へ、3 時間で goto 20 回・dig 20 回）。LLM に場所を見せる所は、すべてここを通す。
import { protectedReason } from './home.mjs'
import { liquidReason } from './liquid.mjs'
import { mineBodyReason } from './facilities.mjs'
import { shallowStone, caveOre, SHALLOW_REMEDY, CAVE_ORE_REMEDY } from './mining.mjs'
import { keptGround, KEPT_REMEDY } from './leveling.mjs'

export const DEMOLISH_REMEDY = 'If taking it down is the point of this goal, dig it again with demolish: true'

// 掘ってよくない理由 { kind, why, remedy, soft }、掘ってよければ null。soft は断りでなく助言
// 規則の順は dig のもの。bot（と block）を渡すと、読み込まれた世界で見る規則（水を止める・地表に近い石）も見る:
// 覚えている場所の一覧は世界を持たないので、渡さない（その二つは dig の側で断る）
export function takeRefusal (state, p, name, { bot = null, block = null, demolish = false } = {}) {
  const wet = bot && block ? liquidReason(bot, p, block) : null
  if (wet) return { kind: 'liquid', why: wet, remedy: 'Leave it, or get the material somewhere else' }
  const held = demolish ? null : protectedReason(state, p, 0, block)
  if (held) return { kind: 'protected', why: held, remedy: DEMOLISH_REMEDY }
  const body = demolish ? null : mineBodyReason(state, p)
  if (body) return { kind: 'mine_body', why: body, remedy: 'Dig the rock beside the mine instead' }
  if (bot && shallowStone(bot, state, p, name)) return { kind: 'shallow', why: 'it is close to the surface', remedy: SHALLOW_REMEDY }
  if (!demolish && caveOre(state, p, name)) return { kind: 'cave_ore', why: CAVE_ORE_REMEDY, remedy: '' }
  if (keptGround(state, p, name)) return { kind: 'kept', why: KEPT_REMEDY, remedy: '', soft: true }
  return null
}

// dig の断りの文
export const refusalText = (r, name, at) => `will not dig ${name} at ${at}: ${r.why}${r.remedy ? `. ${r.remedy}` : ''}`
