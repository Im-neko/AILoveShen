// ついで採取: 探索中に見つけた鉱石や素材は、目標のものでなくても集める（docs/design/79_windfall.md）。
// 鉱石（石炭・銅・鉄・ラピス・レッドストーン・金・ダイヤ・エメラルド）はいつも対象、そのほかは設定の一覧
// （state.collect.extra: PUT /collect、Python の minecraft.collect）。採れるかは今持っているツルハシの
// 階級で決める（ダイヤは鉄以上、黒曜石はダイヤ）: 採れない物は何もしない。場所は視界の地図が覚えている
// ので、あとで道具を作ったら「行く」候補（trip）が出る。
//   now: 12 m 以内で、いま視界に入っているもの（壁の向こうは対象外）。Python の StepPicker が聞かずに採る（windfall: 'now'）
//   trip: 12〜48 m の覚えている場所。候補に出すだけ（windfall: 'trip'）
import vec3Pkg from 'vec3'
import { inventoryCounts, dayPhase, isUnderground, UNDERGROUND_BELOW } from './observe.mjs'
import { diggable } from './world.mjs'
import { emptySlots } from './spare.mjs'
import { mapPlaces } from './worldmap-places.mjs'
import { reachableThreats, HEALTH_CRITICAL, HUNGER_URGENT } from './primitives.mjs'
import { riding } from './boat.mjs'
import { isSwimming } from './swim.mjs'
import { torchesNeeded } from './trail.mjs'
import { armed } from './goals.mjs'

const { Vec3 } = vec3Pkg
export const WINDFALL_ORE = /^(deepslate_)?(coal|copper|iron|lapis|redstone|gold|diamond|emerald)_ore$/
export const NOW_M = 12
export const TRIP_M = 48
const OFFERED = 2
const FREE_SLOTS = 2
const UNDERGROUND_Y = 54
// この小目標の間は寄り道しない（身を守る・家に帰る・戦う: 帰る途中に銅を 8 分掘り続けた 2026-10-01）。
// 夜を越すのは、地下にいれば寄り道してよい（safeToDetour）
const NO_DETOUR_GOALS = new Set(['at_home', 'through_night', 'cleared'])

const fmt = (p) => `${p.x},${p.y},${p.z}`

// 対象の種類（ブロック名）: 鉱石と、設定の一覧（レジストリにあるもの）
export function collectKinds (bot, state) {
  const names = Object.keys(bot.registry.blocksByName)
  const ores = names.filter((n) => WINDFALL_ORE.test(n))
  const extra = (state.collect?.extra ?? []).filter((n) => bot.registry.blocksByName[n])
  return [...new Set([...ores, ...extra])]
}

// 今持っている道具で掘れるか（道具が要らないブロックは常に掘れる）
export function canHarvest (bot, name) {
  const tools = bot.registry.blocksByName[name]?.harvestTools
  if (!tools || !Object.keys(tools).length) return true
  const ids = Object.keys(tools).map(Number)
  return bot.inventory.items().some((i) => ids.includes(i.type))
}

function carriedFrom (bot, knowledge, name) {
  const drops = knowledge?.dropsOf(name) ?? []
  const inv = inventoryCounts(bot)
  return (drops.length ? drops : [name]).reduce((sum, d) => sum + (inv[d] ?? 0), 0)
}

// 目標に要らない素材は 1 スタックまで: 掘って取れる物を 1 スタック持っていたら、もう採らない
// （持ち物をいっぱいにしない。ユーザー 2026-10-01: 「目的外の素材を集めるのは 1 スタックまで」）
function atStack (bot, knowledge, name) {
  const drops = knowledge?.dropsOf(name) ?? []
  const stack = Math.min(...(drops.length ? drops : [name]).map((d) => bot.registry.itemsByName[d]?.stackSize ?? 64))
  return carriedFrom(bot, knowledge, name) >= stack
}

// 寄り道してよいか: 小目標が身を守るものでなく、体力・満腹度に余裕があり、近くに敵がいなくて、昼か地下で（地下は武器あり）、持ち物に空きがある
function safeToDetour (bot, state) {
  // 地下の夜は昼と同じ（設計書: 地下は帰らなくてよい）なので、地下で夜を越す間は寄り道してよい
  const predicate = state.goal?.spec?.predicate
  if (NO_DETOUR_GOALS.has(predicate) && !(predicate === 'through_night' && isUnderground(bot))) return false
  if (bot.health <= HEALTH_CRITICAL || bot.food <= HUNGER_URGENT) return false
  // 地下は武器を持っているときだけ（無武器で骷髏に撃たれ続けて死んだ: 2026-10-01 19:44）
  if (bot.entity.position.y < UNDERGROUND_BELOW && !armed(bot)) return false
  if (riding(bot) || isSwimming(bot) || reachableThreats(bot, state).length) return false
  if (dayPhase(bot.time.timeOfDay) !== 'day' && !isUnderground(bot)) return false
  return emptySlots(bot) >= FREE_SLOTS
}

// 目から見えているか（間に別のブロックがない）。findBlocks は読み込み済みの全ブロックを探すので、壁や岩の
// 向こうの鉱石も拾ってしまい、「見つけた」はずのない鉄を掘りに行っていた（2026-10-04: 原木の小目標中に鉄を 7 手）
const inSight = (bot, p) => {
  const block = bot.blockAt(p)
  return block != null && bot.canSeeBlock(block) === true
}

// 近くで見つけたもの: 候補（windfall: 'now'）。近い順に OFFERED 個まで
export function windfallNow (bot, state, knowledge) {
  if (typeof bot.findBlocks !== 'function' || !safeToDetour(bot, state)) return []
  const kinds = collectKinds(bot, state).filter((n) => canHarvest(bot, n) && !atStack(bot, knowledge, n))
  if (!kinds.length) return []
  const me = bot.entity.position
  return bot.findBlocks({ matching: kinds.map((n) => bot.registry.blocksByName[n].id), maxDistance: NOW_M, count: 48 })
    .map((p) => ({ p, name: bot.blockAt(p)?.name }))
    .filter(({ p, name }) => name && kinds.includes(name) && diggable(bot, state, name, p))
    .sort((a, b) => a.p.distanceTo(me) - b.p.distanceTo(me))
    .filter(({ p }) => inSight(bot, p))
    .slice(0, OFFERED)
    .map(({ p, name }) => ({
      id: `collect ${name} at ${fmt(p)} (seen nearby; the goal does not need it)`,
      verb: 'dig', target: name, distance: Math.round(p.distanceTo(me) * 10) / 10, pos: p, block: name,
      windfall: 'now', also: `${name} found on the way`
    }))
}

// 前に見て覚えている場所（視界の地図）: いま採れるものだけ、遠すぎず、地下へは松明が足りるときだけ。候補（windfall: 'trip'）
export function windfallTrips (bot, state, knowledge) {
  if (!state.worldMap || !safeToDetour(bot, state)) return []
  const kinds = collectKinds(bot, state).filter((n) => canHarvest(bot, n) && !atStack(bot, knowledge, n))
  if (!kinds.length) return []
  const me = bot.entity.position
  const torches = inventoryCounts(bot).torch ?? 0
  const places = mapPlaces(state, me, Number(bot.time.age), kinds)
  return Object.entries(places)
    .flatMap(([kind, list]) => list.map((p) => ({ kind, p, d: Math.hypot(p.x - me.x, p.y - me.y, p.z - me.z) })))
    .filter(({ p, d }) => d > NOW_M && d <= TRIP_M && (p.y > UNDERGROUND_Y || torches >= torchesNeeded(d)))
    .sort((a, b) => a.d - b.d)
    .slice(0, OFFERED)
    .map(({ kind, p, d }) => {
      const pos = new Vec3(p.x, p.y, p.z)
      return {
        id: `go to the ${kind} seen at ${fmt(pos)} and mine it (the goal does not need it)`,
        verb: 'goto_pos', target: kind, distance: Math.round(d), pos, range: 2,
        windfall: 'trip', also: `${kind} seen before`
      }
    })
}
