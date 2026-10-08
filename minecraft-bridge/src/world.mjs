// ソルバーと候補のための、ボットのまわりにあるもの: 掘れるブロック、狩れる動物、作業台。
// 観測ごとにスナップショットを1つ作り、検索結果はその中にキャッシュする。

import { isLog, inventoryCounts, nearbyEntities } from './observe.mjs'
import { inHouse } from './home.mjs'
import { liquidReason } from './liquid.mjs'
import { gatherableLog, isNaturalLogName } from './trees.mjs'
import { findTable, findFurnace, knownFurnace, isLeaves, isUnreachable, HUNGER_URGENT } from './primitives.mjs'
import { HUNTABLE } from './knowledge.mjs'
import { isRipe, cropByBlock } from './farming.mjs'
import { cropFarms, finishedMine, mineSummaries, inMineBody, inTown, onFarmGround } from './facilities.mjs'
import { inHomeRoom } from './builds.mjs'
import { REMEMBERED_ANIMALS, storedCounts, smeltingCounts } from './memory.mjs'
import { rememberedKinds } from './worldmap-places.mjs'
import { isQuarryKind, quarrySpot, usableShaft, QUARRY_RADIUS } from './quarry.mjs'
import { keptGround, findCount } from './leveling.mjs'
import { mineFromHome, minePlan, shallowStone, caveOre } from './mining.mjs'

const DIG_RADIUS = 32
const SOIL_KINDS = new Set(['dirt', 'grass_block', 'coarse_dirt', 'podzol'])
const DIG_DY = 4 // 登ったりトンネルを掘ったりせずに掘れる、足元から上下のブロック数
const MAX_LOG_HEIGHT = 5 // 地面に立って手が届く、地面からの原木の高さ（目の高さ 1.6 ＋ 届く距離 4.5）
const MAX_LOG_HEIGHT_TOWER = 16 // 土を積んで（1x1 の塔）届く上限。積める高さは持っている土の数まで
const DIRT_SEARCH = 12 // 土を持っていないとき、積む土を掘って集められる近さ
const DIRT_SEEN_MS = 3000
const HUNT_RADIUS = 32
const TARGETS_PER_KIND = 3
export const SCAFFOLD_SOURCES = ['dirt', 'grass_block', 'coarse_dirt', 'podzol'] // 素手で掘れて、積む土が落ちるブロック

// 地面のブロックから何段上の原木まで届くか: 立って届く高さに、積める土の分を足す（積んだ塔の上に立つので、1 つは余裕）
export function reachableLogHeight (dirt) {
  return Math.min(MAX_LOG_HEIGHT_TOWER, Math.max(MAX_LOG_HEIGHT, MAX_LOG_HEIGHT - 1 + dirt))
}

// 地面のブロックから何段上の原木に届くには、土がいくつ要るか（逆。立って届くなら 0）
export const dirtForLogHeight = (height) => (height <= MAX_LOG_HEIGHT ? 0 : height - MAX_LOG_HEIGHT + 1)

// 原木から、その下の最初の地面（葉でない固いブロック）までの段数（地面の 1 つ上に立つと、dy - 1 段上）。原木と空気は通り越す。水・溶岩の上や、上限までに地面がなければ null
export function logHeightAbove (bot, pos, max = MAX_LOG_HEIGHT_TOWER) {
  for (let dy = 1; dy <= max; dy++) {
    const below = bot.blockAt(pos.offset(0, -dy, 0))
    if (!below) return null
    if (isLog(below.name) || below.boundingBox === 'empty') {
      if (/water|lava/.test(below.name)) return null
      continue
    }
    return isLeaves(below.name) ? null : dy
  }
  return null
}

// 持っていなくても、近くに掘れる土があれば、積む土は集められる（dig が先に集める）。数えるのは掘れる数だけ（3 秒覚える）
const dirtSeen = new WeakMap()
function nearbyDirt (bot, now = Date.now()) {
  const seen = dirtSeen.get(bot)
  if (seen && now - seen.at < DIRT_SEEN_MS) return seen.count
  const ids = SCAFFOLD_SOURCES.map((n) => bot.registry.blocksByName[n]?.id).filter((i) => i != null)
  const count = ids.length ? bot.findBlocks({ matching: ids, maxDistance: DIRT_SEARCH, count: MAX_LOG_HEIGHT_TOWER }).length : 0
  dirtSeen.set(bot, { at: now, count })
  return count
}

// 原木に届くか: 下へ、原木と空気を通り越して最初の地面（葉でない固いブロック）までの高さで決める。
// 前は地面から原木が続いていることを求めていたので、下の原木を切った後の浮いた幹や、段差の上に
// 張り出した木は、近くにあっても木と見なさなかった（2026-09-26）
function logReachable (bot, block) {
  const height = logHeightAbove(bot, block.position, reachableLogHeight((inventoryCounts(bot).dirt ?? 0) + nearbyDirt(bot)))
  return height !== null
}

const NEIGHBORS = [[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]]
// 空気に接している（水にも当たり判定がない: town2 は海の下の石炭を掘って溺れた）
const AIR = new Set(['air', 'cave_air'])
export const exposed = (bot, p) => NEIGHBORS.some(([x, y, z]) => AIR.has(bot.blockAt(p.offset(x, y, z))?.name))

// 上に岩が重なっていない（空が見える）か。葉は数えない
const SKY_CHECK = 12
function openToSky (bot, p) {
  for (let dy = 1; dy <= SKY_CHECK; dy++) {
    const b = bot.blockAt(p.offset(0, dy, 0))
    if (!b) return true
    if (b.boundingBox === 'block' && !isLeaves(b.name)) return false
  }
  return true
}

// すくえる水（流れていない水源。空気に面したもの）: 一番近いもの。バケツで水を汲む先（solver の fill）。
// 地下の洞窟の水は選ばない（歩いて届かず、経路探索が戸口から掘り下げた: 2026-10-07。地表の池・川・海だけ）
export function waterSource (bot, state) {
  const id = bot.registry.blocksByName.water?.id
  if (id == null) return null
  const me = bot.entity.position
  return bot.findBlocks({ matching: id, maxDistance: DIG_RADIUS, count: 64 })
    .filter((p) => {
      const b = bot.blockAt(p)
      return Number(b?.getProperties?.().level ?? b?.metadata ?? 1) === 0 && !inHouse(state, p) && exposed(bot, p) && !isUnreachable(state, p) && openToSky(bot, p)
    })
    .sort((a, b) => a.distanceTo(me) - b.distanceTo(me))[0] ?? null
}

// 掘ってよいブロックか: 家の一部でなく、空気に面し、届き、水や溶岩をせき止めていない（digTargets が使う）
export function diggable (bot, state, name, p) {
  const me = bot.entity.position
  return !inHouse(state, p) && exposed(bot, p) && !isUnreachable(state, p) && isRipe(bot.blockAt(p)) &&
    !liquidReason(bot, p, bot.blockAt(p)) && (!isNaturalLogName(name) || gatherableLog(bot, state, p)) &&
    (isLog(name) ? logReachable(bot, bot.blockAt(p)) : Math.abs(p.y - Math.floor(me.y)) <= DIG_DY) &&
    !shallowStone(bot, state, p, name) && // 家があるとき、地表に近い石は採掘場から（設計書 68）
    !caveOre(state, p, name) && // 洞窟に出ている深い鉱石は取らない。採掘場の壁に出たものだけ（設計書 86 §4）
    !keptGround(state, p, name) && // 整地する土地の下は掘らない（土は整地の外から）
    !inMineBody(state, p) // 採掘場の階段の柱と段の床は掘らない（底から上がれなくなる）
}

// この種類のうちボットが掘れるブロック: 近い順。家の一部は含めない。石・土などの地形は、採掘の場所が
// あればその近くを先に、場所に近い順（毎回違う所から掘って穴だらけにしない。quarry.mjs、設計書 64）
export function digTargets (bot, state, name, limit = TARGETS_PER_KIND) {
  const id = bot.registry.blocksByName[name]?.id
  if (id == null) return []
  const me = bot.entity.position
  const ok = (p) => diggable(bot, state, name, p)
  const spot = isQuarryKind(name) ? quarrySpot(state, me) : null
  if (spot) {
    const there = bot.findBlocks({ matching: id, point: spot, maxDistance: QUARRY_RADIUS, count: findCount(state) }).filter(ok)
    if (there.length) return there.sort((a, b) => a.distanceTo(spot) - b.distanceTo(spot) || a.distanceTo(me) - b.distanceTo(me)).slice(0, limit)
  }
  // 土は街の外から: 畑の地面、街の中、外の順に後回しにする（外が遠くても、なければ中から）。近い順の 64 個に街の
  // 土だけが並ばないように、多めに探す
  const soil = SOIL_KINDS.has(name)
  const rank = (p) => soil ? (onFarmGround(state, p) ? 2 : inTown(state, p) ? 1 : 0) : 0
  return bot.findBlocks({ matching: id, maxDistance: DIG_RADIUS, count: soil ? findCount(state, 512) : findCount(state) })
    .filter(ok)
    .sort((a, b) => rank(a) - rank(b) || a.distanceTo(me) - b.distanceTo(me))
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
    // 前に掘った階段の底から続けて届く鉱石（底より下）を先に（毎回新しい階段を掘らない）: 底に近い順
    .map((t) => ({ ...t, shaft: usableShaft(state, me, t.pos.y) }))
    .sort((a, b) => (a.shaft ? 0 : 1) - (b.shaft ? 0 : 1) ||
      (a.shaft ? Math.hypot(a.pos.x - a.shaft.bottom.x, a.pos.z - a.shaft.bottom.z) - Math.hypot(b.pos.x - b.shaft.bottom.x, b.pos.z - b.shaft.bottom.z) : 0) ||
      a.depth - b.depth || a.pos.distanceTo(me) - b.pos.distanceTo(me))
    .map(({ pos, depth }) => ({ pos, depth }))
    .slice(0, limit)
}

export function huntTargets (bot, names, limit = TARGETS_PER_KIND) {
  const me = bot.entity.position
  return nearbyEntities(bot)
    .filter(({ e, dist }) => names.includes(e.name) && HUNTABLE.has(e.name) && dist <= HUNT_RADIUS && Math.abs(e.position.y - me.y) < 4)
    .slice(0, limit)
}

// いまのまわりにいる、覚えておく動物（memory.mjs）: [{ kind, pos }]。ブロックは視界の地図が持つ
// （worldmap.mjs、docs/design/59_world_map.md）。前はここで、行動の終わりに周囲 32 m の決まった種類の
// ブロックを調べていた
export function sightings (bot) {
  return nearbyEntities(bot)
    .filter(({ e, dist }) => REMEMBERED_ANIMALS.has(e.name) && dist <= HUNT_RADIUS)
    .map(({ e }) => ({ kind: e.name, pos: e.position }))
}

// チェストから取り出してよいもの: 大目標がそこに取っておくもの以外すべて（備蓄として取っておく
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
  // 掘り下げ: 埋まった物まで階段で下りてよい（2026-09-26: 上限なし。小目標の dig_depth があればそこまで）
  const surfaceY = Math.floor(state.goal?.surfaceY ?? bot.entity.position.y)
  const buriedCache = new Map()
  const buried = (name) => {
    if (!buriedCache.has(name)) buriedCache.set(name, buriedTargets(bot, state, name, surfaceY))
    return buriedCache.get(name)
  }
  return {
    inventory: inventoryCounts(bot),
    stored: takeable(storedCounts(state.memory ?? {}), state.goal?.keep ?? [], bot.food <= HUNGER_URGENT),
    remembered: state.memory ? rememberedKinds(state, bot.entity.position, Number(bot.time.age)) : new Set(),
    blocks: new Proxy({}, { get: (_, name) => typeof name === 'string' ? dig(name).length : undefined }),
    mobs: new Proxy({}, { get: (_, name) => typeof name === 'string' ? hunt(name).length : undefined }),
    table: findTable(bot, state) ? 'near' : null,
    furnace: findFurnace(bot, state) ? 'near' : null,
    // 近くにはないが、最後に見た（家の）かまど: そこまで歩いて焼ける
    furnaceAway: knownFurnace(bot, state)?.near === false,
    smelting: smeltingCounts(state.memory ?? {}, (f) => !state.home || inHomeRoom(state.home, f)), // 家のかまどだけ（精錬は家の中）
    unlocked: (item) => state.recipeBook.recipesFor(item).length > 0,
    dig,
    hunt,
    buried,
    water: () => waterSource(bot, state),
    // 畑（設計した畑と家の畑）のその作物（ブロック名で）。作物でないか、植わっていなければ null
    farm: (block) => {
      const crop = cropByBlock(block)
      const f = crop ? cropFarms(bot, state, crop) : null
      return f?.planted ? f : null
    },
    // 下りきった採掘場（設計書 47 §16）: 埋まった石・鉱石はそこから下りて探す
    mine: finishedMine(bot, state),
    mines: mineSummaries(state),
    // 家のそばの採掘場から掘るか（設計書 68）と、その次の一手（minePlan）
    home: mineFromHome(state),
    minePlan: (sources) => minePlan(bot, state, sources),
    // 深さの上限は付けない（安全の決まりは掘るときに見る）。dig_depth があればそこまで
    digDepth: state.goal?.spec?.dig_depth ?? Infinity
  }
}
