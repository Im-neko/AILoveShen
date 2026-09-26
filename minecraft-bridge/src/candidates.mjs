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
import { plantSpot, tillSpot, sowSpot } from './farming.mjs'
import { round, bearing, dayPhase, burningInDaylight, isDark, inventoryCounts } from './observe.mjs'
import { isInside, exitSpots, shelterOf } from './home.mjs'
import { recall, visited, homeChests, chestWith, furnaceWith, frontierDistance } from './memory.mjs'
const FRONTIER_SLACK = 8
import { cooking, isRawMeat } from './cooking.mjs'
import { reachableThreats, bestWeapon, nearbyDrops, findTable, findFurnace, torchSpot, stationSpot, stationSpots, HEALTH_CRITICAL, HUNGER_URGENT, EXPLORE_DISTANCE } from './primitives.mjs'

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
  // also の印（ほかの中目標のため、狩りのための剣）は、その葉から出た候補すべてに付ける
  for (const leaf of status?.leaves ?? []) {
    out.push(...fromLeaf(bot, state, world, leaf).map((c) => leaf.also && !c.also ? { ...c, also: leaf.also } : c))
  }
  out.push(...forNeeds(bot, state, knowledge))
  const home = state.home
  const { inside, day, danger, sheltering } = shelterOf(bot, home)
  // 同じ id なら先のものを残す: 目標の候補は目標が許すこと（confront）を持っている
  const unique = [...out.reduce((m, c) => m.has(c.id) ? m : m.set(c.id, c), new Map()).values()]
  const safe = sheltering ? unique.filter((c) => c.confront || !needsOutside(c, home)) : unique
  const held = unique.length - safe.length
  let withheld = null
  if (held && !day) withheld = `staying inside for the night: ${held} actions outside are held back until morning`
  if (held && day) {
    withheld = `staying inside while ${danger.map(({ e }) => e.name).join(', ')} wait near the door: ${held} actions outside are held back ` +
      '(cleared() goes out to fight them; an exit can be dug through another wall)'
    if (!home.breach.length) {
      for (const spot of exitSpots(bot, home, danger.map(({ e }) => e))) {
        safe.push({ id: `exit through the ${spot.side} wall`, verb: 'exit_wall', target: `${spot.side} wall`, inPlace: true, spot, nearest_hostile: round(spot.distance) })
      }
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
  safe.push(...(inside ? waitsInside(bot, danger) : []))
  if (!safe.length) {
    safe.push({ id: inside ? 'wait inside' : 'wait', verb: 'wait', target: inside ? 'inside the house' : 'here', inPlace: true, inside, seconds: 10, purpose: 'nothing else can be done now' })
  }
  return { candidates: safe, withheld }
}

// 避難の判定は home.mjs（作業台の置き場所も使う）。道具（tools.mjs）はここから import する
export { shelterOf }

// 自然回復には満腹度がほぼ満タンである必要がある
const REGEN_FOOD = 18
const MAX_HEALTH = 20

function waitsInside (bot, danger) {
  const wait = (id, purpose) => ({ id, verb: 'wait', target: 'inside the house', inPlace: true, inside: true, seconds: 10, purpose })
  const out = []
  if (dayPhase(bot.time.timeOfDay) !== 'day') out.push(wait('wait inside until morning', 'the night passes'))
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
      const lookingFor = leaf.sources.length ? leaf.sources.join('/') : leaf.item
      const me = bot.entity.position
      // 前に見た場所を先に出す。次に方角を、すでに行った場所には印をつけて出す
      const recalled = state.memory
        ? recall(state.memory, leaf.sources, me, Number(bot.time.age)).map((p) => ({
          id: `go to ${p.kind} seen at ${p.x},${p.z}`, verb: 'goto_memory', target: p.kind, pos: new Vec3(p.x, p.y, p.z),
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
    case 'place':
      // 置ける場所でだけ出す（何も置けない場所で何度も選ばれた）。家の中と外の両方を出し、選ぶ側が
      // 決める（避難中は家の中だけ）
      return placeStation(bot, state, leaf.item)
    case 'light':
      return [{ id: `place a torch at ${fmt(leaf.pos)} (dark ground)`, verb: 'place_torch_at', target: 'torch', pos: leaf.pos, distance: dist(bot, leaf.pos) }]
    case 'smelt': {
      if (!leaf.count) {
        // 精錬に置いてきた場所。どれだけ遠くても出す（ソルバーはそこから来るものとして数えた）
        const f = state.memory && furnaceWith(state.memory, leaf.item, bot.entity.position)
        if (!f) return []
        const pos = new Vec3(f.x, f.y, f.z)
        return [{ id: `take ${leaf.item} from the furnace at ${fmt(pos)}`, verb: 'smelt', target: leaf.item, item: leaf.item, pos, distance: dist(bot, pos) }]
      }
      const furnace = findFurnace(bot)
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
    case 'stay_underground':
      return [{ id: 'keep working underground through the night', verb: 'wait', target: 'here', inPlace: true, seconds: 10, purpose: 'underground it is as dark by day as by night: the night outside does not matter here' }]
    case 'plant': {
      // 植林（設計書 33）: 家から 6〜24 m の、ほかの木から離れた地面。id に場所は入れない
      // （/act は候補を作り直して id で探す: 場所の選び直しで id が変わると動かない）
      const p = plantSpot(bot, state)
      return p ? [{ id: `plant ${leaf.item} near the home`, verb: 'plant', target: leaf.item, item: leaf.item, pos: p, distance: dist(bot, p) }] : []
    }
    case 'till': {
      const p = tillSpot(bot, state)
      return p ? [{ id: 'till the ground near the home with a hoe', verb: 'till', target: 'farmland', pos: p, distance: dist(bot, p) }] : []
    }
    case 'sow': {
      const p = sowSpot(bot, state)
      return p ? [{ id: `sow ${leaf.item} on the farmland near the home`, verb: 'sow', target: leaf.item, item: leaf.item, pos: p, distance: dist(bot, p) }] : []
    }
    default:
      return []
  }
}

function forNeeds (bot, state, knowledge) {
  const out = []
  for (const { e, dist } of reachableThreats(bot, state).slice(0, THREATS_OFFERED)) {
    const weapon = bestWeapon(bot)?.name ?? 'none (fist)'
    out.push({ id: `attack ${e.name} #${e.id}`, verb: 'attack', target: e.name, distance: round(dist), pos: e.position, entityId: e.id, hostile: true, weapon })
    out.push({ id: `flee from ${e.name} #${e.id}`, verb: 'flee', target: e.name, distance: round(dist), entityId: e.id, inPlace: false, pos: e.position })
  }
  if (bot.food < 20) {
    // 食料の目標が食料と数えるものだけ: 腐った肉などは害がある
    const edible = new Set(knowledge.resolve('food').members)
    const foods = bot.inventory.items().filter((i) => edible.has(i.name))
    foods.sort((a, b) => bot.registry.foodsByName[b.name].foodPoints - bot.registry.foodsByName[a.name].foodPoints)
    // 焼いた物を先に。生肉しかないときは、焼ける（かまどと燃料がある）なら焼くまで待つ。食べるのは
    // 飢えそうなとき（HUNGER_URGENT）か、焼く手段がなくてかなり空腹のとき（RAW_OK）だけ
    const raw = (i) => isRawMeat(knowledge, i.name)
    let best = foods.find((i) => !raw(i)) ?? null
    if (!best && foods.length) {
      const canCook = !!cooking(bot, knowledge, state)
      if (bot.food <= HUNGER_URGENT || (!canCook && bot.food <= RAW_OK)) best = foods[0]
    }
    const lastResort = bot.food <= HUNGER_URGENT && bot.inventory.items().find((i) => LAST_RESORT_FOOD.includes(i.name))
    const eat = best ?? lastResort
    if (eat) out.push({ id: `eat ${eat.name}`, verb: 'eat', target: eat.name, item: eat.name, inPlace: true })
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
  const weapon = bestWeapon(bot)
  if (weapon && bot.heldItem?.name !== weapon.name) out.push({ id: `equip ${weapon.name}`, verb: 'equip', target: weapon.name, item: weapon.name, inPlace: true })
  // 体力が危険になってやめた戦い（cleared など）には、目標に関係なく帰る道が要る。まわりに危険が
  // あるときだけ: 回復に要るのは家ではなく食料なので、昼に脅威がなければ食料探しを続ける
  // （ステップごとに家に帰ると、いつまでも食料を見つけられなかった。frun3 以降）
  const danger = reachableThreats(bot, state).length > 0 || dayPhase(bot.time.timeOfDay) !== 'day'
  if (state.home && bot.health <= HEALTH_CRITICAL && danger && !isInside(bot, state.home)) {
    out.push({ id: 'go home', verb: 'go_home', target: 'home', inPlace: true, distance: dist(bot, state.home.inside) })
  }
  // 暗い場所を照らすとモブが湧かなくなる。一度照らしたら、もっと先に行くまで再び出さない
  if (isDark(bot) && bot.inventory.items().some((i) => i.name === 'torch') && torchSpot(bot)) {
    out.push({ id: 'place a torch here', verb: 'place_torch', target: 'torch', inPlace: true })
  }
  const hole = state.home?.breach[0]
  if (hole) out.push({ id: 'close the hole in the house wall', verb: 'repair_wall', target: 'house wall', inPlace: true, at: fmt(hole) })
  for (const { e, dist } of nearbyDrops(bot, state, DROP_RADIUS).slice(0, DROPS_OFFERED)) {
    const item = e.getDroppedItem()?.name ?? 'item'
    out.push({ id: `pick up ${item} #${e.id}`, verb: 'pickup', target: item, distance: round(dist), direction: bearing(bot.entity.position, e.position), pos: e.position, entityId: e.id })
  }
  return out
}

function awayFrom (bot, start, dx, dz) {
  const vx = bot.entity.position.x - start.x
  const vz = bot.entity.position.z - start.z
  return Math.hypot(vx, vz) < 1 || vx * dx + vz * dz >= 0
}

// 一番近いチェスト（家のもの）にアイテムを入れる
function toChest (bot, state, item, count) {
  const chest = state.memory && homeChests(state.memory, state.home).sort((a, b) => dist(bot, new Vec3(a.x, a.y, a.z)) - dist(bot, new Vec3(b.x, b.y, b.z)))[0]
  if (!chest) return []
  const pos = new Vec3(chest.x, chest.y, chest.z)
  return [{ id: `put ${count} ${item} in the chest at ${fmt(pos)}`, verb: 'deposit', target: item, item, count, pos, distance: dist(bot, pos) }]
}

// 家の中でインベントリがいっぱい: 目標に要らない一番大きいスタックをチェストに入れてよい
const FREE_SLOTS_WANTED = 8
const STORE_OFFERED = 3
const TOOL = /_(sword|pickaxe|axe|shovel|hoe)$/
const GOAL_GROUPS = { built: ['log', 'planks', 'door'], placed: ['bed', 'wool', 'planks'] }

function storeSpare (bot, state, knowledge) {
  if (!knowledge || !state.memory || !homeChests(state.memory, state.home).length || !isInside(bot, state.home)) return []
  if (bot.inventory.emptySlotCount() >= FREE_SLOTS_WANTED) return []
  const spec = state.goal?.spec
  const groups = ['food', ...(GOAL_GROUPS[spec?.predicate] ?? []), ...(spec?.item ? [spec.item] : [])]
  const keep = new Set(groups.flatMap((g) => knowledge.resolve(g).members))
  const counts = {}
  for (const i of bot.inventory.items()) counts[i.name] = (counts[i.name] ?? 0) + i.count
  return Object.entries(counts)
    .filter(([name]) => !keep.has(name) && !TOOL.test(name) && !['chest', 'crafting_table'].includes(name))
    .sort((a, b) => b[1] - a[1])
    .slice(0, STORE_OFFERED)
    .flatMap(([name, n]) => toChest(bot, state, name, n))
}

// 選ぶ側に見せる候補の中身（位置のオブジェクトやエンティティの参照は除く）
export function describe (c) {
  const { id, pos, entityId, inPlace, inside, dx, dz, block, needsTable, item, spot, confront, site, ...rest } = c
  return rest
}
