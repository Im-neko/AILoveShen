// 候補: 選ぶ側が選べる、ワールドに即した具体的なプリミティブの行動。
//
// 目標の末端（いま目標を進めるもの）と体の欲求（脅威、空腹、武器、近くのドロップ、壁の穴）から
// 作り、待つことも加える。安全はここで守り、選ぶ側には任せない: 家に避難している間（夜、または
// ドアの前に敵対モブがいるとき）は外での行動を出さない。選ぶ側は、夜に「外で原木を掘りに行く」を
// 9回中5回選んだ。外したものは報告し（目標が進まない理由になる）、昼には出る方法を用意する:
// ドアの前で待つものと戦う（cleared の目標）か、別の壁を掘って出口にする。これがないと、
// スケルトンがドアの近くに立っている間、ボットは朝のあいだずっと中にいた。
//
// 候補の id は対象（位置かエンティティの id）を名指しするので、次の /act まで変わらない。/act は
// 候補をもう一度作り、その id の候補を実行する。

import vec3Pkg from 'vec3'
import { workCell, mineStatus, waterAt, HOME_FARM } from './facilities.mjs'
import { cropBySeed, CROPS } from './farming.mjs'
import { describeWhere } from './placing.mjs'
import { round, bearing, dayPhase, burningInDaylight, isDark, isUnderground, inventoryCounts, fillItem, UNDERGROUND_BELOW, EXPLODES } from './observe.mjs'
import { leakingHoles } from './ledger.mjs'
import { sleepersInBeds } from './sleepers.mjs'
import { isInside, exitSpots, shelterOf, hasBed, homeEntryBlocked } from './home.mjs'
import { inBurrow, inOpenShaft, burrowSpotCached } from './burrow.mjs'
import { campSiteCached, campCount } from './camp.mjs'
import { findLandCached } from './land.mjs'
import { closedSpaceCached, isolatedSpaceCached, pillarUnder, regionCached } from './reach.mjs'
import { digOutRoute } from './dig-out.mjs'
import { storageUsage, contentsSignature, roomyChests } from './storage.mjs'
import { memoryView } from './worldmap-places.mjs'
import { inHomeRoom } from './builds.mjs'
import { recoverableDeath, recall, visited, homeChests, chestWith, furnaceWith, frontierDistance } from './memory.mjs'
const FRONTIER_SLACK = 8
import { cooking, isRawMeat } from './cooking.mjs'
import { TORCH_LOW, materialsOf } from './goals.mjs'
import { surfaceExits } from './surface.mjs'
import { UNDERGROUND_LEAVES } from './solver.mjs'
import { riding } from './boat.mjs'
import { isSwimming } from './swim.mjs'
import { caveOre } from './mining.mjs'
import { refusalAt } from './refusals.mjs'
import { KEEP_WHEN_FIGHTING, toReplace } from './wear.mjs'
import { REGEN_FOOD, cannotHeal, needsFoodToHeal } from './healing.mjs'
import { windfallNow, windfallTrips } from './windfall.mjs'
import { torchesNeeded } from './trail.mjs'
import { goalKeep, spareStacks, emptySlots, TOSS_BELOW, reserveFor, mineInProgress, MINING_RESERVE } from './spare.mjs'
import { seedsToToss, seedsToTake, seedToss } from './seeds.mjs'
import { knownFurnace, FURNACE_ITEM, reachableThreats, SLEEP_FROM, SLEEP_UNTIL, bestWeapon, nearbyDrops, findTable, torchSpot, stationSpot, stationSpots, HEALTH_CRITICAL, HUNGER_URGENT, EAT_WASTE_OK, EXPLORE_DISTANCE } from './primitives.mjs'

const { Vec3 } = vec3Pkg
const DROP_RADIUS = 16
const DROPS_OFFERED = 3
const THREATS_OFFERED = 2
const EXPLORE_DIRECTIONS = { north: [0, -1], east: [1, 0], south: [0, 1], west: [-1, 0] }
// 飢えていて、ほかによい食料を持っていないときだけ食べる: 満腹度 4 と引き換えに、たいてい短い
// 空腹の効果を受ける（frun2: 腐った肉を持ったまま飢えた）
const LAST_RESORT_FOOD = ['rotten_flesh']
const RAW_OK = 12 // 焼く手段がないとき、生肉を食べてよい満腹度

const fmt = (p) => `${p.x},${p.y},${p.z}`
const dist = (bot, p) => round(bot.entity.position.distanceTo(p))

// { candidates, withheld }: withheld は外したものがあればその内容（避難の規則、ここに置く場所が
// ない作業台やかまど）
export function ground (bot, state, knowledge, world, status) {
  const out = []
  // also の印（ほかの大目標のため、狩りのための剣）は、その葉から出た候補すべてに付ける
  for (const leaf of status?.leaves ?? []) {
    // 束（設計書 44）の葉は、どの条件のためかを for に
    out.push(...fromLeaf(bot, state, world, leaf).map((c) => leaf.also && !c.also ? { ...c, also: leaf.also } : c)
      .map((c) => leaf.for && !c.for ? { ...c, for: leaf.for } : c))
  }
  out.push(...forNeeds(bot, state, knowledge))
  out.push(...stashWhenNothingHeals(bot, state, knowledge, world))
  out.push(...windfallNow(bot, state, knowledge), ...windfallTrips(bot, state, knowledge))
  out.push(...tidyUp(bot, state, knowledge, world, status))
  out.push(...defragUp(bot, state))
  const home = state.home
  const { inside, day, danger, sheltering, why } = shelterOf(bot, home)
  // 同じ id なら先のものを残す: 目標の候補は目標が許すこと（confront）を持っている
  const distinct = [...out.reduce((m, c) => m.has(c.id) ? m : m.set(c.id, c), new Map()).values()]
  // 断られた・着けなかった場所への同じ手は出さない（refusals.mjs）。何を外したかは withheld に
  const refused = distinct.filter((c) => c.pos && refusalAt(state, c.verb, c.pos))
  // 追ってくるクリーパーを家へ連れて帰ると、壁と床を吹き飛ばされる（docs/design/88）
  const leadsHome = creeperNear(bot, state) ? distinct.filter((c) => c.verb === 'go_home') : []
  const unique = distinct.filter((c) => !refused.includes(c) && !leadsHome.includes(c))
  const safe = sheltering ? unique.filter((c) => c.confront || !needsOutside(c, home)) : unique
  const held = unique.length - safe.length
  let withheld = null
  if (refused.length) {
    withheld = `left out (refused or unreachable a moment ago): ${refused.map((c) => `${c.id} (${refusalAt(state, c.verb, c.pos).reason})`).join('; ')}`
  }
  const also = (text) => { withheld = withheld ? `${withheld}; ${text}` : text }
  if (leadsHome.length) also(`${CREEPER_HOME_REASON}; left out: ${leadsHome.map((c) => c.id).join(', ')}`)
  if (held) {
    also(`staying inside: ${held} actions outside are held back (${why}). ` +
      (day ? 'cleared() goes out to fight them; an exit can be dug through another wall' : 'wait for morning, or cleared() goes out to fight'))
  }
  // ドアの前の敵を避けて、壁に出口を掘る手（昼）。外に出てよいときも、ドアの前の敵を避ける選択肢として出す
  if (inside && day && danger.length && !home.breach.length) {
    for (const spot of exitSpots(bot, home, danger.map(({ e }) => e))) {
      safe.push({ id: `exit through the ${spot.side} wall`, verb: 'exit_wall', target: `${spot.side} wall`, inPlace: true, spot, nearest_hostile: round(spot.distance) })
    }
  }
  // 待つことは、時間が変えるもの（回復、朝、日光で燃えるモブ）のためか、ほかに何もできないときに
  // 出す。目的なしに出すと、選ぶ側は何も変わらないのに中で待った: 昼に9ステップ続けて、また
  // ドアの前にハスクがいて体力が満タンのとき6回中6回。
  const station = (status?.leaves ?? []).find((l) => l.kind === 'place')
  if (station && !stationSpot(bot, state, station.item)) {
    const room = `no room here to place the ${station.item}: move to flat open ground`
    withheld = withheld ? `${withheld}; ${room}` : room
  }
  // 目標の葉が同じ候補（through_night の sleep in the bed）を出していれば重ねない
  safe.push(...(inside ? waitsInside(bot, state, danger) : []).filter((c) => !safe.some((o) => o.id === c.id)))
  if (!safe.length) {
    safe.push({ id: inside ? 'wait inside' : 'wait', verb: 'wait', target: inside ? 'inside the house' : 'here', inPlace: true, inside, seconds: 10, purpose: 'nothing else can be done now' })
  }
  return { candidates: safe, withheld }
}

// 避難の判定は home.mjs（作業台の置き場所も使う）。道具（tools.mjs）はここから import する
export { shelterOf }

const LOW_HEALTH_WALK = 32 // 体力が危険なとき、これより遠い家へは歩かない
const SHUT_IN_SMALL = 24 // 閉じた所がこの広さ以下なら、聞かずに積んで出る（広い洞窟は、積まなくても別の出方があるかもしれない）
export const CREEPER_HOME_HOLD = 16 // クリーパーがこの距離以内にいる間は、家へ帰る手を出さない（追われる距離）
export const CREEPER_HOME_REASON = `a creeper is within ${CREEPER_HOME_HOLD} m: it follows you and blows up the walls next to you, so do not lead it to the house`

// 追ってくるクリーパーがいるか（閉じた家の中なら外のものは届かない）
export const creeperNear = (bot, state) => reachableThreats(bot, state).some(({ e, dist: d }) => EXPLODES.has(e.name) && d <= CREEPER_HOME_HOLD)
const LOST_TOOL_HOME = 6 // 壊れたつるはしを作りに家へ戻る手は、家からこれより離れているときに出す
const MAX_HEALTH = 20

function waitsInside (bot, state, danger) {
  const wait = (id, purpose) => ({ id, verb: 'wait', target: 'inside the house', inPlace: true, inside: true, seconds: 10, purpose })
  const out = []
  if (dayPhase(bot.time.timeOfDay) !== 'day') {
    // 家にベッドがあって眠れる時刻（敵がそばにいなければ）なら、朝まで待たずに寝る（through_night の葉と同じ候補。小目標が
    // 収集などのときも、夜の家の中で「待つ」だけを出して 10 秒待つ手を繰り返した）
    const tod = bot.time.timeOfDay
    if (!danger.length && hasBed(bot, state.home) && tod >= SLEEP_FROM && tod <= SLEEP_UNTIL && !bot.isSleeping) {
      out.push({ id: 'sleep in the bed', verb: 'sleep', target: 'bed', inPlace: true, effect: 'skips the night' })
    } else {
      out.push(wait('wait inside until morning', 'the night passes'))
    }
  }
  if (bot.health < MAX_HEALTH && bot.food >= REGEN_FOOD) out.push(wait('wait inside to heal', `health ${round(bot.health)}/${MAX_HEALTH} regenerates`))
  const burning = danger.filter(({ e }) => burningInDaylight(bot, e)).map(({ e }) => e.name)
  if (burning.length) out.push(wait('wait inside while they burn', `${burning.join(', ')} at the door burn in the sunlight`))
  return out
}

// 候補を実行するとボットが家の外に出るか（その場合は先にドアから出る）。位置のない候補は、
// 外でボットのまわりに対して行う（探索、作業台を置く）。
const NEAR_TABLE = 6 // これより遠い作業台には、新しく置く選択肢も出す
const TABLE_PLANKS = 4

function placeStation (bot, state, item, why = '') {
  return stationSpots(bot, state, item).map(({ pos, where }) => ({
    id: `place ${item} ${where === 'inside' ? 'inside the house' : 'nearby'}`, verb: 'place_station', target: item, item, pos, distance: dist(bot, pos), ...(why ? { why } : {})
  }))
}

export function newTableHere (bot, state, table) {
  const why = `the crafting table at ${fmt(table.position)} is ${dist(bot, table.position)}m away`
  const inv = inventoryCounts(bot)
  if (inv.crafting_table > 0) return placeStation(bot, state, 'crafting_table', why)
  const planks = Object.entries(inv).filter(([n]) => n.endsWith('_planks')).reduce((s, [, c]) => s + c, 0)
  if (planks < TABLE_PLANKS || !stationSpots(bot, state, 'crafting_table').length) return []
  return [{ id: `craft a crafting_table to use here (${TABLE_PLANKS} planks)`, verb: 'craft', target: 'crafting_table', item: 'crafting_table', times: 1, needsTable: false, inPlace: true, why }]
}

export function needsOutside (c, home) {
  if (c.inPlace) return false
  return !c.pos || !isInside({ entity: { position: c.pos } }, home)
}

function fromLeaf (bot, state, world, leaf) {
  switch (leaf.kind) {
    case 'dig':
      return leaf.sources.flatMap((name) => world.dig(name).map((p) => ({
        id: `dig ${name} at ${fmt(p)}`, verb: 'dig', target: name, distance: dist(bot, p), pos: p, block: name,
        ...(leaf.also ? { also: leaf.also } : {})
      }))).filter((c) => !leaf.near || c.distance <= leaf.near)
    case 'kill':
      return leaf.sources.flatMap((name) => world.hunt(name).map(({ e, dist: d }) => ({
        id: `attack ${name} #${e.id}`, verb: 'attack', target: name, distance: round(d), pos: e.position, entityId: e.id, hostile: false,
        ...(leaf.also ? { also: leaf.also } : {})
      }))).filter((c) => !leaf.near || c.distance <= leaf.near)
    case 'dig_down': {
      const [name] = leaf.sources
      const t = world.buried?.(name)?.[0]
      if (!t) return []
      return [{ id: `dig stairs down toward ${name} at ${fmt(t.pos)}`, verb: 'dig_down', target: name, pos: t.pos, distance: dist(bot, t.pos), depth: t.depth }]
    }
    case 'explore': {
      if (leaf.farm) {
        const p = new Vec3(leaf.farm.pos.x, leaf.farm.pos.y, leaf.farm.pos.z)
        const [name] = leaf.sources
        return [{ id: `go to the farm (${leaf.farm.ripe} ripe ${name})`, verb: 'goto_memory', target: name, pos: p, distance: dist(bot, p) }]
      }
      const lookingFor = leaf.sources.length ? leaf.sources.join('/') : leaf.item
      const me = bot.entity.position
      // 前に見た場所を先に出す。次に方角を、すでに行った場所には印をつけて出す
      const recalled = state.memory
        ? recall(memoryView(state, me, Number(bot.time.age), leaf.sources), leaf.sources, me, Number(bot.time.age), (p) => recallTripOk(bot, p))
          .filter((p) => !caveOre(state, p, p.kind)) // 洞窟の深い鉱石へは歩いて行かない（採掘場の壁だけ）
          .map((p) => ({
          id: `go to ${p.kind} seen at x=${p.x} z=${p.z}`, verb: 'goto_memory', target: p.kind, pos: new Vec3(p.x, p.y, p.z),
          distance: p.distance, seen_minutes_ago: p.minutesAgo, count: p.count
        }))
        : []
      // その向きで、まだ見ていない土地までの距離。近くを見尽くしていれば、そこまで区間ごとに進む
      // （見た所を行き来する探索のくり返しを避ける）
      const directions = Object.entries(EXPLORE_DIRECTIONS)
        .filter(([, [dx, dz]]) => !leaf.away || awayFrom(bot, leaf.away, dx, dz))
        .map(([dir, [dx, dz]]) => {
          const go = state.memory ? Math.max(EXPLORE_DISTANCE, frontierDistance(state.memory, me, dx, dz)) : EXPLORE_DISTANCE
          const far = go > EXPLORE_DISTANCE + FRONTIER_SLACK
          // id は変えない（/act は候補を作り直して id で探す。距離を入れると、観測と実行の間に
          // 見た区域が増えて id が変わり、選んだ探索が「今はできない」になって何も動かなかった）
          return {
            id: `explore ${dir}`,
            verb: 'explore', target: dir, looking_for: lookingFor, dx, dz, go,
            ...(far ? { unexplored_land_m: go } : {}),
            been_there: !!state.memory && visited(state.memory, me.offset(dx * EXPLORE_DISTANCE, 0, dz * EXPLORE_DISTANCE))
          }
        })
        .sort((a, b) => a.go - b.go)
      return [...recalled, ...directions]
    }
    case 'craft': {
      const table = leaf.needsTable ? findTable(bot, state) : null
      const out = [{
        id: `craft ${leaf.item} x${leaf.times}`, verb: 'craft', target: leaf.item, item: leaf.item, times: leaf.times, needsTable: leaf.needsTable,
        ...(table ? { pos: table.position, distance: dist(bot, table.position) } : { inPlace: !leaf.needsTable })
      }]
      // 作業台が遠ければ、ここに新しいものを置く選択肢も出す（歩くか、板材 4 枚で作るかは選ぶ側が決める）
      if (table && dist(bot, table.position) > NEAR_TABLE) out.push(...newTableHere(bot, state, table))
      return out
    }
    case 'place': {
      // 置ける場所でだけ出す（何も置けない場所で何度も選ばれた）。かまどと作業台は家の中だけ（家がまだないときは
      // 地下を除く外）。家の外にいるときは、置く手の代わりに家へ帰る手を出す
      const spots = placeStation(bot, state, leaf.item)
      if (spots.length || !state.home || isInside(bot, state.home) || !FURNACE_ITEM.test(leaf.item)) return spots
      return [{ id: `go home to place the ${leaf.item} inside the house`, verb: 'go_home', target: 'home', inPlace: true, distance: dist(bot, state.home.inside), why: `a ${leaf.item} is placed and used only inside the house (not in the field or underground, where monsters attack while you stand still)` }]
    }
    case 'level':
      // 畑の整地（設計書 75）。id に場所は入れない
      if (leaf.facility) return [{ id: `level the ground of the farm ${leaf.facility}`, verb: 'level', target: 'ground', columns_off: leaf.off, facility: leaf.facility, pos: new Vec3(leaf.pos.x, leaf.pos.y, leaf.pos.z), distance: dist(bot, leaf.pos) }]
      // 整地（leveling.mjs）: 1 回で届く範囲を次々にそろえる。pos は最初に手を付ける所（外の作業）
      return [{ id: `level the ground within ${leaf.distance} of ${leaf.where}`, verb: 'level', target: 'ground', columns_off: leaf.off, where: leaf.where, radius: leaf.distance, pos: leaf.pos, distance: dist(bot, leaf.pos) }]
    case 'light':
      return [{ id: `place a torch at ${fmt(leaf.pos)} (dark ground)`, verb: 'place_torch_at', target: 'torch', pos: leaf.pos, distance: dist(bot, leaf.pos) }]
    case 'smelt': {
      if (!leaf.count) {
        // 精錬に置いてきた場所。どれだけ遠くても出す（ソルバーはそこから来るものとして数えた）
        const f = state.memory && furnaceWith(state.memory, leaf.item, bot.entity.position, (x) => !state.home || inHomeRoom(state.home, x))
        if (!f) return []
        const pos = new Vec3(f.x, f.y, f.z)
        return [{ id: `take ${leaf.item} from the furnace at ${fmt(pos)}`, verb: 'smelt', target: leaf.item, item: leaf.item, pos, distance: dist(bot, pos) }]
      }
      const furnace = knownFurnace(bot, state)
      if (!furnace) return []
      const pos = furnace.position
      const where = { pos, distance: dist(bot, pos) }
      const inv = inventoryCounts(bot)
      const input = leaf.inputs.find((m) => inv[m] > 0)
      const fuel = leaf.fuels.find((m) => inv[m] >= leaf.fuelCount) ?? leaf.fuels.find((m) => inv[m] > 0)
      if (!input || !fuel) return []
      return [{ id: `smelt ${leaf.count} ${input} into ${leaf.item}`, verb: 'smelt', target: leaf.item, item: leaf.item, input, count: leaf.count, fuel, fuelCount: leaf.fuelCount, ...where }]
    }
    case 'place_plan': {
      if (leaf.build) {
        // 名前付きの建物（docs/design/25_builds.md）: 原点は登録のときに決まっている
        const s = state.builds[leaf.build].status(bot)
        const p = state.builds[leaf.build].worldPos(leaf.block)
        const what = leaf.block.block === 'air' ? 'clear the block at' : `place ${leaf.block.block} at`
        return [{ id: `${what} ${fmt(p)} for ${leaf.build}`, verb: 'place_plan', build: leaf.build, target: `${leaf.block.block} of the build ${leaf.build}`, progress: `${s.placed}/${s.total}`, pos: p, distance: dist(bot, p) }]
      }
      const plan = state.plan
      const s = plan.status(bot)
      const p = plan.origin ? plan.worldPos(leaf.block) : null
      return [{
        id: p ? `place ${leaf.block.block} at ${fmt(p)}` : plan.site ? `place ${leaf.block.block} (start the house at ${plan.site.x},${plan.site.z})` : `place ${leaf.block.block} (start the house here)`,
        verb: 'place_plan', target: `${leaf.block.block} of the house`, progress: `${s.placed}/${s.total}`, planBlock: leaf.block,
        ...(p ? { pos: p, distance: dist(bot, p) } : plan.site ? { site: plan.site, distance: round(Math.hypot(plan.site.x - bot.entity.position.x, plan.site.z - bot.entity.position.z)) } : {})
      }]
    }
    case 'withdraw': {
      // 家のチェストに蓄える目標のときは、家のチェストからは取り出さない（入れ直すだけになる）
      const away = state.goal?.spec?.predicate === 'stored' ? homeChests(state.memory ?? {}, state.home) : []
      const chest = state.memory && chestWith(state.memory, [leaf.item], bot.entity.position, away)
      if (!chest) return []
      const pos = new Vec3(chest.x, chest.y, chest.z)
      return [{ id: `take ${leaf.count} ${leaf.item} from the chest at ${fmt(pos)}`, verb: 'withdraw', target: leaf.item, item: leaf.item, count: leaf.count, pos, distance: dist(bot, pos) }]
    }
    case 'deposit':
      return leaf.items.flatMap(({ item, count }) => toChest(bot, state, item, count))
    case 'survey': {
      const { site } = leaf
      const pos = new Vec3(site.x, bot.entity.position.y, site.z)
      return [{ id: `survey the ${site.id} site at ${site.x},${site.z}`, verb: 'survey', target: site.id, pos, site, distance: dist(bot, pos) }]
    }
    case 'place_chest':
      return [{ id: 'place a chest in the house', verb: 'place_chest', target: 'chest', pos: state.home.inside, distance: dist(bot, state.home.inside) }]
    case 'place_in_home': {
      // 家の中に置く（placed(item, home)）: 家の中の空いた所だけ。チェストは今までのやり方
      if (leaf.item === 'chest') return fromLeaf(bot, state, world, { kind: 'place_chest' })
      return placeStation(bot, state, leaf.item).filter((c) => c.id.endsWith('inside the house'))
    }
    case 'stash':
      // 夜に戦いに出る前に、持ち物を家のチェストへ（武器・防具・食べ物・松明は持ったまま）
      return [{ id: `put your items in the home's chest before fighting (keep the weapon)`, verb: 'stash', target: 'chest', pos: leaf.pos, items: leaf.items, distance: dist(bot, leaf.pos) }]
    case 'place_at':
      // 好きな物を好きな場所に置く（placed(item, where, count): placing.mjs）
      return [{ id: `place ${leaf.item} ${describeWhere(leaf.where, state)}${leaf.against.y === leaf.pos.y ? ' on the wall' : ''} at ${fmt(leaf.pos)}`, verb: 'place_at', target: leaf.item, item: leaf.item, pos: leaf.pos, against: leaf.against, distance: dist(bot, leaf.pos) }]
    case 'take_placed':
      // 置いてある家具を壊して拾う（ベッドを家に運ぶ）。furniture.mjs
      return [{ id: `take the ${leaf.block} placed at ${fmt(leaf.pos)}`, verb: 'dig', target: leaf.block, block: leaf.block, pos: leaf.pos, distance: dist(bot, leaf.pos) }]
    case 'place_bed':
      return [{ id: `place the ${leaf.item ?? 'bed'} in the house`, verb: 'place_bed', target: leaf.item ?? 'bed', ...(leaf.item ? { item: leaf.item } : {}), inPlace: true, distance: dist(bot, state.home.inside) }]
    case 'go_home':
      return [{ id: 'go home', verb: 'go_home', target: 'home', inPlace: true, distance: dist(bot, state.home.inside) }]
    case 'clear': {
      // 武器があってもなくても出す: 選ぶ側がほかの方法（見えている体力、待つ、壁を掘った出口）と
      // 比べる。戦いは体力が危険になったら止まる
      const weapon = bestWeapon(bot)?.name ?? 'none (fist)'
      return leaf.mobs.map((e) => ({
        id: `attack ${e.name} #${e.id}`, verb: 'attack', target: e.name, distance: dist(bot, e.position), pos: e.position, entityId: e.id,
        hostile: true, weapon, confront: true
      }))
    }
    case 'sleep':
      return [{ id: 'sleep in the bed', verb: 'sleep', target: 'bed', inPlace: true, effect: 'skips the night' }]
    case 'sleep_at':
      // 家のでないベッド（村など）で寝る。寝るとスポーン地点もそこになる
      return [{ id: 'sleep in the bed nearby', verb: 'sleep_at', target: 'bed', pos: leaf.pos, distance: dist(bot, leaf.pos), effect: 'skips the night; sets the respawn point there' }]
    case 'bed_here':
      return [{ id: 'place your bed here and sleep', verb: 'bed_here', target: 'bed', inPlace: true, effect: 'skips the night; sets the respawn point here; pick the bed up in the morning' }]
    case 'dig_in':
      return [{ id: 'dig into the ground here and seal yourself in until morning', verb: 'dig_in', target: 'ground', pos: leaf.pos, distance: dist(bot, leaf.pos), effect: 'a 1x1 hole 3 deep, sealed on top with the dirt you dug: monsters cannot reach you; wait in it until morning' }]
    case 'wall_in':
      return [{ id: 'wall yourself in with dirt here and wait until morning', verb: 'wall_in', target: 'camp', pos: leaf.pos, distance: dist(bot, leaf.pos), effect: `${leaf.need} blocks of dirt, cobblestone or planks around you (dug by hand nearby if you carry none), roof included: monsters cannot reach you; wait in it until morning` }]
    case 'stay_burrowed':
      return [{ id: 'stay in the sealed hole until morning', verb: 'wait', target: 'the hole', inPlace: true, seconds: 10, purpose: 'you are sealed in a hole in the ground: the night outside cannot reach you' }]
    case 'stay_underground':
      return [{ id: 'keep working underground through the night', verb: 'wait', target: 'here', inPlace: true, seconds: 10, purpose: 'underground it is as dark by day as by night: the night outside does not matter here' }]
    case 'fill':
      // 空のバケツで水源の水を汲む（use_item）。id に場所は入れない（/act は候補を作り直して id で探す）
      return [{ id: 'fill the bucket with water', verb: 'use_item', target: 'bucket on water', item: 'bucket', pos: leaf.pos, distance: dist(bot, leaf.pos) }]
    case 'water': {
      // 畑の水のマス（掘って水のバケツを置く。設計書 47 §17）
      const only = leaf.facility && leaf.facility !== HOME_FARM ? { facility: leaf.facility } : {}
      const cell = workCell(bot, state, 'water', { ...only, crop: leaf.crop ?? null })
      if (!cell) return []
      return [{ id: `place water in the farm ${cell.facility}`, verb: 'place_water', target: 'water', item: 'water_bucket', pos: cell.pos, distance: dist(bot, cell.pos) }]
    }
    case 'fence':
    case 'fence_gate': {
      // 畑のまわりの柵とゲート（設計書 75）
      const fac = state.facilities?.[leaf.facility]
      const pos = leaf.kind === 'fence_gate' ? fac?.layout?.gate : fac?.layout?.fence?.[0]
      if (!pos) return []
      const p = new Vec3(pos.x, pos.y, pos.z)
      return [{ id: leaf.kind === 'fence' ? `put up the fence around the farm ${leaf.facility}` : `put the fence gate of the farm ${leaf.facility}`, verb: leaf.kind, target: leaf.kind === 'fence' ? 'fence' : 'fence gate', facility: leaf.facility, pos: p, distance: dist(bot, p) }]
    }
    case 'plant':
    case 'till':
    case 'sow': {
      // 植林と畑（設計書 33）: 設計した施設の次のマス（設計書 47 §14）。まくのは家の畑の空いた耕地も。
      // id に場所は入れない（/act は候補を作り直して id で探す: 場所の選び直しで id が変わると動かない）
      const only = leaf.facility && leaf.facility !== HOME_FARM ? { facility: leaf.facility } : {}
      const crop = leaf.crop ?? (leaf.kind === 'sow' ? cropBySeed(leaf.item) : null)
      const cell = workCell(bot, state, leaf.kind, { ...only, crop, sapling: leaf.item ?? 'sapling' })
      if (!cell) return []
      const where = cell.facility === HOME_FARM ? 'the home farm' : `${leaf.kind === 'plant' ? 'the tree farm' : 'the farm'} ${cell.facility}`
      const id = leaf.kind === 'plant'
        ? `plant ${leaf.item} in ${where}`
        : leaf.kind === 'till' ? `till the ground of ${where} with a hoe` : `sow ${leaf.item} on ${where}`
      const target = leaf.kind === 'till' ? 'farmland' : leaf.item
      return [{ id, verb: leaf.kind, target, ...(leaf.kind === 'till' ? {} : { item: leaf.item }), pos: cell.pos, distance: dist(bot, cell.pos) }]
    }
    case 'dig_mine': {
      // 採掘場を設計どおりに掘る（設計書 47 §16）: 1 回の行動で、並びの次のセルから危ないものに当たるまで
      const fac = state.facilities?.[leaf.facility]
      const m = fac ? mineStatus(bot, fac) : null
      if (!m?.next) return []
      const p = new Vec3(m.next.x, m.next.y, m.next.z)
      return [{ id: `dig the mine ${fac.name} (${m.done}/${m.total} cells dug)`, verb: 'dig_mine', target: fac.name, facility: fac.name, ...(leaf.sources ? { sources: leaf.sources } : {}), pos: p, distance: dist(bot, p) }]
    }
    case 'make_mine': {
      // 採掘場を設計して掘る（設計書 68）。外で掘るので pos は付けない（避難中は出さない）
      return [{ id: `make a mine down to y ${leaf.bottom_y} near the home and dig it (for ${leaf.item})`, verb: 'make_mine', target: 'mine', bottom_y: leaf.bottom_y, ...(leaf.sources ? { sources: leaf.sources } : {}), ...(leaf.exhausted ? { exhausted: leaf.exhausted, why: `the mine ${leaf.exhausted} is dug out to its end and no ${leaf.sources?.join('/') ?? 'ore'} showed in its walls` } : {}), distance: state.home ? dist(bot, state.home.inside) : 0 }]
    }
    case 'go_mine': {
      const p = new Vec3(leaf.mine.bottom.x, leaf.mine.bottom.y, leaf.mine.bottom.z)
      return [{ id: `go down the mine ${leaf.mine.name} (bottom y ${p.y}) for ${leaf.item}`, verb: 'goto_pos', target: leaf.mine.name, pos: p, distance: dist(bot, p) }]
    }
    case 'go_facility': {
      const p = new Vec3(leaf.pos.x, leaf.pos.y, leaf.pos.z)
      return [{ id: `go to the facility ${leaf.name}`, verb: 'goto_memory', target: leaf.name, pos: p, distance: dist(bot, p) }]
    }
    default:
      return []
  }
}

export function forNeeds (bot, state, knowledge) {
  const out = []
  for (const { e, dist } of reachableThreats(bot, state).slice(0, THREATS_OFFERED)) {
    const weapon = bestWeapon(bot)?.name ?? 'none (fist)'
    out.push({ id: `attack ${e.name} #${e.id}`, verb: 'attack', target: e.name, distance: round(dist), pos: e.position, entityId: e.id, hostile: true, weapon })
    out.push({ id: `flee from ${e.name} #${e.id}`, verb: 'flee', target: e.name, distance: round(dist), entityId: e.id, inPlace: false, pos: e.position })
  }
  // 水の中: 経路探索器は水の中から動かないので、目標が何でも進まない。先に陸へ泳ぐ（聞かずに実行。docs/design/89）
  // （壁で閉じた小部屋の水の中は除く: 陸は壁の外で、泳いでも着けない。出るには dig_out / climb_out。docs/design/90 §5.1）
  if (isSwimming(bot) && !riding(bot) && !closedSpaceCached(bot)) {
    const land = findLandCached(bot)
    if (land) {
      out.push({ id: `swim to the nearest land at ${land.x},${land.y},${land.z}`, verb: 'reach_land', target: 'land', pos: land.pos, distance: land.distance, urgent: true, purpose: 'you are in the water: walking, digging and building all need land (the pathfinder cannot plan from the water); swim to the shore first' })
    }
  }
  if (bot.food < 20) {
    // 食料の目標が食料と数えるものだけ: 腐った肉などは害がある
    const edible = new Set(knowledge.resolve('food').members)
    const foods = bot.inventory.items().filter((i) => edible.has(i.name))
    const points = (i) => bot.registry.foodsByName[i.name].foodPoints
    const gap = 20 - bot.food
    // 体力が危険で、自然回復の満腹度（18）に足りないなら、生肉でも食べる: 回復しないまま歩いて死んだ（2026-10-01）。
    // 体力が少し減っているだけ（HEAL_BELOW 以下）でも、満腹度 18 になるまで食べる: 回復は満腹度 18 以上のときだけ
    const critical = bot.health <= HEALTH_CRITICAL && bot.food < REGEN_FOOD
    const hurt = needsFoodToHeal(bot)
    const calm = !reachableThreats(bot, state).length
    // 食べる物は、捨てる点数が少ないものから、同じなら大きいものから（満腹度 19 で焼いた肉を食べて 7 点捨てない）。
    // 捨てる点数が EAT_WASTE_OK を超える物は、食べないと満腹度 18 に届かないとき（hurt）か飢えそうなときだけ
    const mustEat = bot.food <= HUNGER_URGENT || hurt
    const pickFrom = (list) => {
      const ranked = list
        .map((i) => ({ i, waste: Math.max(0, points(i) - gap) }))
        .sort((a, b) => a.waste - b.waste || points(b.i) - points(a.i))
      return (ranked.find((r) => r.waste <= EAT_WASTE_OK) ?? (mustEat ? ranked[0] : null))?.i ?? null
    }
    // 焼いた物を先に。生肉しかないときは、焼ける（かまどと燃料がある）なら焼くまで待つ。食べるのは
    // 飢えそうなとき（HUNGER_URGENT）か、焼く手段がなくてかなり空腹のとき（RAW_OK）か、回復のためだけ
    const raw = (i) => isRawMeat(knowledge, i.name)
    let best = pickFrom(foods.filter((i) => !raw(i)))
    if (!best && foods.length) {
      const canCook = !!cooking(bot, knowledge, state)
      if (bot.food <= HUNGER_URGENT || (!canCook && bot.food <= RAW_OK) || critical || (hurt && !canCook)) best = pickFrom(foods.filter(raw))
    }
    const lastResort = bot.food <= HUNGER_URGENT && bot.inventory.items().find((i) => LAST_RESORT_FOOD.includes(i.name))
    const eat = best ?? lastResort
    // urgent: 飢えそう（HUNGER_URGENT）か、体力が危険。Python の StepPicker は聞かずに食べる（2026-10-01: 満腹度 0 で肉を
    // 持ったまま、go_home を選び続けて 11 分歩き、体力を半分失った）。体力が少し減っているだけなら、敵がいないときだけ
    if (eat) out.push({ id: `eat ${eat.name}`, verb: 'eat', target: eat.name, item: eat.name, inPlace: true, ...(hurt ? { healTo: REGEN_FOOD } : {}), ...(bot.food <= HUNGER_URGENT || critical || (hurt && calm) ? { urgent: true } : {}) })
  }
  // かまどがあれば、目標に関係なく生肉を焼く（回復する満腹度が 2.7 倍）。見えなければ最後に見た
  // かまど（家など）へ焼きに行く
  const cook = cooking(bot, knowledge, state)
  if (cook) {
    const pos = cook.furnace.position
    const { input, count, fuel, fuelCount, product } = cook
    out.push({ id: `cook ${count} ${input} in the furnace at ${fmt(pos)}`, verb: 'smelt', target: product, item: product, input, count, fuel, fuelCount, pos, distance: dist(bot, pos) })
  }
  out.push(...storeSpare(bot, state, knowledge))
  out.push(...tossSpare(bot, state, knowledge))
  out.push(...surplusSeeds(bot, state, knowledge))
  // 家にチェストがなく、チェストを持っている（作ったばかり）: 置く。預ける先ができる（docs/design/66）
  if (state.home && state.memory && !homeChests(state.memory, state.home).length && bot.inventory.items().some((i) => i.name === 'chest')) {
    out.push({ id: 'place a chest in the house', verb: 'place_chest', target: 'chest', pos: state.home.inside, distance: dist(bot, state.home.inside) })
  }
  // 地下で松明が足りない: 洞窟の中には松明の材料（石炭と棒）がないことが多い。地上に出て、家で作る
  // （2026-10-03）。材料が手元にあれば、そこで作る手（craft torch）が先に出る
  if (isUnderground(bot) && (inventoryCounts(bot).torch ?? 0) < TORCH_LOW) {
    const [way] = surfaceExits(bot, state)
    if (way) {
      const pos = new Vec3(way.x, way.y, way.z)
      out.push({ id: 'get out to the surface (no torches, and little to make them from underground)', verb: 'surface', target: 'the surface', pos, distance: dist(bot, pos), purpose: 'underground with fewer than 4 torches: torches are made from coal and sticks, which a cave rarely gives; go up and make them at home' })
    }
  }
  const weapon = bestWeapon(bot)
  if (weapon && bot.heldItem?.name !== weapon.name) out.push({ id: `equip ${weapon.name}`, verb: 'equip', target: weapon.name, item: weapon.name, inPlace: true })
  // 体力が危険になってやめた戦い（cleared など）には、目標に関係なく帰る道が要る。まわりに危険が
  // あるときだけ: 回復に要るのは家ではなく食料なので、昼に脅威がなければ食料探しを続ける
  // （ステップごとに家に帰ると、いつまでも食料を見つけられなかった。frun3 以降）
  const danger = reachableThreats(bot, state).length > 0 || dayPhase(bot.time.timeOfDay) !== 'day'
  // 体力が危険で、家が遠い（LOW_HEALTH_WALK）なら、遠い家へ歩かず、その場で穴を掘って塞ぐ（urgent: 聞かずに
  // 選ぶ）。3 回の死亡はどれも、体力 1〜5 で家への長い道を歩いている最中だった（2026-10-01）
  const homeDist = state.home ? bot.entity.position.distanceTo(state.home.inside) : Infinity
  const exposed = bot.health <= HEALTH_CRITICAL && danger && !isInside(bot, state.home) && !inBurrow(bot) && !isUnderground(bot)
  const burrow = exposed && homeDist > LOW_HEALTH_WALK ? burrowSpotCached(bot, state).spot : null
  if (burrow) {
    out.push({ id: 'dig into the ground here and seal yourself in: your health is critical', verb: 'dig_in', target: 'ground', pos: burrow.feet, distance: dist(bot, burrow.feet), urgent: true, effect: 'a 1x1 hole 3 deep, sealed on top with the dirt you dug: monsters cannot reach you; the home is too far to walk with this health' })
  } else if (state.home && bot.health <= HEALTH_CRITICAL && danger && !isInside(bot, state.home) && !homeEntryBlocked(bot, state.home)) {
    out.push({ id: 'go home', verb: 'go_home', target: 'home', inPlace: true, distance: dist(bot, state.home.inside) })
  }
  // 体力が危険で、縦穴が掘れない所（砂浜・石・水のそば）: 持っているブロックで足りるなら、その場を囲む（urgent）
  if (exposed && !burrow && homeDist > LOW_HEALTH_WALK && !isSwimming(bot)) {
    const { site } = campSiteCached(bot, state)
    if (site && campCount(bot) >= site.need) {
      out.push({ id: 'wall yourself in with the blocks you carry: your health is critical', verb: 'wall_in', target: 'camp', pos: site.feet, distance: dist(bot, site.feet), urgent: true, effect: `${site.need} blocks placed around you, roof included: monsters cannot reach you; the home is too far to walk with this health` })
    }
  }
  // 暗い場所を照らすとモブが湧かなくなる。一度照らしたら、もっと先に行くまで再び出さない
  if (isDark(bot) && bot.inventory.items().some((i) => i.name === 'torch') && torchSpot(bot)) {
    out.push({ id: 'place a torch here', verb: 'place_torch', target: 'torch', inPlace: true })
  }
  // 暗い所か深い地下で松明が足りなくなった: 材料（石炭か木炭と棒）があれば、目標に関係なく作れる
  // （1 回で 4 本。needs() が足りないと知らせる）
  const inv = inventoryCounts(bot)
  const torchFuel = Math.min((inv.coal ?? 0) + (inv.charcoal ?? 0), inv.stick ?? 0)
  if ((inv.torch ?? 0) < TORCH_LOW && torchFuel > 0 && (isDark(bot) || isUnderground(bot))) {
    const times = Math.min(torchFuel, 2)
    out.push({ id: `craft torch x${times}`, verb: 'craft', target: 'torch', item: 'torch', times, needsTable: false, inPlace: true, purpose: `only ${inv.torch ?? 0} torches left in the dark: ${times * 4} more` })
  }
  // 朝になっても穴（dig_in）の中にいる: 塞ぎを壊して上がる。塞ぎが開いたままの穴（pathfinder が壊した、
  // など）でも、昼は出る（2026-09-30: 開いた穴の中で、出る手が出ないまま昼になった）
  // 体力が危険で、自然回復できる（満腹度 18 以上）なら、穴の中で回復してから出る
  const healsHere = bot.health <= HEALTH_CRITICAL && bot.food >= REGEN_FOOD
  if (inBurrow(bot) && healsHere) {
    out.push({ id: 'stay in the sealed hole to heal', verb: 'wait', target: 'the hole', inPlace: true, seconds: 10, purpose: `health ${round(bot.health)}/${MAX_HEALTH}: it regenerates while you are sealed in` })
  } else if (dayPhase(bot.time.timeOfDay) === 'day' && !isInside(bot, state.home) && (inBurrow(bot) || inOpenShaft(bot))) {
    out.push({ id: 'climb out of the hole', verb: 'leave_burrow', target: 'the hole', inPlace: true, purpose: 'it is day: dig the seal and climb up to the ground' })
  }
  // 歩いて出られない閉じた所にいる（階段の床が抜けた、深い穴に落ちた。家の中と塞いだ穴は除く）: 足元に土か丸石を積んで
  // 出る。昼は聞かずに出る（urgent）。夜は安全な地下で朝を待てるので、選べる手にとどめる
  const shut = !isInside(bot, state.home) && !inBurrow(bot) ? closedSpaceCached(bot) : null
  if (shut && fillItem(bot)) {
    out.push({ id: 'climb out of the closed space by piling up dirt or cobblestone', verb: 'climb_out', target: 'the closed space', inPlace: true, cells: shut.cells, ...((bot.entity.isInWater || (dayPhase(bot.time.timeOfDay) === 'day' && shut.cells <= SHUT_IN_SMALL)) && !(state.climbBlockedUntil > Date.now()) ? { urgent: true } : {}), purpose: `you cannot walk out of here (${shut.cells} cells you can reach): pile up what you carry under your feet until you can; no pickaxe is needed` })
  }
  // 積む土も丸石もない（素手の始まり）か、積んで出るのに失敗したあと: 壁を掘って歩いて出る（docs/design/90 §5.1）。
  // 水が入った小部屋の水の中では、夜も聞かずに実行する（climb_out と同じ）
  if (shut && (!fillItem(bot) || state.climbBlockedUntil > Date.now())) {
    const route = digOutRoute(bot, state, regionCached(bot))
    if (!route.why) {
      out.push({ id: 'dig out of the closed space through the wall', verb: 'dig_out', target: 'the closed space', inPlace: true, cells: shut.cells, blocks: route.cells.length, ...((bot.entity.isInWater || (dayPhase(bot.time.timeOfDay) === 'day' && shut.cells <= SHUT_IN_SMALL)) && !(state.digOutBlockedUntil > Date.now()) ? { urgent: true } : {}), purpose: `you cannot walk out of here (${shut.cells} cells you can reach) and carry no dirt or cobblestone to pile up: dig through the wall (${route.cells.length} blocks, bare hands are enough) and walk out` })
    }
  }
  // 1 本の柱の頂上にいる（自分で積んだ塔。まわりは崖で、積んでも出られない）: 足元を掘って降りる。昼は聞かずに降りる
  const pillar = !shut && !isInside(bot, state.home) && !inBurrow(bot) && isolatedSpaceCached(bot) ? pillarUnder(bot) : null
  if (pillar) {
    out.push({ id: 'climb down the pillar you stand on by digging it away', verb: 'climb_down', target: 'the pillar', inPlace: true, ...(dayPhase(bot.time.timeOfDay) === 'day' && !(state.descendBlockedUntil > Date.now()) ? { urgent: true } : {}), purpose: 'you stand on a free-standing pillar with a drop on every side: piling up more does not help; dig it away one block at a time and drop down (the blocks come back to you)' })
  }
  // つるはしが壊れて（10 分以内）、どのつるはしも持っていない: 家に作りに戻る道を出す（作業台とチェストがある）。手元の
  // 材料で作れるときは、goals の「作る」手が先に出る。石は素手では何も落ちないので、持たずに掘り進めない
  const lost = toReplace(bot, state).find((b) => b.kind === 'pickaxe')
  if (lost && state.home && !isInside(bot, state.home) && dist(bot, state.home.inside) > LOST_TOOL_HOME) {
    out.push({ id: `go home to make a new pickaxe (the ${lost.item} broke)`, verb: 'go_home', target: 'home', inPlace: true, distance: round(dist(bot, state.home.inside)), purpose: 'stone and ore drop nothing without a pickaxe: the crafting table and the chest are at home' })
  }
  const hole = state.home?.breach[0]
  if (hole) out.push({ id: 'close the hole in the house wall', verb: 'repair_wall', target: 'house wall', inPlace: true, at: fmt(hole) })
  // 死んで落とした持ち物（5 分で消える）: 目標に関係なく、真っ先に取りに戻る（設計書 50 §17）
  const death = recoverableDeath(state.memory, Number(bot.time.age))
  if (death && recoverTripOk(bot, death)) {
    const pos = new Vec3(death.x, death.y, death.z)
    // id に距離や経過時間を入れない: 歩くたびに変わり、選んだ id が実行のときに「今の提案にない」と断られた
    out.push({ id: `recover your things where you died at ${fmt(pos)}`, verb: 'recover_items', target: 'items', pos, distance: round(dist(bot, pos)), direction: bearing(bot.entity.position, pos), minutes_ago: Math.round((Number(bot.time.age) - death.seen) / 1200 * 10) / 10 })
  }
  // 掘った跡に水が流れ込んでいる（畑の縁の原木を掘って水が出た、など）: 同じ物か、土・丸石で埋める
  // （docs/design/55。id は位置と掘った物だけ: 歩いても変わらない）
  const leak = leakingHoles(bot, state, Number(bot.time.age), (p) => !!waterAt(state, p)).sort((a, b) => dist(bot, a.pos) - dist(bot, b.pos))[0]
  const plug = leak && (bot.inventory.items().find((i) => i.name === leak.d.name) ?? fillItem(bot))
  if (leak && plug) {
    out.push({ id: `plug the hole at ${fmt(leak.pos)} where ${leak.d.name} was dug: water runs into it`, verb: 'plug_hole', target: 'hole', item: plug.name, pos: leak.pos, distance: round(dist(bot, leak.pos)) })
  }
  // ベッドで寝ているモブ（村の家のベッドは村人が使っていて、寝ている間は寝られない）: 夜だけ、1 回殴って
  // 起こす手を出す（docs/design/56。attack は倒すまで戦うので、別の手）
  if (dayPhase(bot.time.timeOfDay) !== 'day') {
    for (const { e, dist: d, bed } of sleepersInBeds(bot)) {
      out.push({ id: `wake up the sleeping ${e.name} #${e.id} in the bed at ${fmt(bed)} (one hit)`, verb: 'wake_sleeper', target: e.name, entityId: e.id, pos: e.position, distance: round(d), effect: 'it leaves the bed: the bed is free to sleep in' })
    }
  }
  for (const { e, dist } of nearbyDrops(bot, state, DROP_RADIUS).slice(0, DROPS_OFFERED)) {
    const item = e.getDroppedItem()?.name ?? 'item'
    out.push({ id: `pick up ${item} #${e.id}`, verb: 'pickup', target: item, distance: round(dist), direction: bearing(bot.entity.position, e.position), pos: e.position, entityId: e.id })
  }
  return out
}

// 地下で死んだ場所へ戻るのは、武器と行きの松明があるときだけ（死んだあとの持ち物は空で、素手のまま
// 前の死に場所へ潜って 7 回続けて死んだ: 2026-10-01。docs/design/80_underground_deaths.md）
function recoverTripOk (bot, death) {
  // 夕方と夜は取りに行かない（復活した直後は素手で、死んだ場所には殺したモブが残っている。2026-10-07 に夜の外で死んだ
  // 3 秒後に取りに出て、同じ場所の手前でまた死んだ。落とし物は 5 分で消えるので、夜に死ねば諦めることになる。設計書 105）
  const phase = dayPhase(bot.time.timeOfDay)
  if (phase === 'dusk' || phase === 'night') return false
  if (death.y >= UNDERGROUND_BELOW) return true
  const items = bot.inventory?.items?.() ?? []
  const armed = items.some((i) => /_(sword|axe)$/.test(i.name))
  const torches = items.filter((i) => i.name === 'torch').reduce((sum, i) => sum + i.count, 0)
  return armed && torches >= torchesNeeded(dist(bot, new Vec3(death.x, death.y, death.z)))
}

// 前に見た場所が地下なら、行きの松明があるときだけ。水は地下の水に行かない（桶の水を洞窟の水に
// 汲みに行って、暗い洞窟で 2,332 マスの中をさまよって死んだ: 2026-10-01）
function recallTripOk (bot, place) {
  if (place.y >= UNDERGROUND_BELOW) return true
  if (place.kind === 'water') return false
  const torches = (bot.inventory?.items?.() ?? []).filter((i) => i.name === 'torch').reduce((sum, i) => sum + i.count, 0)
  return torches >= torchesNeeded(place.distance)
}

function awayFrom (bot, start, dx, dz) {
  const vx = bot.entity.position.x - start.x
  const vz = bot.entity.position.z - start.z
  return Math.hypot(vx, vz) < 1 || vx * dx + vz * dz >= 0
}

// 空きのある一番近いチェスト（家のもの）にアイテムを入れる
function toChest (bot, state, item, count) {
  const [chest] = roomyChests(bot, state)
  if (!chest) return []
  const pos = new Vec3(chest.x, chest.y, chest.z)
  return [{ id: `put ${count} ${item} in the chest at ${fmt(pos)}`, verb: 'deposit', target: item, item, count, pos, distance: dist(bot, pos) }]
}

// 家の中にいて、今使わない物を持っているなら、チェストに戻す（2026-10-01、user: 「採取したものや作ったものは
// 使わない時は基本チェストに戻す。手に持ったまま死んでずっと進まない」: 死ぬと持ち物は全部落ちて 5 分で消える）。
// 残すのは、身を守るもの・道具・食べ物・松明とその材料・置くもの・種と苗木、目標が使う物（目標の材料の木
// まで: 目標の途中で預けて取り出す往復を作らない）、土と丸石の少し（足場と穴埋め）。預ける先がいっぱいで
// 失敗したら、しばらく出さない（tidyBlockedUntil）
export const TIDY_MIN_ITEMS = 1
const TIDY_RESERVE = { dirt: 16, cobblestone: 16 }
const TIDY_BLOCKED_TICKS = 5 * 60 * 20
// いつも持つのは、安くて、どこでも使う物だけ。種・苗木・クワ・バケツ・骨粉は、目標が使うとき（畑や植林場の
// 目標、水のバケツの目標）だけ残す（goalNeeds）: 地下の採掘へ種 60 個を持って行って、死んで全部失った
// （2026-10-03、user: 「いわんこっちゃない」）
const TIDY_KEEP = /_(pickaxe|shovel)$|^(shears|fishing_rod|flint_and_steel|coal|charcoal|stick|chest|crafting_table|furnace|blast_furnace|smoker)$|boat$|bed$/
// 家の外では、地下へ出る直前（目標の次の手が鉱山・掘り下げ）に家のチェストがこの距離以内なら、預けに戻る
export const TIDY_NEAR_HOME = 32
// 価値の高い物: 家に戻ったら預けるだけでなく、遠くからでも戻って預ける（個数に関わらない）
export const VALUABLE = /^(diamond|emerald|raw_gold|gold_ingot|gold_block|diamond_block|emerald_block|ancient_debris|netherite_(ingot|scrap|block)|nether_star|elytra|totem_of_undying|enchanted_golden_apple|trident|heart_of_the_sea|enchanted_book)$/
// 持ち物が多い: 空きスロットがこれ以下で、預けると TIDY_HEAVY_SLOTS 以上空くとき
export const TIDY_HEAVY_FREE = 6
export const TIDY_HEAVY_SLOTS = 4

// 畑・植林場の目標が使う物
function farmingKeep (state, spec) {
  const out = new Set()
  const crops = []
  let trees = false
  if (spec.predicate === 'facility') {
    const layout = state.facilities?.[spec.name]?.layout
    if (layout?.crop) crops.push(layout.crop)
    if (layout?.sapling) trees = true
    if (!layout) { crops.push('wheat'); trees = true }
  } else if (spec.predicate === 'farmed') crops.push(spec.item ?? 'wheat')
  else if (spec.predicate === 'planted') trees = true
  else return out
  for (const crop of crops) {
    const seed = CROPS[crop]?.seed
    if (seed) out.add(seed)
    for (const n of ['bone_meal', 'water_bucket', 'bucket', 'stone_hoe', 'wooden_hoe', 'iron_hoe', 'diamond_hoe', 'golden_hoe', 'netherite_hoe']) out.add(n)
  }
  if (trees) for (const n of ['bone_meal', 'oak_sapling', 'birch_sapling', 'spruce_sapling', 'jungle_sapling', 'acacia_sapling', 'dark_oak_sapling', 'cherry_sapling']) out.add(n)
  return out
}

// 目標が使う物（品物と、その材料の木。束の残りも）
function goalNeeds (knowledge, state, world) {
  const keep = new Set()
  for (const spec of [state.goal?.spec, ...(state.goal?.spec?.together ?? [])].filter(Boolean)) {
    for (const name of goalKeep(knowledge, spec)) keep.add(name)
    if (spec.item && world) {
      try { for (const name of materialsOf(knowledge, world, spec.item).all) keep.add(name) } catch { /* 作り方が分からない物は材料を見ない */ }
    }
  }
  // 畑・植林場の目標は、種・苗木・クワ・水・骨粉を使う（施設は設計の作物と苗木）
  for (const spec of [state.goal?.spec, ...(state.goal?.spec?.together ?? [])].filter(Boolean)) {
    for (const name of farmingKeep(state, spec)) keep.add(name)
  }
  // 材料の木はどの種類でもよい（ソルバーが選んだ 1 種類だけ残すと、持っている別の板材を預けて、
  // すぐ棒のために取り出す往復になった: 2026-10-01、松明のための oak_planks）
  for (const group of ['planks', 'log']) {
    if ([...keep].some((n) => knowledge.resolve(group).members.includes(n))) for (const n of knowledge.resolve(group).members) keep.add(n)
  }
  return keep
}

// チェストから出した物は、しばらく預け直さない（かぼちゃを出した次の手で、預ける手が戻した: 2026-10-01）
export function recentlyTaken (state, bot, name) {
  return Number(bot.time.age) - (state.withdrawn?.[name] ?? -Infinity) < TIDY_BLOCKED_TICKS
}

// 回復の手段がない（体力が危険、満腹度が足りない、食べ物がない）まま家にいる: 持ち物を、武器・防具・松明と
// 目標が使う物だけにして、あとは家のチェストへ。死んでも失うのは安い物だけで、外で動ける（2026-10-01、user:
// 「これ持ち物がなければデス時のリスクはほぼ 0 だと捉えられるようにしたい」）。tidyUp と違い、道具・種・
// 棒なども預ける（目標が使う物は残す）。預けたあとは出さない（残りが無い）。預けられなかったら、しばらく出さない
export function stashWhenNothingHeals (bot, state, knowledge, world) {
  if (!knowledge || !state.home || !state.memory || !isInside(bot, state.home) || !cannotHeal(bot)) return []
  if (Number(bot.time.age) < (state.tidyBlockedUntil ?? 0)) return []
  const [chest] = roomyChests(bot, state)
  if (!chest) return []
  const keep = goalNeeds(knowledge, state, world)
  const items = Object.entries(inventoryCounts(bot))
    .filter(([name, n]) => n > 0 && !KEEP_WHEN_FIGHTING.test(name) && !keep.has(name) && !recentlyTaken(state, bot, name))
    .map(([item, count]) => ({ item, count }))
  if (!items.length) return []
  const pos = new Vec3(chest.x, chest.y, chest.z)
  return [{
    id: "put everything but the weapon, armor, torches and what the goal uses in the home's chest",
    verb: 'stash', target: 'chest', pos, items, distance: dist(bot, pos), urgent: true,
    purpose: `nothing can heal you now (health ${round(bot.health)}/${MAX_HEALTH}, food ${bot.food}, no food carried): a death drops everything you carry`
  }]
}

function tidyUp (bot, state, knowledge, world, status) {
  if (!knowledge || !state.home || !state.memory) return []
  if (Number(bot.time.age) < (state.tidyBlockedUntil ?? 0)) return []
  const [chest] = roomyChests(bot, state)
  if (!chest) return []
  const food = new Set(knowledge.resolve('food').members)
  const tidyReserve = mineInProgress(state) ? { ...TIDY_RESERVE, ...MINING_RESERVE } : TIDY_RESERVE // 枝掘りの間は、塞ぐ丸石を預けない
  const surplus = new Map(seedsToToss(bot, state, goalKeep(knowledge, state.goal?.spec)).map((s) => [s.item, s.count])) // 余った種は預けず捨てる
  const stowable = Object.entries(inventoryCounts(bot))
    .filter(([name, n]) => n > 0 && !KEEP_WHEN_FIGHTING.test(name) && !TIDY_KEEP.test(name) && !food.has(name) && !recentlyTaken(state, bot, name))
    .map(([item, n]) => ({ item, count: n - (tidyReserve[item] ?? 0) - (surplus.get(item) ?? 0) }))
    .filter((s) => s.count > 0)
  if (stowable.reduce((sum, s) => sum + s.count, 0) < TIDY_MIN_ITEMS) return []
  const keep = goalNeeds(knowledge, state, world)
  const stow = stowable.filter((s) => !keep.has(s.item))
  if (stow.reduce((sum, s) => sum + s.count, 0) < TIDY_MIN_ITEMS) return []
  const pos = new Vec3(chest.x, chest.y, chest.z)
  // 家の中なら、個数に関わらずすぐ（ダイヤ 1 個でも持ち歩かない: 2026-10-03）。外では、集めてもすぐには戻らないが、
  // 価値の高い物（VALUABLE）を持っている・持ち物が多い（空きが少なく、預けると空きが増える）ときは、家から
  // 遠くても（地下からでも）一度戻って預ける。地下へ出る直前に家のチェストが近ければ、先に預ける
  let why = ''
  if (!isInside(bot, state.home)) {
    if (!safeToGoHome(bot, state)) return []
    const valuables = stow.filter((s) => VALUABLE.test(s.item)).map((s) => s.item)
    const freed = stow.reduce((sum, s) => sum + Math.ceil(s.count / (bot.registry.itemsByName[s.item]?.stackSize ?? 64)), 0)
    const down = (status?.leaves ?? []).some((l) => UNDERGROUND_LEAVES.has(l.kind))
    if (valuables.length) why = `you carry ${valuables.join(', ')}: a death drops it and it is gone in 5 minutes, so go home and put it in the chest first`
    else if (emptySlots(bot) <= TIDY_HEAVY_FREE && freed >= TIDY_HEAVY_SLOTS) why = `your inventory is nearly full (${emptySlots(bot)} free slots) of things the goal does not need: go home and put them in the chest`
    else if (down && !isUnderground(bot) && bot.entity.position.distanceTo(pos) <= TIDY_NEAR_HOME) why = 'you are about to go underground: put what the goal does not need in the chest first, a death drops everything you carry'
    else return []
  }
  return [{ id: "put the things you do not need now in the home's chest", verb: 'tidy', target: 'chest', pos, stow, kinds: stow.length, distance: dist(bot, pos), purpose: why || 'dying drops everything you carry, and it is gone in 5 minutes: keep only what the goal, your defence and your food need' }]
}

// 家のチェストが複数あって、散らばっているか半端なスタックがあれば、まとめる（storage.mjs）。家の中で、
// 前にまとめたあと中身が変わっていなければ出さない（2026-10-04, user: 「チェストが複数になったらデフラグできるようにしたい」）
function defragUp (bot, state) {
  if (!state.home || !state.memory || !isInside(bot, state.home)) return []
  if (state.defragBlockedUntil && Number(bot.time.age) < state.defragBlockedUntil) return []
  const use = storageUsage(bot, state)
  if (use.chests < 2 || (!use.spread && use.saving < 1)) return []
  if (state.defragSig === contentsSignature(state) || emptySlots(bot) < 2) return []
  const [first] = homeChests(state.memory, state.home)
  const pos = new Vec3(first.x, first.y, first.z)
  return [{
    id: "put the home's chests in order (merge half-empty stacks, keep each kind in one chest)",
    verb: 'defrag',
    target: 'chest',
    pos,
    distance: dist(bot, pos),
    purpose: `${use.chests} chests; ${use.used}/${use.size} slots used, sorting them would free ${use.saving}`
  }]
}

// 家へ戻る道中に出てよいか: 体力・満腹度に余裕があり、近くに敵がいなくて、乗り物や水の中でない
function safeToGoHome (bot, state) {
  return bot.health > HEALTH_CRITICAL && bot.food > HUNGER_URGENT && !riding(bot) && !isSwimming(bot) && !reachableThreats(bot, state).length
}

// 家の中でインベントリがいっぱい: 目標に要らない一番大きいスタックをチェストに入れてよい
const FREE_SLOTS_WANTED = 8
const STORE_OFFERED = 3
const TOOL = /_(sword|pickaxe|axe|shovel|hoe)$/

function storeSpare (bot, state, knowledge) {
  if (!knowledge || !state.memory || !homeChests(state.memory, state.home).length || !isInside(bot, state.home)) return []
  if (bot.inventory.emptySlotCount() >= FREE_SLOTS_WANTED) return []
  const keep = goalKeep(knowledge, state.goal?.spec)
  const counts = {}
  for (const i of bot.inventory.items()) counts[i.name] = (counts[i.name] ?? 0) + i.count
  const sealing = mineInProgress(state) ? MINING_RESERVE : {} // 枝掘りの間は、塞ぐ丸石を預けない（超えた分だけ）
  return Object.entries(counts)
    .map(([name, n]) => [name, n - (sealing[name] ?? 0)])
    .filter(([name, n]) => n > 0 && !keep.has(name) && !TOOL.test(name) && !['chest', 'crafting_table'].includes(name))
    .sort((a, b) => b[1] - a[1])
    .slice(0, STORE_OFFERED)
    .flatMap(([name, n]) => toChest(bot, state, name, n))
}

// 種の余り（seeds.mjs）: 持ち物の残す数を超えた分は、街から離れた所で捨てる。余りがあるなら、チェストの種は、家の中なら
// 出しに行く（出せば持ち物の超えた分になり、次に捨てる）。目標が種を集めているときは出さない
function surplusSeeds (bot, state, knowledge) {
  if (!knowledge) return []
  const keep = goalKeep(knowledge, state.goal?.spec)
  const out = []
  const toss = seedsToToss(bot, state, keep)
  const away = toss.length ? seedToss(bot, state, toss) : null
  if (away) {
    out.push({ id: `throw away the surplus ${toss.map((s) => s.item).join(', ')}`, ...away, purpose: 'the farms need one stack of seeds each (in the inventory and the chests together): the rest is thrown away, far from the town (a pile at your feet is picked up again)' })
  }
  const chest = state.memory && state.home ? homeChests(state.memory, state.home).find((c) => seedsToTake(bot, state, keep).some((t) => (c.contents?.[t.item] ?? 0) > 0)) : null
  const take = seedsToTake(bot, state, keep).find((t) => (chest?.contents?.[t.item] ?? 0) > 0)
  if (chest && take && isInside(bot, state.home) && emptySlots(bot) > 0) {
    const stack = bot.registry?.itemsByName?.[take.item]?.stackSize ?? 64
    const count = Math.min(take.count, chest.contents[take.item], emptySlots(bot) * stack)
    const pos = new Vec3(chest.x, chest.y, chest.z)
    out.push({ id: `take ${count} ${take.item} from the chest at ${fmt(pos)} (what is over one stack per farm is then thrown away)`, verb: 'withdraw', target: take.item, item: take.item, count, pos, distance: dist(bot, pos), purpose: 'there are more seeds than the farms need (one stack each, in the inventory and the chests together): take them out to throw the rest away' })
  }
  return out
}

// 持ち物がほぼいっぱい（空き 0〜1）で、余っている物（土・丸石の残す数を超えた分、花崗岩、花など）があれば、
// 捨てて空ける。家のチェストがなくても、外にいても、その場でできる。空きがなくなると、落とした肉も拾えず
// 食料が集まらない（docs/design/66）。id に数を入れない（捨てるたびに変わり、選んだ id が「今の提案にない」と
// 断られる）
function tossSpare (bot, state, knowledge) {
  if (!knowledge || emptySlots(bot) >= TOSS_BELOW) return []
  // 家の中で、家にチェストがあれば預ける（storeSpare）。捨てるのは、預けられないとき
  if (state.memory && homeChests(state.memory, state.home).length && isInside(bot, state.home)) return []
  const items = spareStacks(bot, goalKeep(knowledge, state.goal?.spec), reserveFor(state))
  if (!items.length) return []
  return [{ id: 'throw away the spare items to free inventory slots', verb: 'toss', target: 'items', inPlace: true, items, purpose: 'the inventory is full: dropped items and drops cannot be picked up' }]
}

// 選ぶ側に見せる候補の中身（位置のオブジェクトやエンティティの参照は除く）
export function describe (c) {
  const { id, pos, entityId, inPlace, inside, dx, dz, block, needsTable, item, spot, confront, site, stow, yaw, ...rest } = c
  return rest
}
