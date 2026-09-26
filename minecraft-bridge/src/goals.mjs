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
//   cleared()                ドアの近くで待つ敵対モブがいない（昼だけ: 外に出て戦う）
//   stored(item, count)      家のチェストにアイテムかグループが `count` 個ある（最後に開けたときの中身で）
//   lit(distance)            家のまわり半径 `distance` の地面に暗い所がない
//   surveyed(count)          街の候補地を `count` か所調べた（docs/design/16_town_site.md）

import vec3Pkg from 'vec3'
import { solve } from './solver.mjs'
import { dayPhase, inventoryCounts, EXPLODES, isDark } from './observe.mjs'
import { isInside, isDoorOpen, hasBed, bedSpot, dangerOutside } from './home.mjs'
import { homeChests, storedCounts } from './memory.mjs'
import { darkGround } from './lighting.mjs'
import { cooking } from './cooking.mjs'
import { placedBedToTake, HOME_FURNITURE, furnitureInHome } from './furniture.mjs'
import { ensureSurvey, surveyedSites } from './survey.mjs'
import { reachableThreats, LEG, SLEEP_FROM, SLEEP_UNTIL, HEALTH_CRITICAL, HUNGER_URGENT } from './primitives.mjs'

const { Vec3 } = vec3Pkg
const MAX_COUNT = 256
const MAX_DIG_DEPTH = 64
const MIN_EXPLORE = 8
const MIN_LIT = 8
const MAX_LIT = 32
const MAX_EXPLORE = 256
const EXPLORE_STEP = 20 // 探索の1ステップで進むおよその距離
const DAY_TICKS = 24000
const MORNING = 0 // また日が昇る時刻（明け方は 24000 = 0 で終わる）
const TICKS_PER_MINUTE = 1200

export const PREDICATES = ['have', 'built', 'placed', 'at_home', 'through_night', 'explored', 'cleared', 'stored', 'lit', 'surveyed']
// ワールドの状態だけから判定するので、中目標の完了条件にできる
// （ほかはその時点や、目標を立てた場所によって変わる）
export const CONDITION_PREDICATES = ['have', 'built', 'placed', 'stored', 'lit', 'surveyed']

// 目標の指定を検証し、保持する目標の状態を返す。不正なら理由を添えて投げる
// 正しいが、何か（家、計画）ができるまで追えない目標
export class NotYetError extends Error {}

// どの目標も立てた場所を覚えている: 探索はそこから離れる方向に行い、行ったり来たりしない
// keep: 中目標のためにチェストに取っておくもの（[{item, count}]、中目標の stored() の条件）。
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
  return { ...goal, exploreFrom: { x: p.x, y: p.y, z: p.z }, surfaceY: Math.floor(p.y), keep: validKeep(spec.keep ?? [], knowledge) }
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
        if (!state.builds?.[spec.name]) throw new NotYetError(`there is no build named ${spec.name} yet: it is designed when the mid goal is added`)
        return { spec: { predicate, name: String(spec.name) } }
      }
      if (!state.plan) throw new NotYetError('there is no house plan')
      return { spec: { predicate } }
    case 'placed':
      // 家の中に置く家具（furniture.mjs の HOME_FURNITURE）: ベッド、作業台、かまど、チェスト
      if (!HOME_FURNITURE[spec.item] || spec.where !== 'home') {
        throw new Error(`placed supports ${Object.keys(HOME_FURNITURE).join(', ')} in the home, e.g. placed(crafting_table, home)`)
      }
      needHome()
      return { spec: { predicate, item: spec.item, where: 'home' } }
    case 'at_home':
      needHome()
      return { spec: { predicate } }
    case 'through_night':
      needHome()
      return { spec: { predicate }, sawNight: dayPhase(bot.time.timeOfDay) !== 'day' }
    case 'explored': {
      const distance = Number(spec.distance)
      if (!Number.isInteger(distance) || distance < MIN_EXPLORE || distance > MAX_EXPLORE) {
        throw new Error(`distance must be ${MIN_EXPLORE}-${MAX_EXPLORE}`)
      }
      const p = bot.entity.position
      return { spec: { predicate, distance }, start: { x: p.x, y: p.y, z: p.z } }
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
    case 'cleared': {
      needHome()
      // 暗いうちは次々に湧く: 夜は中で明けるのを待つ（through_night）
      const phase = dayPhase(bot.time.timeOfDay)
      if (phase !== 'day') throw new Error(`it is ${phase}: hostile mobs keep spawning in the dark; stay inside until morning`)
      return { spec: { predicate } }
    }
    default:
      throw new Error(`unknown predicate ${predicate}; use one of ${PREDICATES.join(', ')}`)
  }
}

// いまの目標についての { spec, met, remaining, lines, blocked, impossible, leaves }
// 家の計画のうち、置けていないドア（向きが違うものを含む）
function brokenDoor (bot, state) {
  if (!state.home || !state.plan?.origin) return null
  return state.plan.pending(bot).find((b) => b.block === 'door') ?? null
}

export function evaluate (bot, state, knowledge, world) {
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
    if (door) {
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
      const r = addSolved([{ spec: goal.spec.item, count: goal.spec.count }])
      out.met = r.met
      break
    }
    case 'built': {
      const name = goal.spec.name
      const plan = name ? state.builds[name] : state.plan
      const status = plan.status(bot)
      out.met = status.complete
      if (out.met) break
      // 原木のブロックのぶんの原木を先に取り、それが板材にされないようにする
      const need = plan.materialsNeeded(bot)
      addSolved(['log', 'door', 'planks', 'cobblestone', 'dirt'].filter((k) => need[k]).map((k) => ({ spec: k, count: need[k] })))
      out.lines.unshift(`${name ? `blocks of the build ${name}` : 'house blocks'} placed ${status.placed}/${status.total}`)
      out.remaining += status.total - status.placed
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
      if (goal.spec.item !== 'bed') {
        // 作業台・かまど・チェストを家の中に置く
        const item = goal.spec.item
        const at = furnitureInHome(bot, state.home, item)
        out.met = !!at
        out.lines.push(`a ${item} in the house: ${at ? `yes (${at.x},${at.y},${at.z})` : 'no'}`)
        if (out.met) break
        const held = (inventoryCounts(bot)[item] ?? 0) > 0
        if (!held) addSolved([{ spec: item, count: 1 }])
        else out.leaves.push({ kind: 'place_in_home', item })
        out.remaining += 1
        break
      }
      out.met = hasBed(bot, state.home)
      out.lines.push(`a bed in the house: ${out.met ? 'yes' : 'no'}`)
      if (out.met) break
      const bed = Object.keys(inventoryCounts(bot)).some((n) => n.endsWith('_bed'))
      // 近くに置いてあるベッド（家の外）は、拾って運べる（作るのと並べて出す: どちらかは選ぶ側）
      const placedBed = bed ? null : placedBedToTake(bot, state)
      if (placedBed) out.leaves.push({ kind: 'take_placed', ...placedBed })
      if (!bed) addSolved([{ spec: 'bed', count: 1 }])
      else if (bedSpot(bot, state.home)) out.leaves.push({ kind: 'place_bed' })
      else out.blocked.push('no free spot for the bed in the house')
      out.remaining += 1
      break
    }
    case 'at_home':
      out.met = inside && !isDoorOpen(bot, state.home) && !state.home.breach.length
      if (!out.met) {
        out.leaves.push({ kind: 'go_home' })
        out.remaining = 1
      }
      out.lines.push(`inside the house with the door closed: ${out.met ? 'yes' : 'no'}`)
      break
    case 'through_night': {
      if (phase !== 'day') goal.sawNight = true
      out.met = goal.sawNight && phase === 'day'
      out.lines.push(`the night has passed: ${out.met ? 'yes' : `no (${phase})`}`)
      if (out.met) break
      // 朝までの分数: 待つことが進み具合に表れるので、停滞とみなされない
      const tod = bot.time.timeOfDay
      out.remaining = Math.max(1, Math.ceil(((MORNING - tod + DAY_TICKS) % DAY_TICKS) / TICKS_PER_MINUTE))
      if (!inside) out.leaves.push({ kind: 'go_home' })
      else if (hasBed(bot, state.home) && tod >= SLEEP_FROM && tod <= SLEEP_UNTIL && !bot.isSleeping) out.leaves.push({ kind: 'sleep' })
      break
    }
    case 'explored': {
      const start = new Vec3(goal.start.x, goal.start.y, goal.start.z)
      const d = bot.entity.position.xzDistanceTo(start)
      out.met = d >= goal.spec.distance
      out.lines.push(`explored ${Math.floor(d)}/${goal.spec.distance}m`)
      if (!out.met) {
        out.remaining = Math.ceil((goal.spec.distance - d) / EXPLORE_STEP)
        out.leaves.push({ kind: 'explore', item: 'new places', sources: [], away: start })
      }
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
      } else {
        const inv = inventoryCounts(bot)
        // いま焼ける生肉は焼いてから入れる（焼く候補、次にかまど）
        const cook = cooking(bot, knowledge)
        const held = members.filter((m) => inv[m] > 0 && m !== cook?.input).map((m) => ({ item: m, count: Math.min(inv[m], want) }))
        if (held.length) out.leaves.push({ kind: 'deposit', items: held })
      }
      addSolved([{ spec: goal.spec.item, count: want }], gathering)
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
    case 'cleared': {
      const danger = dangerOutside(bot, state.home)
      out.met = danger.length === 0
      out.remaining = danger.length
      out.lines.push(`hostiles near the door: ${danger.length ? danger.map(({ e }) => e.name).join(', ') : 'none'}`)
      if (out.met) break
      if (phase !== 'day') out.blocked.push(`it is ${phase}: stay inside until morning`)
      // そこでクリーパーと近接で戦うと入口を吹き飛ばされる（M1 で起きた）
      else if (danger.some(({ e }) => EXPLODES.has(e.name))) out.blocked.push('a creeper is near the door: it explodes when fought in melee; wait for it to leave')
      else out.leaves.push({ kind: 'clear', mobs: danger.map(({ e }) => e) })
      break
    }
  }
  return out
}

// 目標を立てずに条件を判定する: [{ spec, met, lines, impossible }]（impossible: いまボットに何を
// しても手に入らないもの）。不正な条件なら投げる。
// まだ判定できない条件（建っていない家のベッド）は満たしていないとする。
export function checkConditions (specs, bot, state, knowledge, world) {
  return specs.map((spec) => {
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
    return { spec: goal.spec, met: r.met, lines: r.lines, impossible: [...new Set(r.impossible)] }
  })
}

// 体の欲求。数値と重さだけで、行動の名前は書かない（選ぶ側は、これを明示すると判断がよくなり、
// 何をすべきか書くと悪くなった）
export function needs (bot, state) {
  const out = []
  if (bot.health <= HEALTH_CRITICAL) out.push(`health critical (${Math.round(bot.health)}/20)`)
  if (bot.food <= HUNGER_URGENT) out.push(`hunger urgent (${bot.food}/20): healing stops below 18 and sprinting below 7`)
  const phase = dayPhase(bot.time.timeOfDay)
  if (phase === 'dusk') out.push('night is coming: hostile mobs spawn outside in the dark')
  if (phase === 'night' && !isInside(bot, state.home)) out.push('it is night and you are outside')
  if (isDark(bot)) out.push('dark here (covered, no light near): hostile mobs can spawn around you')
  for (const { e, dist } of reachableThreats(bot, state)) out.push(`hostile ${e.name} ${Math.round(dist)}m away`)
  for (const { e } of dangerOutside(bot, state.home)) out.push(`${e.name} waiting outside the door`)
  for (const b of state.home?.breach ?? []) out.push(`the house wall has a hole at ${b.x},${b.y},${b.z}`)
  return out.length ? out : ['none']
}
