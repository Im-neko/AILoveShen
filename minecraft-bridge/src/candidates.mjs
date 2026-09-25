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
import { round, bearing, dayPhase, burningInDaylight, isDark, inventoryCounts } from './observe.mjs'
import { isInside, dangerOutside, exitSpots } from './home.mjs'
import { recall, visited, homeChests, chestWith, furnaceWith } from './memory.mjs'
import { cooking } from './cooking.mjs'
import { reachableThreats, bestWeapon, nearbyDrops, findTable, findFurnace, torchSpot, stationSpot, HEALTH_CRITICAL, HUNGER_URGENT, EXPLORE_DISTANCE } from './primitives.mjs'

const { Vec3 } = vec3Pkg
const DROP_RADIUS = 16
const DROPS_OFFERED = 3
const THREATS_OFFERED = 2
const EXPLORE_DIRECTIONS = { north: [0, -1], east: [1, 0], south: [0, 1], west: [-1, 0] }
// 飢えていて、ほかによい食料を持っていないときだけ食べる: 満腹度 4 と引き換えに、たいてい短い
// 空腹の効果を受ける（frun2: 腐った肉を持ったまま飢えた）
const LAST_RESORT_FOOD = ['rotten_flesh']

const fmt = (p) => `${p.x},${p.y},${p.z}`
const dist = (bot, p) => round(bot.entity.position.distanceTo(p))

// { candidates, withheld }: withheld は外したものがあればその内容（避難の規則、ここに置く場所が
// ない作業台やかまど）
export function ground (bot, state, knowledge, world, status) {
  const out = []
  for (const leaf of status?.leaves ?? []) out.push(...fromLeaf(bot, state, world, leaf))
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
  if (station && !stationSpot(bot, state)) {
    const room = `no room here to place the ${station.item}: move to flat open ground`
    withheld = withheld ? `${withheld}; ${room}` : room
  }
  safe.push(...(inside ? waitsInside(bot, danger) : []))
  if (!safe.length) {
    safe.push({ id: inside ? 'wait inside' : 'wait', verb: 'wait', target: inside ? 'inside the house' : 'here', inPlace: true, inside, seconds: 10, purpose: 'nothing else can be done now' })
  }
  return { candidates: safe, withheld }
}

// 家に避難しているか: 家の中にいて、夜か、ドアの前に敵対モブがいる。避難中は外での行動を出さない
// （候補）・断る（道具、設計書 21）
export function shelterOf (bot, home) {
  const inside = isInside(bot, home)
  const day = dayPhase(bot.time.timeOfDay) === 'day'
  const danger = dangerOutside(bot, home)
  return { inside, day, danger, sheltering: inside && (!day || danger.length > 0) }
}

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
export function needsOutside (c, home) {
  if (c.inPlace) return false
  return !c.pos || !isInside({ entity: { position: c.pos } }, home)
}

function fromLeaf (bot, state, world, leaf) {
  switch (leaf.kind) {
    case 'dig':
      return leaf.sources.flatMap((name) => world.dig(name).map((p) => ({
        id: `dig ${name} at ${fmt(p)}`, verb: 'dig', target: name, distance: dist(bot, p), pos: p, block: name
      })))
    case 'kill':
      return leaf.sources.flatMap((name) => world.hunt(name).map(({ e, dist: d }) => ({
        id: `attack ${name} #${e.id}`, verb: 'attack', target: name, distance: round(d), pos: e.position, entityId: e.id, hostile: false
      })))
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
      const directions = Object.entries(EXPLORE_DIRECTIONS)
        .filter(([, [dx, dz]]) => !leaf.away || awayFrom(bot, leaf.away, dx, dz))
        .map(([dir, [dx, dz]]) => ({
          id: `explore ${dir}`, verb: 'explore', target: dir, looking_for: lookingFor, dx, dz,
          been_there: !!state.memory && visited(state.memory, me.offset(dx * EXPLORE_DISTANCE, 0, dz * EXPLORE_DISTANCE))
        }))
        .sort((a, b) => a.been_there - b.been_there)
      return [...recalled, ...directions]
    }
    case 'craft': {
      const table = leaf.needsTable ? findTable(bot) : null
      return [{
        id: `craft ${leaf.item} x${leaf.times}`, verb: 'craft', target: leaf.item, item: leaf.item, times: leaf.times, needsTable: leaf.needsTable,
        ...(table ? { pos: table.position, distance: dist(bot, table.position) } : { inPlace: !leaf.needsTable })
      }]
    }
    case 'place':
      // 置ける場所でだけ出す（何も置けない場所で何度も選ばれた）
      return stationSpot(bot, state) ? [{ id: `place ${leaf.item} nearby`, verb: 'place_station', target: leaf.item, item: leaf.item }] : []
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
        verb: 'place_plan', target: `${leaf.block.block} of the house`, progress: `${s.placed}/${s.total}`,
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
    case 'place_bed':
      return [{ id: 'place the bed in the house', verb: 'place_bed', target: 'bed', inPlace: true, distance: dist(bot, state.home.inside) }]
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
    const best = foods.sort((a, b) => bot.registry.foodsByName[b.name].foodPoints - bot.registry.foodsByName[a.name].foodPoints)[0]
    const lastResort = bot.food <= HUNGER_URGENT && bot.inventory.items().find((i) => LAST_RESORT_FOOD.includes(i.name))
    const eat = best ?? lastResort
    if (eat) out.push({ id: `eat ${eat.name}`, verb: 'eat', target: eat.name, item: eat.name, inPlace: true })
  }
  // 近くにかまどがあれば、目標に関係なく生肉を焼く（回復する満腹度が 2.7 倍）
  const cook = cooking(bot, knowledge)
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
