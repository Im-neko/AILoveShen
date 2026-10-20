// 目標: Gemini が目標を立てる語彙、目標を満たしたかの判定、目標のためにできること。
//
// 目標はワールドだけから判定する（モデルの言うことからは判定しない）。必要なアイテムはソルバーが
// 分解する。家、帰る家、夜はそれぞれ独自の手順を足す。
//
//   have(item, count)        アイテムかグループ（planks, log, door, bed, wool, food, ...）を `count` 個持っている
//   built()                  家の計画のブロックがすべて置かれている
//   placed(item, where)      家にベッドがある（いまのところ対応している配置はこれだけ）
//   at_home()                ドアを閉めて家の中にいる
//   through_night()          夜が明けた（家の中にいたか、寝ていた）
//   explored(distance)       目標を立てた場所から（水平に）この距離だけ離れた
//   found(item, distance)    探しているもの（モブか群系の名前）が見つかった。始めた所から distance ブロック探して見つからなければ行き止まり（found.mjs、設計書 176）
//   cleared()                ドアの近くで待つ敵対モブがいない（昼だけ: 外に出て戦う）
//   stored(item, count)      家のチェストにアイテムかグループが `count` 個ある（最後に開けたときの中身で）
//   lit(distance)            家のまわり半径 `distance` の地面に暗い所がない
//   surveyed(count)          街の候補地を `count` か所調べた（docs/design/16_town_site.md）
//   planted(item, count)     自分が植えた苗木（sapling か種類）が `count` 本ある（育った木も数える。設計書 33）
//   farmed(item, count)      畑（設計した畑と家の畑）に作物（wheat, carrots, potatoes, beetroots）が `count` マス植わっている（設計書 47 §14）
//   paved           街の理想図の道（roads.mjs）がすべて土の道になっている（設計書 200）
//   leveled(where, distance) where（home か x,y,z）のまわり半径 `distance` の地面がその高さにそろっている（設計書 41）
//   facility(name)           設計した施設（畑・植林場: facilities.mjs）のマスがすべて済んでいる（耕してまいた、植えた。設計書 47 §7）
//   custom(name)             Gemini が書いた判定（conditions.mjs）が成り立っている（設計書 42。候補は出さない: 道具で進める）

import vec3Pkg from 'vec3'
import { solve, nextStep, isGather } from './solver.mjs'
import { mineTorchesWanted, enoughToGo } from './mine-torches.mjs'
import { dayPhase, inventoryCounts, EXPLODES, isDark, isUnderground, fillCount } from './observe.mjs'
import { foundKind, foundNow } from './found.mjs'
import { isInside, isDoorOpen, hasBed, bedSpot, dangerOutside, homeEntryBlocked } from './home.mjs'
import { assessRisk, atLeast, describeRisk, SHELTER_LEVEL } from './risk.mjs'
import { homeChests, storedCounts } from './memory.mjs'
import { darkGround } from './lighting.mjs'
import { roadStatus } from './roads.mjs'
import { levelStatus, levelTarget, describeLevel, toolsMissing, fillNeeded, farmLevel, soilCount, keptByNotDemolishing, MIN_LEVEL, MAX_LEVEL } from './leveling.mjs'
import { cooking, isRawMeat, FAR_COOK_MIN } from './cooking.mjs'
import { runCheck } from './conditions.mjs'
import { placedToTake, takeableItem, HOME_FURNITURE, ownFurnitureInHome, isHomeFurnitureItem } from './furniture.mjs'
import { ensureSurvey, surveyedSites } from './survey.mjs'
import { CROPS, cropOf, plantingStatus, isSaplingItem, saplingMatches } from './farming.mjs'
import { toReplace, upgradesToTry, replacementTiers, KEEP_WHEN_FIGHTING } from './wear.mjs'
import { layoutStatus, updateStatus, mineStatus, ensureFarmShape, cropFarms, designedFarms, designedTreeFarms, workCell, HOME_FARM, FENCE_ITEM, FENCE_GATE_ITEM } from './facilities.mjs'
import { isNoPathTo } from './move.mjs'
import { checkPlacedSpec, countPlaced, placeSpots, describeWhere } from './placing.mjs'
import { reachableThreats, knownFurnace, LEG, SLEEP_FROM, SLEEP_UNTIL, HEALTH_CRITICAL, HUNGER_URGENT, WATER_AHEAD_MS } from './primitives.mjs'
import { boatItem, riding } from './boat.mjs'
import { inBurrow, burrowSpotCached } from './burrow.mjs'
import { campSiteCached, campMaterials } from './camp.mjs'
import { waterNeed } from './breath.mjs'
import { REGEN_FOOD, cannotHeal, needsFoodToHeal, valuablesCarried } from './healing.mjs'
import { isSwimming } from './swim.mjs'

const { Vec3 } = vec3Pkg
const MAX_COUNT = 256
const BURROW_HOME_DISTANCE = 64 // 家がこれより遠い夜は、帰る代わりに穴を掘る手も出す
const MAX_DIG_DEPTH = 384 // 任意の上限（省けば上限なし）
const MIN_EXPLORE = 8
const MIN_LIT = 8
const MAX_LIT = 32
const MAX_EXPLORE = 256
const MAX_FOUND = 128 // 探す範囲の上限（1 手で遠くまで歩いて迷った 2026-10-10 の教訓: 一度に決める範囲を絞る）
const EXPLORE_STEP = 20 // 探索の1ステップで進むおよその距離
const DAY_TICKS = 24000
export const TORCH_LOW = 4 // これより少ない松明は「足りない」（Python の goal_chooser.TORCH_LOW と同じ）
const isDusk = (phase) => phase === 'dusk' || phase === 'night' // 夕方と夜（夜を越す目標が「夜を見た」と数える）
const MORNING = 0 // また日が昇る時刻（明け方は 24000 = 0 で終わる）
const TICKS_PER_MINUTE = 1200

export const PREDICATES = ['have', 'built', 'placed', 'at_home', 'through_night', 'explored', 'found', 'cleared', 'stored', 'lit', 'surveyed', 'planted', 'farmed', 'leveled', 'paved', 'custom', 'facility']
// ワールドの状態だけから判定するので、大目標の完了条件にできる
// （ほかはその時点や、目標を立てた場所によって変わる）
export const CONDITION_PREDICATES = ['have', 'built', 'placed', 'stored', 'lit', 'surveyed', 'planted', 'farmed', 'leveled', 'paved', 'custom', 'facility']
const MAX_PLANTED = 32
const DEPOSIT_BATCH = 32 // これだけ持ったらチェストに入れに行く
const NEAR_HOME = 12
const FULL_SLOTS = 3
const SIDE_RADIUS = 8 // ほかの大目標の物を一緒に取るのは、このくらいそばにあるときだけ
const MAX_FARMED = 64

// 目標の指定を検証し、保持する目標の状態を返す。不正なら理由を添えて投げる
// 正しいが、何か（家、計画）ができるまで追えない目標
export class NotYetError extends Error {}

// どの目標も立てた場所を覚えている: 探索はそこから離れる方向に行い、行ったり来たりしない
// keep: 大目標のためにチェストに取っておくもの（[{item, count}]、大目標の stored() の条件）。
// ソルバーはこれを取り出さない。ただし飢えているときの食料は除く（world.mjs）
// dig_depth: 埋まった石・鉱石まで階段で掘り下げてよい深さ（目標を立てた足元から。Gemini が決める。
// なければ掘り下げない。docs/design/17_dig_down.md）
export function makeGoal (spec, bot, state, knowledge) {
  const p = bot.entity.position
  const goal = validGoal(spec, bot, state, knowledge)
  if (spec.dig_depth !== undefined && spec.dig_depth !== null) {
    const depth = Number(spec.dig_depth)
    if (!Number.isInteger(depth) || depth < 0 || depth > MAX_DIG_DEPTH) throw new Error(`dig_depth must be 0-${MAX_DIG_DEPTH}`)
    goal.spec.dig_depth = depth
  }
  const out = {
    ...goal,
    exploreFrom: { x: p.x, y: p.y, z: p.z },
    surfaceY: Math.floor(p.y),
    keep: validKeep(spec.keep ?? [], knowledge),
    also: validAlso(spec.also ?? [], knowledge)
  }
  if (spec.together?.length) Object.assign(out, together(spec, out, bot, state, knowledge))
  return out
}

// 小目標の束（設計書 44）: 先頭の条件と一緒に追う条件（合わせて MAX_BUNDLE まで）。束に入れられるのは
// 世界から判定できる条件だけ（夜・家・戦う・探検は 1 つで追う）。まだ判定できない条件（まだない建物・
// 判定）は外して、その理由を lines に出す
export const MAX_BUNDLE = 4
function together (spec, lead, bot, state, knowledge) {
  if (!Array.isArray(spec.together)) throw new Error('together must be a list of conditions')
  if (spec.together.length > MAX_BUNDLE - 1) throw new Error(`a bundle holds at most ${MAX_BUNDLE} conditions (the first and ${MAX_BUNDLE - 1} together)`)
  const members = [spec, ...spec.together]
  for (const m of members) {
    if (!CONDITION_PREDICATES.includes(m?.predicate)) throw new Error(`${m?.predicate} cannot be in a bundle; only conditions judged from the world: ${CONDITION_PREDICATES.join(', ')}`)
  }
  const out = []
  const skipped = []
  for (const m of spec.together) {
    try {
      out.push({ ...validGoal(m, bot, state, knowledge), exploreFrom: lead.exploreFrom, surfaceY: lead.surfaceY, keep: lead.keep, also: [] })
    } catch (e) {
      if (!(e instanceof NotYetError)) throw e
      skipped.push(`left out of the bundle: ${describeSpec(m)}: ${e.message}`)
    }
  }
  return { together: out, togetherSkipped: skipped }
}

export function describeSpec (spec) {
  const args = [spec.name, spec.item, spec.count, spec.where, spec.distance].filter((v) => v != null)
  if (spec.started) args.push('started')
  if (spec.demolish) args.push('demolish')
  return `${spec.predicate}(${args.join(', ')})`
}

// also: ほかの大目標のために集めたい物（[{item, count}]）。今の目標の途中で、すぐそばで取れるなら
// 一緒に取る候補を出す（木を見つけたら原木と、ついでに葉から苗木）。知らない物は黙って外す
const MAX_ALSO = 6
function validAlso (also, knowledge) {
  if (!Array.isArray(also)) return []
  return also.slice(0, MAX_ALSO).flatMap(({ item, count }) => {
    const n = Number(count)
    if (!Number.isInteger(n) || n < 1) return []
    try {
      return [{ item: String(item), count: Math.min(n, MAX_COUNT), members: knowledge.resolve(String(item)).members }]
    } catch {
      return []
    }
  })
}

function validKeep (keep, knowledge) {
  if (!Array.isArray(keep)) throw new Error('keep must be a list of {item, count}')
  const food = new Set(knowledge.resolve('food').members)
  return keep.map(({ item, count }) => {
    const n = Number(count)
    if (!Number.isInteger(n) || n < 1) throw new Error(`keep: count must be a positive integer, got ${count}`)
    const { members } = knowledge.resolve(String(item))
    return { item: String(item), count: n, members, food: members.every((m) => food.has(m)) }
  })
}

function validGoal (spec, bot, state, knowledge) {
  const { predicate } = spec
  const needHome = () => { if (!state.home) throw new NotYetError('there is no home yet (build the house first)') }
  switch (predicate) {
    case 'have': {
      const count = Number(spec.count)
      if (!Number.isInteger(count) || count < 1 || count > MAX_COUNT) throw new Error(`count must be 1-${MAX_COUNT}`)
      knowledge.resolve(String(spec.item))
      return { spec: { predicate, item: String(spec.item), count } }
    }
    case 'stored': {
      const count = Number(spec.count)
      if (!Number.isInteger(count) || count < 1 || count > MAX_COUNT) throw new Error(`count must be 1-${MAX_COUNT}`)
      knowledge.resolve(String(spec.item))
      needHome() // チェストは家の中に置く
      return { spec: { predicate, item: String(spec.item), count } }
    }
    case 'built':
      if (spec.name) {
        // 名前付きの建物（docs/design/25_builds.md）。設計は Python が先に登録する
        // まだない名前は「まだ」: /check（街の段階の確認）では未達、目標にはできない（PUT /goal は 400）
        if (!state.builds?.[spec.name]) throw new NotYetError(`there is no build named ${spec.name} yet: it is designed when the objective is added`)
        return { spec: { predicate, name: String(spec.name) } }
      }
      if (!state.plan) throw new NotYetError('there is no house plan')
      return { spec: { predicate } }
    case 'placed':
      // 家の中の家具（furniture.mjs の HOME_FURNITURE: ベッド、作業台、かまど、チェスト）は家具の扱い。
      // ほかは好きな物を好きな場所に（placing.mjs: home / near_home / build:NAME / x,y,z と数）
      if (isHomeFurnitureItem(spec.item) && (spec.where ?? 'home') === 'home' && (spec.count ?? 1) === 1) {
        if (spec.item.endsWith('_bed') && !bot.registry.itemsByName[spec.item]) throw new Error(`there is no item ${spec.item}`)
        needHome()
        return { spec: { predicate, item: spec.item, where: 'home' } }
      }
      // ベッドは家の中だけ（家具）。「bed」はどの色のベッドでもという意味で、置ける物の名前ではない（ほかの場所は色つきの名前）
      if (spec.item === 'bed') throw new Error('a bed is placed in the home only: write placed(bed, home) (the house comes first: built())')
      return { spec: checkPlacedSpec(bot, state, spec) }
    case 'at_home':
      needHome()
      return { spec: { predicate } }
    case 'through_night':
      // 家がなくても夜は越せる（近くのベッド、持っているベッド、地下）
      return { spec: { predicate }, sawNight: isDusk(dayPhase(bot.time.timeOfDay)) }
    case 'explored': {
      const distance = Number(spec.distance)
      if (!Number.isInteger(distance) || distance < MIN_EXPLORE || distance > MAX_EXPLORE) {
        throw new Error(`distance must be ${MIN_EXPLORE}-${MAX_EXPLORE}`)
      }
      const p = bot.entity.position
      return { spec: { predicate, distance }, start: { x: p.x, y: p.y, z: p.z } }
    }
    case 'found': {
      const item = String(spec.item ?? '').trim().toLowerCase()
      const distance = Number(spec.distance)
      if (!Number.isInteger(distance) || distance < MIN_EXPLORE || distance > MAX_FOUND) {
        throw new Error(`distance must be ${MIN_EXPLORE}-${MAX_FOUND}`)
      }
      if (!foundKind(bot, item)) throw new Error(`found: ${item || 'no item'} is neither a mob nor a biome name (e.g. sheep, plains)`)
      const p = bot.entity.position
      return { spec: { predicate, item, distance }, start: { x: p.x, y: p.y, z: p.z } }
    }
    case 'surveyed': {
      needHome() // 候補地は家（最初の夜を越す小屋）を中心に決める
      const count = Number(spec.count)
      const planned = ensureSurvey(state, bot).sites.length
      if (!Number.isInteger(count) || count < 1 || count > planned) throw new Error(`count must be 1-${planned} (the candidate sites)`)
      return { spec: { predicate, count } }
    }
    case 'lit': {
      needHome()
      const distance = Number(spec.distance)
      if (!Number.isInteger(distance) || distance < MIN_LIT || distance > MAX_LIT) throw new Error(`distance (the radius) must be ${MIN_LIT}-${MAX_LIT}`)
      return { spec: { predicate, distance } }
    }
    case 'paved': {
      if (!state.roads?.strips?.length) throw new NotYetError('the town has no planned roads yet (the roads come with the town map)')
      return { spec: { predicate } }
    }
    case 'leveled': {
      const where = String(spec.where ?? 'home')
      if (where === 'home') needHome()
      levelTarget(state, where)
      const distance = Number(spec.distance)
      if (!Number.isInteger(distance) || distance < MIN_LEVEL || distance > MAX_LEVEL) throw new Error(`distance (the radius) must be ${MIN_LEVEL}-${MAX_LEVEL}`)
      return { spec: { predicate, where, distance, ...(spec.demolish ? { demolish: true } : {}) } }
    }
    case 'custom': {
      // 判定は Python が先に登録する（PUT /conditions/<name>）。まだない名前は「まだ」
      const name = String(spec.name ?? '')
      if (!state.conditions?.[name]) throw new NotYetError(`there is no check named ${name} yet: it is written when the objective is added`)
      return { spec: { predicate, name } }
    }
    case 'facility': {
      // 設計した施設（設計書 47 §7）。設計は Python が先に登録する。まだない名前は「まだ」
      const name = String(spec.name ?? '')
      const fac = state.facilities?.[name]
      if (!fac) throw new NotYetError(`there is no facility named ${name} yet: it is designed when the objective is added`)
      if (!fac.layout) throw new Error(`${name} was not designed (it was already there): facility() is for designed farms and tree farms`)
      return { spec: { predicate, name, ...(spec.started ? { started: true } : {}) } }
    }
    case 'planted': {
      needHome()
      const item = String(spec.item ?? 'sapling')
      if (item !== 'sapling' && !(isSaplingItem(item) && bot.registry.itemsByName[item])) throw new Error('planted takes sapling (any kind) or one kind like oak_sapling')
      const count = Number(spec.count)
      if (!Number.isInteger(count) || count < 1 || count > MAX_PLANTED) throw new Error(`count must be 1-${MAX_PLANTED}`)
      return { spec: { predicate, item, count } }
    }
    case 'farmed': {
      needHome()
      const crop = cropOf(spec.item ?? 'wheat')
      if (!crop) throw new Error(`farmed takes one of ${Object.keys(CROPS).join(', ')}`)
      const count = Number(spec.count)
      if (!Number.isInteger(count) || count < 1 || count > MAX_FARMED) throw new Error(`count must be 1-${MAX_FARMED}`)
      return { spec: { predicate, item: crop, count } }
    }
    case 'cleared': {
      needHome()
      // 夜も出て倒してよい（敵がいて眠れないとき。2026-09-27: 夜の行動が制約されすぎ）。素手でも、危なさ
      // （risk.mjs）が極めて高いとき以外は止めない。暗いうちは次々に湧くので、持ち物は先に家のチェストに預ける
      // （evaluate の stash）
      return { spec: { predicate } }
    }
    default:
      throw new Error(`unknown predicate ${predicate}; use one of ${PREDICATES.join(', ')}`)
  }
}

// いまの目標についての { spec, met, remaining, lines, blocked, impossible, leaves }
// 家の計画のうち、置けていないドア（向きが違うものを含む）
// 家の計画が今の家のもの（ドアの位置が同じ）でなければ見ない: 見つけた拠点や引っ越した家に、
// 別の場所の建てかけの計画のドアを直しに行かせない
const MAX_DOOR_FAILURES = 3
function brokenDoor (bot, state) {
  if (!state.home || !state.plan?.origin) return null
  const door = state.plan.pending(bot).find((b) => b.block === 'door')
  if (!door) return null
  const pos = state.plan.worldPos(door)
  const home = state.home.door
  if (!home || Math.abs(pos.x - home.x) + Math.abs(pos.z - home.z) > 0 || Math.abs(pos.y - home.y) > 1) return null
  // 足元がない（爆発で地面ごと飛んだ）ドアは、先に足元を埋める（built）。ドアだけを置きに行っても、床がなくて置けず、
  // 3 回の失敗で放棄された（2026-10-06）
  if (bot.blockAt(pos.offset(0, -1, 0))?.boundingBox === 'empty') return null
  return door
}

// いまの目標（束なら条件ごとに評価してまとめる: 済んだかは全部、残りは合計、候補は束全体の葉から。
// 葉には、どの条件のためかを for に付ける）
export function evaluate (bot, state, knowledge, world) {
  const goal = state.goal
  if (!goal.together?.length) return evaluateOne(bot, state, knowledge, world)
  const parts = [goal, ...goal.together].map((g) => ({ g, r: evaluateOne(bot, { ...state, goal: g }, knowledge, world) }))
  const out = { spec: goal.spec, met: parts.every(({ r }) => r.met), remaining: 0, lines: [], blocked: [], impossible: [], leaves: [], members: [] }
  parts.forEach(({ g, r }, i) => {
    const label = describeSpec(g.spec)
    out.members.push({ spec: g.spec, met: r.met, remaining: r.remaining, lines: r.lines })
    out.remaining += r.remaining
    out.lines.push(`[${i + 1}/${parts.length}] ${label}: ${r.met ? 'done' : `${r.remaining} left`}`, ...r.lines.map((l) => `  ${l}`))
    if (r.met) return
    out.blocked.push(...r.blocked)
    out.impossible.push(...r.impossible)
    out.leaves.push(...r.leaves.map((l) => l.for ? l : { ...l, for: label }))
  })
  out.lines.push(...(goal.togetherSkipped ?? []))
  return out
}

// 畑の柵とゲートの行（設計書 75）: 畑のマスと整地が済むまでは置かない。数だけを書くと、LLM は先に板を集める中目標や
// placed(oak_fence) の中目標を書く（2026-10-04: 28/56 マスのまま板 35 枚と柵を先にそろえようとして 63 手進まなかった）
// 畑の整地・修理の一歩（設計書 75、115）: 先に高さをそろえる（そろえる前に耕すと、整地で掘り返される）。道具がないと掘れない
// 石は道具を作り、穴は土を持ってから埋める（畑は耕せる土だけで埋める。土は街の外から）
function farmLevelStep (bot, fac, lv, out, addSolved) {
  const missing = toolsMissing(bot, lv.cuts)
  for (const [tool, n] of Object.entries(missing)) {
    out.lines.push(`${n} blocks to dig need a ${tool} (or better)`)
    addSolved([{ spec: tool, count: 1 }])
  }
  const diggable = lv.cuts.length - Object.values(missing).reduce((a, b) => a + b, 0)
  const need = fillNeeded(lv)
  const soil = soilCount(bot)
  if (diggable || (lv.fills.length && soil >= need)) out.leaves.push({ kind: 'level', facility: fac.name, pos: (lv.cuts[0] ?? lv.fills[0]), off: lv.off })
  if (!diggable && lv.fills.length && soil < need) {
    out.lines.push(`${need} blocks to fill need ${need} dirt (carrying ${soil}): gather it outside the town (not from the farms or around the home) first`)
    addSolved([{ spec: 'dirt', count: need }])
  }
}

function fenceNote (f, lv) {
  if (!f.fence.length && !f.gate) return ''
  const what = [f.fence.length ? `${f.fence.length} fence` : '', f.gate ? 'a fence gate' : ''].filter(Boolean).join(' and ')
  if (f.done === f.total && !lv?.off) return `, ${what} to place now`
  return `, ${what} come last: the facility places them itself once the ground is level and every cell is done (no planks or fences before then)`
}

function evaluateOne (bot, state, knowledge, world) {
  const goal = state.goal
  const out = { spec: goal.spec, met: false, remaining: 0, lines: [], blocked: [], impossible: [], leaves: [] }
  const addSolved = (needs, w = world) => {
    const r = solve(knowledge, w, needs)
    out.lines.push(...r.lines)
    out.blocked.push(...r.blocked)
    out.impossible.push(...r.impossible)
    // 近くにないものを探す: 目標を立てた場所から離れる方向に（以前は出発点のまわりを行ったり
    // 来たりして、20m 先の食料を見つけられなかった）
    out.leaves.push(...r.leaves.map((l) => l.kind === 'explore' && !l.away && goal.exploreFrom ? { ...l, away: goal.exploreFrom } : l))
    out.remaining += r.remaining
    return r
  }
  const phase = dayPhase(bot.time.timeOfDay)
  const inside = isInside(bot, state.home)
  // 向きの違うドア（開けても板が通り道をふさぐ）は、家に入る目標の前に置き直す。今のドアは壊して使う
  if (['at_home', 'through_night', 'placed'].includes(goal.spec.predicate) && !inside) {
    const door = brokenDoor(bot, state)
    if (door && (state.doorFailures ?? 0) >= MAX_DOOR_FAILURES) {
      // 何度やっても置き直せない: ほかの行動を止めない（ドアはそのままで入れることが多い）
      out.blocked.push(`replacing the door failed ${state.doorFailures} times: left as it is`)
    } else if (door) {
      out.blocked.push('the door of the house is turned the wrong way (it blocks the doorway when open): replace it')
      const pos = state.plan.worldPos(door)
      const reusable = [pos, pos.offset(0, -1, 0)].some((p) => bot.blockAt(p)?.name.endsWith('_door'))
      if (reusable || Object.keys(inventoryCounts(bot)).some((n) => n.endsWith('_door'))) out.leaves.push({ kind: 'place_plan', block: door })
      else addSolved([{ spec: 'door', count: 1 }])
      out.remaining += 1
      return out
    }
  }
  switch (goal.spec.predicate) {
    case 'have': {
      // 家具（ベッド、作業台、かまど、チェスト）は、近くに置いてあるものを拾えば足りる: 作らずに拾う
      // （材料集めからやり直さない）。1 つ持てば足りるときだけ
      const furniture = goal.spec.count <= 1 ? takeableItem(goal.spec.item) : null
      const held = furniture && (furniture === 'bed'
        ? Object.keys(inventoryCounts(bot)).some((n) => n.endsWith('_bed'))
        : (inventoryCounts(bot)[furniture] ?? 0) > 0)
      const taken = furniture && !held ? placedToTake(bot, state, furniture) : null
      if (taken) {
        out.lines.push(`have ${goal.spec.count} ${goal.spec.item}: a placed ${taken.block} at ${taken.pos.x},${taken.pos.y},${taken.pos.z} can be taken instead of made`)
        out.leaves.push({ kind: 'take_placed', ...taken })
        out.remaining += goal.spec.count
        break
      }
      const r = addSolved([{ spec: goal.spec.item, count: goal.spec.count }])
      out.met = r.met
      // 仕事の次の一歩（小目標にする。設計書 50 §13）
      if (!r.met) out.next = nextStep(r.nodes[0], world, knowledge)
      out.gather = isGather(r.nodes[0])
      break
    }
    case 'built': {
      const name = goal.spec.name
      const plan = name ? state.builds[name] : state.plan
      let status = plan.status(bot)
      // 足元の地面が直せない家（深い穴、溶岩、埋めるより建て直したほうが安い）は、敷地を選び直して建て直す
      // （建て直しが済めば今の家は前の家になる: settleHome）
      if (!name && status.ruined) {
        const why = status.ruined
        console.log(`[build] 家を別の場所に建て直す: ${why}`)
        plan.relocate()
        status = plan.status(bot)
        out.lines.push(`the old site is lost (${why}): the house is rebuilt on a new site`)
      }
      out.met = status.complete
      if (out.met) break
      // 原木のブロックのぶんの原木を先に取り、それが板材にされないようにする
      const need = plan.materialsNeeded(bot)
      addSolved(['log', 'door', 'planks', 'cobblestone', 'dirt'].filter((k) => need[k]).map((k) => ({ spec: k, count: need[k] })))
      out.lines.unshift(`${name ? `blocks of the build ${name}` : 'house blocks'} placed ${status.placed}/${status.total}`)
      if (status.foundation) out.lines.push(`the ground under the house has fallen away: ${status.foundation} blocks of dirt to fill first (bottom up)`)
      out.remaining += status.total - status.placed + status.foundation
      const next = plan.pending(bot)[0]
      const held = next && (next.block === 'air' || Object.keys(inventoryCounts(bot)).some((n) => knowledge.isMember(next.block, n)))
      if (name) {
        if (held) out.leaves.push({ kind: 'place_plan', block: next, build: name })
        break
      }
      if (!plan.origin && !plan.canSearchSite(bot)) {
        out.blocked.push('no flat site for the house near here')
        out.leaves.push({ kind: 'explore', item: 'a flat site', sources: [] })
      } else if (held) {
        out.leaves.push({ kind: 'place_plan', block: next })
      }
      break
    }
    case 'placed': {
      if (!isHomeFurnitureItem(goal.spec.item) || goal.spec.where !== 'home' || (goal.spec.count ?? 1) !== 1) {
        // 好きな物を好きな場所に（placing.mjs）: 足りなければ、持っていれば置く、なければ作る・集める
        const { item, where, count } = goal.spec
        const have = countPlaced(bot, state, where, item)
        const need = Math.max(0, count - have)
        out.met = need === 0
        out.remaining = need
        out.lines.push(`${item} ${describeWhere(where, state)}: ${have}/${count}`)
        if (out.met) break
        const held = inventoryCounts(bot)[item] ?? 0
        // 家の中に置く家具（チェスト）は、家の外に置いてしまった自分の物を拾って運ぶ。作らない（設計書 117）
        const outside = held < need && where === 'home' && isHomeFurnitureItem(item) ? placedToTake(bot, state, item) : null
        if (outside) out.leaves.push({ kind: 'take_placed', ...outside })
        else if (held < need) addSolved([{ spec: item, count: need - held }])
        if (held) {
          const [spot] = placeSpots(bot, state, where, 1, item)
          if (spot) out.leaves.push({ kind: 'place_at', item, where, pos: spot.pos, against: spot.against })
          else out.blocked.push(`no free spot to place ${item} ${describeWhere(where, state)}`)
        }
        break
      }
      if (goal.spec.item.endsWith('_bed')) {
        // 色つきのベッド（blue_bed）: その色のベッドが家にあること。部屋が前のベッドでふさがって
        // いれば、前のベッドを拾う候補も出す（拾ったベッドは白なら染められる）
        const item = goal.spec.item
        const at = ownFurnitureInHome(bot, state, item)
        out.met = !!at
        out.lines.push(`a ${item} in the house: ${at ? 'yes' : 'no'}`)
        if (out.met) break
        const held = (inventoryCounts(bot)[item] ?? 0) > 0
        const old = ownFurnitureInHome(bot, state, 'bed')
        if (!held) addSolved([{ spec: item, count: 1 }])
        else if (bedSpot(bot, state.home)) out.leaves.push({ kind: 'place_bed', item })
        else if (!old) out.blocked.push(`no free spot for the ${item} in the house`)
        if (old && (held || !bedSpot(bot, state.home))) out.leaves.push({ kind: 'take_placed', pos: old, block: bot.blockAt(old)?.name })
        out.remaining += 1
        break
      }
      if (goal.spec.item !== 'bed') {
        // 作業台・かまど・チェスト・松明を家の中に置く
        const item = goal.spec.item
        const at = ownFurnitureInHome(bot, state, item)
        out.met = !!at
        out.lines.push(`a ${item} in the house: ${at ? `yes (${at.x},${at.y},${at.z})` : 'no'}`)
        if (out.met) break
        const held = (inventoryCounts(bot)[item] ?? 0) > 0
        // 家の外に置いてある同じ物（作業台、かまど、チェスト）は、拾って運べる。作らない
        const placed = held ? null : placedToTake(bot, state, item)
        if (placed) out.leaves.push({ kind: 'take_placed', ...placed })
        else if (!held) addSolved([{ spec: item, count: 1 }])
        else out.leaves.push({ kind: 'place_in_home', item })
        out.remaining += 1
        break
      }
      // 見つけた拠点のベッドは村人の物: 自分で置いた物だけ数える
      out.met = state.home?.found ? !!ownFurnitureInHome(bot, state, 'bed') : hasBed(bot, state.home)
      out.lines.push(`a bed in the house: ${out.met ? 'yes' : 'no'}${state.home?.found && !out.met && hasBed(bot, state.home) ? ' (only the bed that was there already: it is not yours)' : ''}`)
      if (out.met) break
      const bed = Object.keys(inventoryCounts(bot)).some((n) => n.endsWith('_bed'))
      // 近くに置いてあるベッド（家の外）は、拾って運べる（作るのと並べて出す: どちらかは選ぶ側）
      const placedBed = bed ? null : placedToTake(bot, state, 'bed')
      if (placedBed) out.leaves.push({ kind: 'take_placed', ...placedBed })
      // 拾えるベッドが近くにあれば作らない（材料集めからやり直していた）。拾えなければ（断られた・遠い）作る
      if (!bed) { if (!placedBed) addSolved([{ spec: 'bed', count: 1 }]) } else if (bedSpot(bot, state.home)) out.leaves.push({ kind: 'place_bed' })
      else out.blocked.push('no free spot for the bed in the house')
      out.remaining += 1
      break
    }
    case 'at_home':
      out.met = inside && !isDoorOpen(bot, state.home) && !state.home.breach.length
      if (!out.met) {
        const blocked = homeEntryBlocked(bot, state.home)
        if (blocked) out.blocked.push(blocked)
        else out.leaves.push({ kind: 'go_home' })
        out.remaining = 1
      }
      out.lines.push(`inside the house with the door closed: ${out.met ? 'yes' : 'no'}`)
      break
    case 'through_night': {
      if (isDusk(phase)) goal.sawNight = true
      // 明け方（23000〜24000）も夜が明けた: 日が昇るのを待たせて、朝なのに「夜を越す」が続かないように
      out.met = goal.sawNight && (phase === 'day' || phase === 'dawn')
      out.lines.push(`the night has passed: ${out.met ? 'yes' : `no (${phase})`}`)
      if (out.met) break
      // 昼（夜を一度も見ていない）に夜の目標を持っても、夜の過ごし方は出さない（2026-10-04: 昼に through_night を
      // 選び、畑の中へ縦穴を掘って潜った）。夕方から始まる
      if (phase === 'day' && !goal.sawNight) {
        out.lines.push('it is day: through_night starts at dusk')
        out.blocked.push('it is daytime: there is no night to get through yet; choose the goal for the day')
        break
      }
      // 朝までの分数: 待つことが進み具合に表れるので、停滞とみなされない
      const tod = bot.time.timeOfDay
      out.remaining = Math.max(1, Math.ceil(((MORNING - tod + DAY_TICKS) % DAY_TICKS) / TICKS_PER_MINUTE))
      const sleepy = tod >= SLEEP_FROM && tod <= SLEEP_UNTIL && !bot.isSleeping
      if (inside) {
        if (hasBed(bot, state.home) && sleepy) out.leaves.push({ kind: 'sleep' })
        // 持っているベッドが家にまだ置かれていないなら、置く（2026-10-10 21:36: ベッドを持って家の中にいたのに、
        // 朝まで待つ手しか出ず、「ベッドを置いて」と頼まれても置けなかった）。置けば、次の手で寝られる
        else if (!hasBed(bot, state.home) && Object.keys(inventoryCounts(bot)).some((n) => n.endsWith('_bed')) && bedSpot(bot, state.home)) {
          out.leaves.push({ kind: 'place_bed' })
        }
        // 敵がそばにいると眠れない: 武器があれば、出て倒す（cleared。持ち物は先にチェストへ）のも選べる
        const near = dangerOutside(bot, state.home)
        if (sleepy && near.length) {
          out.lines.push(`hostiles near the house (${near.map(({ e }) => e.name).join(', ')}): you cannot sleep while they are close` +
            `; you could go out and fight them (cleared: valuable items go into the chest first; ${describeRisk(assessRisk(bot, state))})`)
        }
        break
      }
      // 家に帰るのは選択肢の 1 つ（2026-09-26: 絶対の決まりではない）。近くのベッド（村など）で寝る、
      // 持っているベッドをここに置いて寝る、地下ならそのまま（地下は夜も昼も同じ）
      // さっき行けなかった家とベッドはしばらく出さない（ほかの越し方を選ばせる: 2026-09-27 一晩中「No path」）
      const homeCut = state.home && isNoPathTo(state, state.home.outside)
      // 穴の中（掘って塞いだ）なら、朝までそのまま。ほかの越し方は出さない（出ると穴を捨てることになる）
      if (inBurrow(bot)) {
        out.leaves.push({ kind: 'stay_burrowed' })
        break
      }
      const entryBlocked = state.home && homeEntryBlocked(bot, state.home)
      if (entryBlocked) out.blocked.push(entryBlocked)
      if (state.home && !homeCut && !entryBlocked) out.leaves.push({ kind: 'go_home' })
      if (homeCut) out.blocked.push('home: no path to its door a moment ago (see the last result); try another way through the night')
      if (sleepy) {
        const bed = nearbyBed(bot, state)
        if (bed) out.leaves.push({ kind: 'sleep_at', pos: bed })
        // 近くのベッドがあっても、持っているベッドを置いて寝るのは別の選択肢
        if (Object.keys(inventoryCounts(bot)).some((n) => n.endsWith('_bed'))) out.leaves.push({ kind: 'bed_here' })
      }
      if (isUnderground(bot)) out.leaves.push({ kind: 'stay_underground' })
      // 家がない・行けない・遠い夜は、足元に穴を掘って塞ぐ（docs/design/61）。ベッドで寝るのとは別の選択肢
      const homeFar = state.home && bot.entity.position.distanceTo(state.home.outside) > BURROW_HOME_DISTANCE
      if (!state.home || homeCut || homeFar || entryBlocked) {
        const { spot, why } = burrowSpotCached(bot, state)
        if (spot) out.leaves.push({ kind: 'dig_in', pos: spot.feet })
        else out.blocked.push(`digging in for the night: ${why}`)
        // 縦穴が掘れない（砂浜・石・水のそば）夜は、立った所のまわりを土などで囲んで朝を待つ（docs/design/89）。
        // 水の中では囲めない: 先に陸へ（reach_land）
        if (!spot && !isSwimming(bot)) {
          const { site, why: campWhy } = campSiteCached(bot, state)
          const short = site && campMaterials(bot, state, site)
          if (site && !short) out.leaves.push({ kind: 'wall_in', pos: site.feet, need: site.need })
          else out.blocked.push(`making a camp: ${short ?? campWhy}`)
        }
        // 剣も斧もなければ、先に作る（夜の戦いと見張りのため。素手で木を殴って集めるところから）。
        // 泳いでいる間は集める木がない: 先に陸へ
        if (!armed(bot) && !isSwimming(bot)) {
          // 夜の暗がりに木を探して歩かない（explore は出さない）: 見えている木を殴る・作れる物を作る手だけ
          const r = solve(knowledge, world, [{ spec: 'sword', count: 1 }])
          // 材料のどれかが近くに見えていなければ（blocked）、一部だけを出さない（棒だけ作れても剣にならない）
          const leaves = r.blocked.length || r.impossible.length ? [] : r.leaves.filter((l) => l.kind !== 'explore')
          if (leaves.length) {
            out.leaves.push(...leaves.map((l) => ({ ...l, also: 'a weapon to fight with through the night' })))
            out.lines.push('no sword or axe: make one (bare hands can punch the trees you can see) to fight what comes in the dark')
          }
        }
      }
      break
    }
    case 'explored': {
      const start = new Vec3(goal.start.x, goal.start.y, goal.start.z)
      const d = bot.entity.position.xzDistanceTo(start)
      out.met = d >= goal.spec.distance
      out.lines.push(`explored ${Math.floor(d)}/${goal.spec.distance}m`)
      if (!out.met) {
        out.remaining = Math.ceil((goal.spec.distance - d) / EXPLORE_STEP)
        out.leaves.push({ kind: 'explore', item: 'new places', sources: [], away: start, reach: Math.ceil(goal.spec.distance - d) })
      }
      break
    }
    case 'found': {
      const target = goal.spec.item
      const hit = foundNow(bot, target)
      if (hit) {
        out.met = true
        out.lines.push(`found: ${hit.where}`)
        break
      }
      const start = new Vec3(goal.start.x, goal.start.y, goal.start.z)
      const d = bot.entity.position.xzDistanceTo(start)
      out.lines.push(`${target} not found yet (${Math.floor(d)}/${goal.spec.distance}m searched from where it began)`)
      if (d >= goal.spec.distance) {
        const seen = [...new Set(Object.values(state.memory?.biomes ?? {}))]
        out.impossible.push(`searched ${Math.floor(d)}m from where it began and found no ${target}${seen.length ? ` (biomes walked through: ${seen.join(', ')})` : ''}`)
        break
      }
      out.remaining = Math.ceil((goal.spec.distance - d) / EXPLORE_STEP)
      out.leaves.push({ kind: 'explore', item: target, sources: [target], away: start, reach: Math.ceil(goal.spec.distance - d) })
      break
    }
    case 'surveyed': {
      const survey = ensureSurvey(state, bot)
      const done = surveyedSites(state).length
      const n = goal.spec.count
      out.met = done >= n
      out.lines.push(`candidate sites surveyed ${Math.min(done, n)}/${n}`)
      if (out.met) break
      // 一番近い未調査の候補地へ。残りは候補地の数と、そこまでのレッグの数（歩くたびに減る）
      const me = bot.entity.position
      const next = survey.sites.filter((s) => !state.memory?.sites?.[s.id])
        .map((s) => ({ ...s, distance: Math.hypot(s.x - me.x, s.z - me.z) }))
        .sort((a, b) => a.distance - b.distance)[0]
      out.remaining = (n - done) * 10 + Math.ceil(next.distance / LEG)
      out.lines.push(`next: the ${next.id} site at ${next.x},${next.z} (${Math.round(next.distance)}m)`)
      out.leaves.push({ kind: 'survey', site: next })
      break
    }
    case 'stored': {
      const { members } = knowledge.resolve(goal.spec.item)
      const stored = storedCounts(state.memory ?? {}, state.home)
      const n = goal.spec.count
      const inChests = members.reduce((s, m) => s + (stored[m] ?? 0), 0)
      out.met = inChests >= n
      out.lines.push(`${goal.spec.item} in the chests (${Math.min(inChests, n)}/${n})`)
      if (out.met) break
      const want = n - inChests
      out.remaining += want
      // 家のチェストの中身を取り出してまた入れることはしない。ほかのチェスト（引っ越す前の家）
      // からは取り出して運んでよい
      // アイテムはチェストがあってもなくても集めるので、残りの作業量はチェストを置いて中身を
      // 入れるにつれて減るだけ
      const elsewhere = Object.fromEntries(Object.entries(world.stored ?? {})
        .map(([m, c]) => [m, Math.max(0, c - (stored[m] ?? 0))]).filter(([, c]) => c > 0))
      const gathering = { ...world, stored: elsewhere }
      if (!homeChests(state.memory ?? {}, state.home).length) {
        if (addSolved([{ spec: 'chest', count: 1 }], gathering).met) out.leaves.push({ kind: 'place_chest' })
        out.remaining += 1
        addSolved([{ spec: goal.spec.item, count: want }], gathering)
        break
      }
      const r = addSolved([{ spec: goal.spec.item, count: want }], gathering)
      const inv = inventoryCounts(bot)
      // いま焼ける生肉は焼いてから入れる（焼く候補、次にかまど）
      const cook = cooking(bot, knowledge)
      const held = members.filter((m) => inv[m] > 0 && m !== cook?.input).map((m) => ({ item: m, count: Math.min(inv[m], want) }))
      const carrying = held.reduce((s, h) => s + h.count, 0)
      // 1 個ずつ家に運ばない: ある程度たまったら（足りる数、DEPOSIT_BATCH、持ち物がいっぱい）、
      // ここで集めるものがなくなったら、家の近くにいるなら、夕方・夜なら（どうせ帰る）入れに行く
      const nearHome = bot.entity.position.distanceTo(state.home.inside) <= NEAR_HOME
      const nothingHere = !r.leaves.some((l) => l.kind !== 'explore')
      const full = (bot.inventory.emptySlotCount?.() ?? 36) <= FULL_SLOTS
      if (held.length && (carrying >= want || carrying >= DEPOSIT_BATCH || full || nearHome || nothingHere || (phase !== 'day' && !isUnderground(bot)))) {
        out.leaves.push({ kind: 'deposit', items: held })
      } else if (held.length) {
        out.lines.push(`carrying ${carrying}: take them to the chest at ${DEPOSIT_BATCH}, when nothing is left to gather here, or at dusk`)
      }
      break
    }
    case 'lit': {
      const { dark, unloaded } = darkGround(bot, state, goal.spec.distance)
      out.met = dark.length === 0 && unloaded === 0
      out.remaining = dark.length + unloaded
      if (unloaded) {
        out.lines.push(`the ground within ${goal.spec.distance} of the home: out of view`)
        out.blocked.push('the home is too far to see its grounds; go back to it')
        out.leaves.push({ kind: 'go_home' })
        break
      }
      out.lines.push(`dark ground within ${goal.spec.distance} of the home: ${dark.length ? `${dark.length} spots` : 'none'}`)
      if (out.met) break
      const torches = inventoryCounts(bot).torch ?? 0
      if (torches) out.leaves.push({ kind: 'light', pos: dark[0] })
      else addSolved([{ spec: 'torch', count: 4 }])
      break
    }
    case 'paved': {
      // 計画した道がすべて土の道になっている（設計書 200）。敷く手（pave）は candidates の paveCandidate と同じ
      const s = roadStatus(bot, state, { fresh: true })
      if (!s) {
        out.blocked.push('the town has no planned roads')
        break
      }
      out.met = s.todo.length === 0 && s.unseen === 0 && s.total > 0
      out.remaining = s.todo.length + s.unseen
      out.lines.push(`planned road cells: ${s.done} of ${s.total} laid${s.unseen ? `, ${s.unseen} out of view` : ''}`)
      if (out.met) break
      if (!bot.inventory.items().some((i) => /_shovel$/.test(i.name))) addSolved([{ spec: 'stone_shovel', count: 1 }])
      break
    }
    case 'leveled': {
      // 整地（設計書 41）: そろっていない列の数が残り。出っ張りを掘る（土が出る）、穴を埋める。
      // 1 回の行動（level）で届く範囲を次々にそろえる（1 マスずつでは歩数の予算に収まらない）
      const { where, distance } = goal.spec
      const demolish = goal.spec.demolish === true
      const s = levelStatus(bot, state, where, distance, { demolish })
      out.met = s.off === 0 && s.unloaded === 0
      out.remaining = s.off + s.unloaded
      out.lines.push(describeLevel(s, distance))
      if (demolish) out.lines.push('demolish: placed things, the home / builds (even protected), trees and farms in the area are broken to level it; water, mines and unbreakable blocks are left')
      else {
        const kept = keptByNotDemolishing(s.skipped)
        if (kept) out.lines.push(`${kept} columns are left as they are because they hold something placed, a home or build, a tree or a farm: leveled(${where}, ${distance}, demolish) breaks them too (a deliberate choice: it takes down what stands there)`)
      }
      if (s.unloaded) {
        out.blocked.push('part of the ground is out of view; go closer to it')
        if (where === 'home') out.leaves.push({ kind: 'go_home' })
      }
      if (out.met || !s.off) break
      // 石などは道具がないと掘れない（掘っても何も落ちない）: 足りない道具を作る
      const missing = toolsMissing(bot, s.cuts)
      const diggable = s.cuts.length - Object.values(missing).reduce((a, b) => a + b, 0)
      for (const [tool, n] of Object.entries(missing)) {
        out.lines.push(`${n} blocks to dig need a ${tool} (or better)`)
        addSolved([{ spec: tool, count: 1 }])
      }
      // 埋めるだけなら、穴の数だけ土を持ってから（少しずつ掘っては埋める往復をしない）。土は整地の外から取る
      const need = fillNeeded(s)
      const enough = fillCount(bot) >= need
      if (diggable || (s.fills.length && enough)) out.leaves.push({ kind: 'level', where, distance, demolish, pos: (s.cuts[0] ?? s.fills[0]), off: s.off })
      if (!diggable && s.fills.length && !enough) {
        out.lines.push(`${need} blocks to fill need ${need} dirt (carrying ${fillCount(bot)}): gather it outside the leveled area first`)
        addSolved([{ spec: 'dirt', count: need }])
      }
      break
    }
    case 'custom': {
      // Gemini が書いた判定（設計書 42）。候補は出さない（何をするかは判定からはわからない）:
      // 道具モードで Gemini が道具を選んで進める。判定が壊れたら、その理由を blocked に
      const def = state.conditions?.[goal.spec.name]
      try {
        if (!def) throw new Error(`the check ${goal.spec.name} is gone`)
        const r = runCheck(bot, state, { name: goal.spec.name, code: def.code })
        out.met = r.met
        out.remaining = r.remaining
        out.lines.push(`${def.description}: ${r.met ? 'done' : `${r.remaining} left`}`, ...r.lines)
        if (!r.met) out.blocked.push('no suggestion for a custom goal: choose the tools that make it true')
      } catch (e) {
        out.remaining = 1
        out.lines.push(`${def?.description ?? goal.spec.name}: the check failed`)
        out.blocked.push(`the check failed: ${e.message}`)
      }
      break
    }
    case 'facility': {
      // 設計した施設のマス（設計書 47 §7）: 畑は耕してまく、植林場は苗木を植える、採掘場は掘る（§16）
      const fac = state.facilities[goal.spec.name]
      if (fac.kind === 'mine') {
        const m = mineStatus(bot, fac)
        updateStatus(bot, fac)
        out.met = m.done === m.total
        out.remaining = m.total - m.done
        const bottom = fac.layout.cells.filter((c) => c.part === 'stairs').at(-1)?.y
        const tunnels = fac.layout.tunnels?.length ? `, tunnels ${fac.layout.tunnels.join('/')}` : ''
        const stopped = (fac.layout.stopped ? `; stopped at y ${fac.layout.stopped.y}: ${fac.layout.stopped.reason}` : '') +
          (fac.layout.paused ? `; waiting at y ${fac.layout.paused.y} for cobblestone: ${fac.layout.paused.reason}` : '')
        out.lines.push(`mine ${fac.name} (stairs down to y ${bottom}${tunnels}): ${m.done}/${m.total} cells dug${m.unseen ? `, out of view ${m.unseen}` : ''}${stopped}`)
        if (out.met) break
        if (!m.next) { out.leaves.push({ kind: 'go_facility', name: fac.name, pos: fac.entrance }); break }
        const inv = inventoryCounts(bot)
        if (!Object.keys(inv).some((n) => n.endsWith('_pickaxe') && inv[n] > 0)) { addSolved([{ spec: 'wooden_pickaxe', count: 1 }]); break }
        // 残りを掘るぶんの松明を手に持ってから掘る（チェストにあれば出す。足りない数が半分未満なら先に揃える）
        const wanted = mineTorchesWanted(m.total - m.done)
        if ((inv.torch ?? 0) < enoughToGo(wanted)) {
          addSolved([{ spec: 'torch', count: wanted }])
          out.lines.push(`torches for the mine: carry ${wanted} before going down (you carry ${inv.torch ?? 0})`)
          break
        }
        out.leaves.push({ kind: 'dig_mine', facility: fac.name })
        break
      }
      ensureFarmShape(bot, state, fac) // 古い畑に長方形と高さを補う（段差を埋められるように）
      const f = layoutStatus(bot, fac)
      updateStatus(bot, fac)
      if (goal.spec.started) {
        // 全マスの 1/4 が植わっていれば成り立つ（定石の育成中の判定。残りは育ったものから回収して埋める）
        out.met = f.started
        out.remaining = Math.max(0, f.quota - f.planted)
        out.lines.push(`${fac.name}: ${f.planted}/${f.quota} cells planted to start (of ${fac.layout.cells.length})`)
        break
      }
      // 畑は、地面の高さがそろっていて（四角と、まわり 2 マス）、柵とゲートが立っていて初めて済み（設計書 75）。
      // 整地は耕す前。柵と門は、畑のマスが全部済んでから
      const lv = fac.kind === 'farm' && fac.layout.rect ? farmLevel(bot, state, fac) : null
      const fenceLeft = f.fence.length + (f.gate ? 1 : 0)
      out.met = f.done === f.total && !fenceLeft && !(lv && (lv.off || lv.unloaded))
      out.remaining = f.total - f.done + fenceLeft + (lv ? lv.off : 0)
      const what = fac.kind === 'farm' ? `farm ${fac.name} (${fac.layout.crop})` : `tree farm ${fac.name} (${fac.layout.sapling})`
      out.lines.push(`${what}: ${f.done}/${f.total} cells done${lv?.off ? `, ground to level ${lv.off} columns` : ''}${f.till.length ? `, to till ${f.till.length}` : ''}${f.sow.length ? `, to sow ${f.sow.length}` : ''}${f.plant.length ? `, to plant ${f.plant.length}` : ''}${f.water.length ? `, water to place ${f.water.length}` : ''}${fenceNote(f, lv)}${f.unseen || lv?.unloaded ? `, out of view ${f.unseen + (lv?.unloaded ?? 0)}` : ''}`)
      if (out.met) break
      const inv = inventoryCounts(bot)
      if (lv?.off) {
        farmLevelStep(bot, fac, lv, out, addSolved)
        break
      }
      if (fac.kind === 'farm') {
        const seed = CROPS[fac.layout.crop].seed
        const seeds = inv[seed] ?? 0
        // 種は、最初は全マスの 1/4 だけ集める（探し回らない）。それ以上は、育った小麦を収穫して出る種で埋める
        // （種を持てば、まけるだけまく。畑に作物があれば、solver が収穫するか育つのを待つ。設計書 74）
        const startingSeeds = Math.min(f.sow.length + f.till.length, Math.max(1, f.quota - f.planted), 16)
        // 水のマス（掘って水のバケツを置く）が先。水が届けば、耕しただけの地面も土に戻らない。バケツがなければ
        // 水のバケツを手に入れる（鉄でバケツを作り、水を汲む）。耕す・まくは、待たずに進めてよい
        if (f.water.length) {
          if (inv.water_bucket > 0) out.leaves.push({ kind: 'water', facility: fac.name })
          else addSolved([{ spec: 'water_bucket', count: 1 }])
        }
        if (f.sow.length) {
          if (seeds > 0) out.leaves.push({ kind: 'sow', item: seed, facility: fac.name })
          else addSolved([{ spec: seed, count: startingSeeds }])
        }
        if (f.till.length) {
          // 耕すのは、まく種が耕し済みのマスより多いときだけ: 水が近くにない畑は、耕しただけで何も植わって
          // いない地面が土に戻る（種のないまま耕し続け、最初に耕した所が土に戻った）。耕す → まく を順に
          if (!Object.keys(inv).some((n) => n.endsWith('_hoe'))) addSolved([{ spec: 'hoe', count: 1 }])
          else if (seeds > f.sow.length) out.leaves.push({ kind: 'till', facility: fac.name })
          else if (!f.sow.length) addSolved([{ spec: seed, count: startingSeeds }])
        }
        // 柵（畑のまわりの輪）とゲート: 畑のマスが全部済んでから（畑を作り終わってから）。持っていれば立てる。
        // なければ作る（柵は板と棒、まとめて 3 本できる）
        const built = f.done === f.total
        if (built && f.fence.length) {
          if (Object.keys(inv).some((n) => FENCE_ITEM.test(n) && inv[n] > 0)) out.leaves.push({ kind: 'fence', facility: fac.name })
          else addSolved([{ spec: 'oak_fence', count: Math.min(f.fence.length, 64) }])
        }
        if (built && f.gate) {
          if (Object.keys(inv).some((n) => FENCE_GATE_ITEM.test(n) && inv[n] > 0)) out.leaves.push({ kind: 'fence_gate', facility: fac.name })
          else addSolved([{ spec: 'oak_fence_gate', count: 1 }])
        }
      } else if (f.plant.length) {
        const held = Object.entries(inventoryCounts(bot)).find(([n, c]) => c > 0 && saplingMatches(fac.layout.sapling, n))
        if (held) out.leaves.push({ kind: 'plant', item: held[0], facility: fac.name })
        else if (f.planted < f.quota) addSolved([{ spec: fac.layout.sapling, count: Math.min(f.plant.length, f.quota - f.planted) }]) // 最初は全マスの 1/4 だけ
        else if (plantingStatus(bot, state, fac.layout.sapling).trees > 0) addSolved([{ spec: fac.layout.sapling, count: f.plant.length }]) // 育った木の葉から
        else out.lines.push(`${f.planted}/${f.plant.length + f.planted} cells planted: wait for the trees to grow, then break their leaves for saplings for the rest (do not search for saplings elsewhere)`)
      }
      if ((f.unseen || lv?.unloaded) && !out.leaves.length) out.leaves.push({ kind: 'go_facility', name: fac.name, pos: fac.entrance })
      break
    }
    case 'planted': {
      // 植えた場所に苗木か育った木（設計書 33）。足りなければ、設計した植林場の空いたマスに植える（設計書 47 §14）
      const { item, count } = goal.spec
      const t = plantingStatus(bot, state, item)
      const have = t.saplings + t.trees + t.unseen
      out.met = have >= count
      out.remaining = Math.max(0, count - have)
      out.lines.push(`trees you planted (${item}): ${have}/${count} (saplings ${t.saplings}, grown ${t.trees}${t.unseen ? `, out of view ${t.unseen}` : ''}${t.gone ? `, gone ${t.gone}` : ''})`)
      if (out.met) break
      const farms = designedTreeFarms(state, item)
      if (!farms.length) {
        const why = `there is no tree farm for ${item} to plant in: add facility(<name>) (a tree farm) to an objective first; it is designed on the town map`
        out.blocked.push(why)
        out.impossible.push(why)
        break
      }
      const held = Object.entries(inventoryCounts(bot)).find(([n, c]) => c > 0 && saplingMatches(item, n))
      const cell = held ? workCell(bot, state, 'plant', { sapling: held[0] }) : workCell(bot, state, 'plant', { sapling: item })
      const away = farms.find((fac) => layoutStatus(bot, fac).unseen)
      if (!cell && away) {
        out.leaves.push({ kind: 'go_facility', name: away.name, pos: away.entrance })
        break
      }
      if (!cell) {
        const why = `the tree farms (${farms.map((f) => f.name).join(', ')}) have no free cell for ${held?.[0] ?? item}: add another tree farm with a new name, facility(<new name>) (e.g. ${farms[0].name}_2); a facility cannot be enlarged`
        out.blocked.push(why)
        out.impossible.push(why)
        break
      }
      if (!held) addSolved([{ spec: item, count: out.remaining }])
      else out.leaves.push({ kind: 'plant', item: held[0], facility: cell.facility })
      break
    }
    case 'farmed': {
      // 畑の作物（設計書 47 §14）。種がなければ集める、空いた耕地があればまく、なければ設計した畑を耕す（クワ）
      const { item: crop, count } = goal.spec
      const info = CROPS[crop]
      const f = cropFarms(bot, state, crop)
      out.met = f.planted >= count
      out.remaining = Math.max(0, count - f.planted)
      const where = f.farms.map((x) => `${x.name} ${x.planted}${x.remembered ? ' (last seen)' : ''}`).join(', ')
      out.lines.push(`${crop} planted on the farms: ${f.planted}/${count} (ripe ${f.ripe}, free cells ${f.free}${where ? `; ${where}` : ''})`)
      if (f.dry) out.lines.push(`${f.dry} of them on dry farmland (moisture < 7: no water within 4 blocks; crops grow slower there)`)
      if (out.met) break
      if (!f.farms.length && !designedFarms(state, crop).length) {
        const why = `there is no farm for ${crop}: add facility(<name>) (a ${crop} farm) to an objective first; it is designed on the town map`
        out.blocked.push(why)
        out.impossible.push(why)
        break
      }
      if (f.planted + f.free < count) {
        const why = `the ${crop} farms have room for ${f.planted + f.free} (asked ${count}): for more, add another farm with a new name, facility(<new name>) (e.g. ${crop}_farm_2); a facility cannot be enlarged`
        out.blocked.push(why)
        if (!f.free) { out.impossible.push(why); break }
      }
      // 畑が崩れていたら（穴、丸石、流された所）、先に直す（設計書 115）。耕す・まくより前
      const broken = designedFarms(state, crop).map((fac) => { ensureFarmShape(bot, state, fac); return { fac, lv: fac.layout.rect ? farmLevel(bot, state, fac) : null } }).find((x) => x.lv?.off)
      if (broken) {
        out.lines.push(`the farm ${broken.fac.name} has fallen apart: ${broken.lv.off} columns to repair (holes, cobblestone or stone where crops go)`)
        farmLevelStep(bot, broken.fac, broken.lv, out, addSolved)
        break
      }
      const inv = inventoryCounts(bot)
      // 水のバケツを持っていれば、畑の水のマスにも置く（湿った耕地は育ちが速い）。バケツを集めはしない（それは facility の目標）
      if (f.water > 0 && inv.water_bucket > 0) out.leaves.push({ kind: 'water', crop })
      if (!(inv[info.seed] > 0)) {
        addSolved([{ spec: info.seed, count: Math.min(out.remaining, 16) }])
        break
      }
      const sow = workCell(bot, state, 'sow', { crop })
      if (sow) { out.leaves.push({ kind: 'sow', item: info.seed, facility: sow.facility }); break }
      const till = workCell(bot, state, 'till', { crop })
      if (!till) {
        // 空きは最後に見た数: 畑が読み込まれていない。そこへ行く
        const away = f.farms.find((x) => x.remembered && x.free)
        const fac = away && state.facilities?.[away.name]
        const pos = fac?.entrance ?? (away?.name === HOME_FARM ? state.home?.outside : null)
        if (pos) out.leaves.push({ kind: 'go_facility', name: away.name, pos })
        else out.blocked.push(`no free cell in the ${crop} farms in view`)
        break
      }
      if (!Object.keys(inv).some((n) => n.endsWith('_hoe'))) { addSolved([{ spec: 'hoe', count: 1 }]); break }
      out.leaves.push({ kind: 'till', facility: till.facility })
      break
    }
    case 'cleared': {
      const danger = dangerOutside(bot, state.home)
      out.met = danger.length === 0
      out.remaining = danger.length
      out.lines.push(`hostiles near the door: ${danger.length ? danger.map(({ e }) => e.name).join(', ') : 'none'}`)
      if (out.met) break
      // そこでクリーパーと近接で戦うと入口を吹き飛ばされる（M1 で起きた）
      if (danger.some(({ e }) => EXPLODES.has(e.name))) {
        out.blocked.push('a creeper is near the door: it explodes when fought in melee; wait for it to leave')
        break
      }
      // 外に出て戦う危なさ（risk.mjs）。極めて高いときだけ止める（敵、体力、時間帯、装備を合わせた数字）。
      // 失うものがあるときは、先に持ち物を家のチェストへ（死んでも失わない。昼も）。武器がなくても、素手で出る
      // 手は出す（夜に素手で出るかは、risk を見て選ぶ側が決める）
      const risk = assessRisk(bot, state)
      if (atLeast(risk, SHELTER_LEVEL) && SHELTER_LEVEL !== 'none') { out.blocked.push(`going out to fight is too dangerous now: ${describeRisk(risk)}`); break }
      out.lines.push(describeRisk(risk))
      if (risk.stakes === 'valuable') {
        const stash = stashLeaf(bot, state)
        if (stash) {
          out.leaves.push(stash)
          out.lines.push(`before fighting: put ${stash.items.length} kinds of items in the home's chest (keep the weapon, armor, food, torches)`)
          out.remaining += 1
          break
        }
      }
      out.leaves.push({ kind: 'clear', mobs: danger.map(({ e }) => e) })
      break
    }
  }
  // 狩るのに剣がなければ、先に作る（素手だと何発もかかる）。作るだけでできるなら狩りより先に
  // （狩りの候補を外す）、材料を集めるところからなら狩りと並べて出す
  if (!out.met && out.leaves.some((l) => l.kind === 'kill')) {
    const inv = inventoryCounts(bot)
    if (!Object.keys(inv).some((n) => n.endsWith('_sword') && inv[n] > 0)) {
      const r = solve(knowledge, world, [{ spec: 'sword', count: 1 }])
      const craftOnly = r.leaves.length > 0 && r.leaves.every((l) => l.kind === 'craft' || l.kind === 'place')
      if (craftOnly) out.leaves = out.leaves.filter((l) => l.kind !== 'kill')
      if (r.leaves.length) {
        out.leaves.push(...r.leaves.map((l) => ({ ...l, also: 'a sword for hunting' })))
        out.lines.push(craftOnly ? 'no sword: craft one before hunting' : 'no sword: gather for one while hunting')
      }
    }
  }
  // 壊れた道具を作り直す（同じ種類をもう持っていなければ）。作るだけでできるものだけ出す。材料から
  // 要るなら、それが要る目標のときにソルバーが道具から組み立てる
  if (!out.met) {
    for (const { item, kind } of toReplace(bot, state)) {
      if (kind === 'sword' && out.lines.some((l) => l.startsWith('no sword'))) continue
      // 作れる一番良い格から（壊れたのが木のつるはしでも、石が手元にあれば石のつるはしを作る）。作るだけでできるもの、
      // または家のチェストから出して作れるものだけ（取りに出かけるのは、それが要る目標のときにソルバーが組み立てる）
      let r = null
      let atHome = false
      for (const spec of [...replacementTiers(item, kind), kind]) {
        const tried = solve(knowledge, world, [{ spec, count: 1 }])
        if (!tried.leaves.length || !tried.leaves.every((l) => l.kind === 'craft' || l.kind === 'place' || l.kind === 'withdraw')) continue
        r = tried
        atHome = tried.leaves.some((l) => l.kind === 'withdraw')
        break
      }
      if (r) {
        out.leaves.push(...r.leaves.map((l) => ({ ...l, also: `a new ${kind} (the ${item} broke)` })))
        out.lines.push(`the ${item} broke: ${atHome ? `take the materials out of the home's chest and craft another ${kind}` : `craft another ${kind}`}`)
      } else {
        out.lines.push(`the ${item} broke: no materials at hand to craft another ${kind}; go home to make one (the crafting table and the chest are there)`)
      }
    }
  }
  // 生肉を持っているのに、かまどがどこにもない: 焼けば満腹度が 2.7 倍（牛肉 3 → 8）になるので、作って置く
  // （2026-10-01: 夜中に 18 回食べたうち焼いたのは 2 回だけ。家にかまどがなく、焼く手が一度も出なかった。
  // 走ると 40 m ごとに満腹度 1 を使うので、牛まで往復して生で食べる食料は赤字だった）
  if (!out.met && bot.inventory) {
    const inv = inventoryCounts(bot)
    const raw = Object.keys(inv).filter((n) => isRawMeat(knowledge, n)).reduce((s, n) => s + inv[n], 0)
    if (raw >= FAR_COOK_MIN && !knownFurnace(bot, state)) {
      const why = `a furnace to cook the ${raw} raw meat (cooked restores 2.7 times as much hunger)`
      if (inv.furnace > 0) {
        out.leaves.push({ kind: 'place', item: 'furnace', also: why })
        out.lines.push('raw meat and no furnace: place the furnace you carry, then cook it')
      } else {
        const r = solve(knowledge, world, [{ spec: 'furnace', count: 1 }])
        if (r.leaves.length && r.leaves.every((l) => l.kind === 'craft' || l.kind === 'place')) {
          out.leaves.push(...r.leaves.map((l) => ({ ...l, also: why })))
          out.lines.push('raw meat and no furnace: craft one (8 cobblestone), place it, then cook the meat')
        }
      }
    }
  }
  // 上の格の道具が今ある材料で作れるなら作る（木のつるはし → 石のつるはし）。種類ごとに一番上だけ
  if (!out.met) {
    for (const kind of new Set(upgradesToTry(bot).map((u) => u.kind))) {
      for (const u of upgradesToTry(bot).filter((u) => u.kind === kind)) {
        const r = solve(knowledge, world, [{ spec: u.item, count: 1 }])
        if (!r.leaves.length || !r.leaves.every((l) => l.kind === 'craft' || l.kind === 'place')) continue
        out.leaves.push(...r.leaves.map((l) => ({ ...l, also: `a better ${kind} (${u.item} instead of the ${u.from} one)` })))
        out.lines.push(`a ${u.item} can be crafted from what you carry (better than the ${u.from} ${kind})`)
        break
      }
    }
  }
  // 長い水を泳いで渡った・渡ろうとした（legToward が覚える）: ボートがなければ作る（設計書 37）。
  // 作るだけでできるときだけ候補に出し、材料からなら行で知らせる
  const water = state.waterAhead
  if (!out.met && water && Date.now() - water.at < WATER_AHEAD_MS && !boatItem(bot) && !riding(bot)) {
    const r = solve(knowledge, world, [{ spec: 'boat', count: 1 }])
    if (r.leaves.length && r.leaves.every((l) => l.kind === 'craft' || l.kind === 'place')) {
      out.leaves.push(...r.leaves.map((l) => ({ ...l, also: `a boat to cross the water on the way (${water.length}m)` })))
      out.lines.push(`${water.length}m of water on the way: a boat can be crafted from what you carry and crosses it far faster than swimming`)
    } else {
      out.lines.push(`${water.length}m of water on the way: a boat (5 planks of one wood, at a crafting table) would cross it far faster than swimming`)
    }
  }
  // ついでに取れるもの（ほかの大目標の物）: 昼に、そばにあれば。今の目標の残りには数えない
  if (!out.met && phase === 'day' && goal.also?.length) {
    const inv = inventoryCounts(bot)
    for (const a of goal.also) {
      const have = a.members.reduce((s, m) => s + (inv[m] ?? 0), 0)
      if (have >= a.count) continue
      const r = solve(knowledge, world, [{ spec: a.item, count: a.count - have }])
      for (const l of r.leaves) {
        if (l.kind === 'dig' || l.kind === 'kill') out.leaves.push({ ...l, also: `${a.item} for another objective`, near: SIDE_RADIUS })
      }
    }
  }
  return out
}

const STASH_MIN_ITEMS = 8

export const armed = (bot) => (bot.inventory?.items?.() ?? []).some((i) => /_(sword|axe)$/.test(i.name))

// 夜に戦いに出る前に預ける物（家のチェストがわかっていて、預ける物が STASH_MIN_ITEMS 個以上）
function stashLeaf (bot, state) {
  const [chest] = homeChests(state.memory ?? {}, state.home)
  if (!chest) return null
  const items = Object.entries(inventoryCounts(bot)).filter(([name, n]) => n > 0 && !KEEP_WHEN_FIGHTING.test(name))
  if (items.reduce((s, [, n]) => s + n, 0) < STASH_MIN_ITEMS) return null
  return { kind: 'stash', pos: new Vec3(chest.x, chest.y, chest.z), items: items.map(([item, count]) => ({ item, count })) }
}

// 家のものでない、近くに置いてあるベッド（村など）の足側
const BED_SEARCH = 32
const isBedItem = (item) => item === 'bed' || item.endsWith('_bed')

function nearbyBed (bot, state) {
  const ids = Object.values(bot.registry.blocksByName).filter((b) => b.name.endsWith('_bed')).map((b) => b.id)
  const me = bot.entity.position
  return bot.findBlocks({ matching: ids, maxDistance: BED_SEARCH, count: 16 })
    // 家と前の家のベッドは出さない（前の家は閉じていて、中に入れないことがある）、さっき行けなかったものも
    .filter((p) => ![state.home, ...(state.formerHomes ?? [])].some((h) => h && isInside({ entity: { position: p } }, h)))
    .filter((p) => !isNoPathTo(state, p))
    .sort((a, b) => a.distanceTo(me) - b.distanceTo(me))[0] ?? null
}

// 目標を立てずに条件を判定する: [{ spec, met, lines, impossible }]（impossible: いまボットに何を
// しても手に入らないもの）。不正な条件なら投げる。
// まだ判定できない条件（建っていない家のベッド）は満たしていないとする。
// item を 1 から作るとき、材料の木に出てくるアイテム（持ち物やチェストの中身は使わない静的な木）。
// root は item そのもの（グループなら中身）、all は木のすべて。計画の小目標が何のためかを確かめるのに使う
// （docs/design/67: 計画に「チェストのための鉄インゴット」と書かれた。チェストは板材だけで作れる）
export function materialsOf (knowledge, world, item) {
  // 持ち物だけでなく、周りの世界も空にする。家があると地下の石は採掘場・階段掘りから（松明 8 本と剣が先）になり、
  // 松明は石炭、石炭は地下の鉱石で、輪になって「不可能」と判定される。不可能は木ごと空にするので、石の道具の材料
  // （丸石・棒）が一つも出なかった（2026-10-03: 預ける手が石のクワの材料を戻し続けた）
  const empty = {
    ...world, inventory: {}, stored: {}, table: null, furnace: null, furnaceAway: false, smelting: {},
    blocks: {}, mobs: {}, remembered: undefined, buried: undefined, home: null, mine: null, mines: [], minePlan: undefined
  }
  const nodes = solve(knowledge, empty, [{ spec: item, count: 1 }]).nodes
  const all = new Set()
  const walk = (n) => {
    if (n.spec) for (const m of knowledge.resolve(n.spec).members) all.add(m)
    for (const c of n.children ?? []) walk(c)
  }
  nodes.forEach(walk)
  return { root: knowledge.resolve(item).members, all: [...all] }
}

// 持ち物・チェスト・作業台を数えたうえで、まだ足りない物（ソルバーの木で、持っている数が要る数に届かない節の物）。
// 物を作るとき、レシピが直接使う材料をまず並べ、持っているぶんを引いて、足りないものだけを順に追う
// （2026-10-07: 板材 5 と棒 9 を持っているのに、木のツルハシの前に原木 3 個を集めるよう計画に書かれた）。
// root は item そのもの。足りない物がなければ、その物を作る手だけが残る
export function shortfallOf (knowledge, world, item, count = 1) {
  const nodes = solve(knowledge, world, [{ spec: item, count }]).nodes
  const short = new Set()
  const walk = (n) => {
    if (n.spec && n.have < n.need) for (const m of knowledge.resolve(n.spec).members) short.add(m)
    for (const c of n.children ?? []) walk(c)
  }
  nodes.forEach(walk)
  return [...short]
}

// options.materials: have / placed / stored の条件に、その物の材料の木（materialsOf）を添える
export function checkConditions (specs, bot, state, knowledge, world, options = {}) {
  // 判定できない条件は、見つかった全部をまとめて言う（1 件ずつ差し戻すと、直したそばから次を踏んで書き直しが尽きる）
  const rejected = []
  const out = specs.map((spec) => {
    try {
      return checkCondition(spec, bot, state, knowledge, world, options)
    } catch (e) {
      rejected.push({ spec, error: e })
      return null
    }
  })
  if (rejected.length === 1) throw rejected[0].error
  if (rejected.length) {
    const label = ({ spec }) => `${spec.predicate}(${Object.entries(spec).filter(([k, v]) => k !== 'predicate' && v != null).map(([, v]) => v).join(', ')})`
    throw new Error(rejected.map((r) => `${label(r)}: ${r.error.message}`).join('; '))
  }
  return out
}

function checkCondition (spec, bot, state, knowledge, world, options) {
  if (!CONDITION_PREDICATES.includes(spec.predicate)) {
    throw new Error(`${spec.predicate} cannot be a condition; use one of ${CONDITION_PREDICATES.join(', ')}`)
  }
  let goal
  try {
    goal = makeGoal(spec, bot, state, knowledge)
  } catch (e) {
    if (e instanceof NotYetError) return { spec, met: false, lines: [e.message], impossible: [] }
    throw e
  }
  const r = evaluate(bot, { ...state, goal }, knowledge, world)
  const out = { spec: goal.spec, met: r.met, lines: r.lines, impossible: [...new Set(r.impossible)], next_step: r.next ? { predicate: 'have', item: r.next.item, count: r.next.count } : null, gather: !!r.gather }
  if (options.materials && ['have', 'placed', 'stored'].includes(goal.spec.predicate) && goal.spec.item) {
    try {
      out.materials = { ...materialsOf(knowledge, world, goal.spec.item), missing: shortfallOf(knowledge, world, goal.spec.item, goal.spec.count ?? 1) }
    } catch {
      // 材料の木を作れない物（作り方が分からない、など）は添えない
    }
  }
  return out
}

// 体の欲求。数値と重さだけで、行動の名前は書かない（選ぶ側は、これを明示すると判断がよくなり、
// 何をすべきか書くと悪くなった）
export function needs (bot, state) {
  const out = []
  // 自然回復は満腹度 18 以上のときだけ（満腹度 12 で体力 1.5 のまま、約 3 分動いて死んだ: 2026-10-01）
  if (bot.health <= HEALTH_CRITICAL) {
    let line = `health critical (${Math.round(bot.health)}/20)` + (bot.food < REGEN_FOOD ? `: it heals only while food is 18 or more (food ${bot.food})` : '')
    // 回復の手段がない: 家にいても治らない。死んで失うのは持ち物だけ（数だけ書く。何をすべきかは書かない）
    if (cannotHeal(bot)) {
      const carried = valuablesCarried(bot)
      line += `; you carry no food. A death drops only what you carry: ${carried ? `${carried} items besides weapons, armor and torches` : 'nothing besides weapons, armor and torches'}`
    }
    out.push(line)
  }
  // 体力が少し減っていて満腹度が 18 に届かない（危険なほどではない）: 数だけ。「hunger」とは書かない（Python が空腹と読む）
  else if (needsFoodToHeal(bot)) out.push(`health ${Math.round(bot.health)}/20 and food ${bot.food}/20: it heals only while food is 18 or more`)
  if (bot.food <= HUNGER_URGENT) out.push(`hunger urgent (${bot.food}/20): healing stops below 18 and sprinting below 7`)
  const wet = waterNeed(bot)
  if (wet) out.push(wet)
  // 水の中にいる間は、陸にいないと知らせる（陸に出たと思い込んで、水の中で次の手を選んでいた: 2026-10-01）
  else if (isSwimming(bot)) out.push('you are swimming in water: you are not on land')
  const phase = dayPhase(bot.time.timeOfDay)
  // 深い地下は夜も昼も同じ暗さ: 夜の知らせは出さない（帰らせない）
  const below = phase !== 'day' && phase !== 'dawn' && isUnderground(bot)
  if (phase === 'dusk' && !below) out.push('night is coming: hostile mobs spawn outside in the dark')
  if (phase === 'night' && !isInside(bot, state.home) && !below) out.push('it is night and you are outside')
  if (isDark(bot)) {
    const torches = inventoryCounts(bot).torch ?? 0
    out.push('dark here (covered, no light near): hostile mobs can spawn around you' +
      (torches === 0 ? '; you carry no torch: turn back and make torches (a torch is 1 coal or charcoal + 1 stick)'
        : torches < TORCH_LOW ? `; only ${torches} torch${torches > 1 ? 'es' : ''} left: place them every few blocks and make more before they run out (a torch is 1 coal or charcoal + 1 stick)` : ''))
  }
  for (const { e, dist } of reachableThreats(bot, state)) out.push(`hostile ${e.name} ${Math.round(dist)}m away`)
  for (const { e } of dangerOutside(bot, state.home)) out.push(`${e.name} waiting outside the door`)
  for (const b of state.home?.breach ?? []) out.push(`the house wall has a hole at ${b.x},${b.y},${b.z}`)
  return out.length ? out : ['none']
}
