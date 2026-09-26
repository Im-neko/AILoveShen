// ソルバーと候補のための、ボットのまわりにあるもの: 掘れるブロック、狩れる動物、作業台。
// 観測ごとにスナップショットを1つ作り、検索結果はその中にキャッシュする。

import { isLog, inventoryCounts, nearbyEntities } from './observe.mjs'
import { inHouse } from './home.mjs'
import { findTable, findFurnace, isLeaves, isUnreachable, HUNGER_URGENT } from './primitives.mjs'
import { HUNTABLE } from './knowledge.mjs'
import { REMEMBERED_BLOCK, REMEMBERED_STONE, REMEMBERED_ANIMALS, storedCounts, smeltingCounts, recallableKinds } from './memory.mjs'

const DIG_RADIUS = 32
const DIG_DY = 4 // 登ったりトンネルを掘ったりせずに掘れる、足元から上下のブロック数
const MAX_LOG_HEIGHT = 4 // 幹の下の地面からこの高さまでの原木は地面から切る
const HUNT_RADIUS = 32
const TARGETS_PER_KIND = 3

// 幹の下の地面が MAX_LOG_HEIGHT ブロック以内なら、その原木には届く。
function logReachable (bot, block) {
  for (let dy = 1; dy <= MAX_LOG_HEIGHT; dy++) {
    const below = bot.blockAt(block.position.offset(0, -dy, 0))
    if (!below) return false
    if (isLog(below.name)) continue
    return below.boundingBox === 'block' && !isLeaves(below.name)
  }
  return false
}

const NEIGHBORS = [[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]]
// 空気に接している（水にも当たり判定がない: town2 は海の下の石炭を掘って溺れた）
const AIR = new Set(['air', 'cave_air'])
export const exposed = (bot, p) => NEIGHBORS.some(([x, y, z]) => AIR.has(bot.blockAt(p.offset(x, y, z))?.name))

// この種類のうちボットが掘れるブロック: 近い順。家の一部は含めない
export function digTargets (bot, state, name, limit = TARGETS_PER_KIND) {
  const id = bot.registry.blocksByName[name]?.id
  if (id == null) return []
  const me = bot.entity.position
  return bot.findBlocks({ matching: id, maxDistance: DIG_RADIUS, count: 64 })
    .filter((p) => !inHouse(state, p) && exposed(bot, p) && !isUnreachable(state, p) &&
      (isLog(name) ? logReachable(bot, bot.blockAt(p)) : Math.abs(p.y - Math.floor(me.y)) <= DIG_DY))
    .sort((a, b) => a.distanceTo(me) - b.distanceTo(me))
    .slice(0, limit)
}

// 空気に面していない（埋まった）この種類のブロックのうち、足元より下にあるもの: 近い順、
// depth は surfaceY（目標を立てたときの足元）から何段下か。階段で掘り下げる先（17_dig_down.md）
export function buriedTargets (bot, state, name, surfaceY, limit = TARGETS_PER_KIND) {
  const id = bot.registry.blocksByName[name]?.id
  if (id == null) return []
  const me = bot.entity.position
  return bot.findBlocks({ matching: id, maxDistance: DIG_RADIUS, count: 256 })
    .filter((p) => p.y < Math.floor(me.y) && !inHouse(state, p) && !exposed(bot, p) && !isUnreachable(state, p))
    .map((p) => ({ pos: p, depth: Math.max(1, surfaceY - p.y) }))
    .sort((a, b) => a.depth - b.depth || a.pos.distanceTo(me) - b.pos.distanceTo(me))
    .slice(0, limit)
}

export function huntTargets (bot, names, limit = TARGETS_PER_KIND) {
  const me = bot.entity.position
  return nearbyEntities(bot)
    .filter(({ e, dist }) => names.includes(e.name) && HUNTABLE.has(e.name) && dist <= HUNT_RADIUS && Math.abs(e.position.y - me.y) < 4)
    .slice(0, limit)
}

// いまのまわりにある、覚えておく価値のあるもの（memory.mjs）: [{ kind, pos }]。ブロックはボットが
// 掘れる、空気に接しているものだけ: 埋まった鉱石も数えると探索に出てしまった（town2: 「家のまわり」に
// 石炭鉱石が 64 個あったが、どれにも届かなかった）
export function sightings (bot) {
  const seen = (pattern, count) => {
    const ids = Object.values(bot.registry.blocksByName).filter((b) => pattern.test(b.name)).map((b) => b.id)
    return bot.findBlocks({ matching: ids, maxDistance: DIG_RADIUS, count })
      .filter((p) => exposed(bot, p))
      .map((p) => ({ kind: bot.blockAt(p)?.name, pos: p }))
      .filter((s) => s.kind)
  }
  const blocks = [...seen(REMEMBERED_BLOCK, 512), ...seen(REMEMBERED_STONE, 64)]
  const animals = nearbyEntities(bot)
    .filter(({ e, dist }) => REMEMBERED_ANIMALS.has(e.name) && dist <= HUNT_RADIUS)
    .map(({ e }) => ({ kind: e.name, pos: e.position }))
  return [...blocks, ...animals]
}

// チェストから取り出してよいもの: 中目標がそこに取っておくもの以外すべて（備蓄として取っておく
// 食料も、飢えているときは食べる）
export function takeable (stored, keep, starving) {
  const out = { ...stored }
  for (const k of keep) {
    if (k.food && starving) continue
    let left = k.count
    for (const m of k.members) {
      const n = Math.min(out[m] ?? 0, left)
      if (!n) continue
      out[m] -= n
      left -= n
    }
  }
  return out
}

// ソルバーから見たワールド。掘る・狩る対象の検索結果は候補のためにキャッシュする
export function snapshot (bot, state) {
  const digCache = new Map()
  const dig = (name) => {
    if (!digCache.has(name)) digCache.set(name, digTargets(bot, state, name))
    return digCache.get(name)
  }
  const hunt = (name) => huntTargets(bot, [name])
  // 掘り下げ: 深さは小目標の dig_depth（Gemini が決める。なければ掘り下げない）
  const surfaceY = Math.floor(state.goal?.surfaceY ?? bot.entity.position.y)
  const buriedCache = new Map()
  const buried = (name) => {
    if (!buriedCache.has(name)) buriedCache.set(name, buriedTargets(bot, state, name, surfaceY))
    return buriedCache.get(name)
  }
  return {
    inventory: inventoryCounts(bot),
    stored: takeable(storedCounts(state.memory ?? {}), state.goal?.keep ?? [], bot.food <= HUNGER_URGENT),
    remembered: state.memory ? recallableKinds(state.memory, bot.entity.position, Number(bot.time.age)) : new Set(),
    blocks: new Proxy({}, { get: (_, name) => typeof name === 'string' ? dig(name).length : undefined }),
    mobs: new Proxy({}, { get: (_, name) => typeof name === 'string' ? hunt(name).length : undefined }),
    table: findTable(bot, state) ? 'near' : null,
    furnace: findFurnace(bot) ? 'near' : null,
    smelting: smeltingCounts(state.memory ?? {}),
    unlocked: (item) => state.recipeBook.recipesFor(item).length > 0,
    dig,
    hunt,
    buried,
    digDepth: state.goal?.spec?.dig_depth ?? 0
  }
}
