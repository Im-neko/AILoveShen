// 道具（設計書 21）: Gemini が名指しで呼ぶ行動と調べもの。
//
// 行動の道具は、候補と同じ実行の経路（runner.mjs の run）を通る。引数はそのまま信じない: 世界と
// 知識から導き（掘るブロックの名前、作業台が要るか、かまどと燃料）、存在と距離を確かめる。安全の
// 制約はここで守り、断るときは理由を返す: 家に避難している間（夜、ドアの前の敵）は外に出る道具を
// 断る、今の家と建てている家は掘らない・置かない。調べものの道具はすぐ返り、履歴に残さない。
import { isQuarryKind, quarrySpot, inQuarry, QUARRY_RADIUS } from './quarry.mjs'
import vec3Pkg from 'vec3'
import { round, inventoryCounts, isHostile, bearing, blockProps, fillCount, isUnderground } from './observe.mjs'
import { shelterOf, needsOutside, creeperNear, CREEPER_HOME_REASON } from './candidates.mjs'
import { refusalAt, noteRefusal } from './refusals.mjs'
import { shallowStone, caveOre, SHALLOW_REMEDY, CAVE_ORE_REMEDY } from './mining.mjs'
import { protectedReason, hasBed, bedSpot, inStandingBuilding, TEMPORARY } from './home.mjs'
import { liquidReason } from './liquid.mjs'
import { isTreeLog, isLeftoverLog, isNaturalLogName } from './trees.mjs'
import { placedByMe } from './ledger.mjs'
import { rockBetween, closedSpace } from './reach.mjs'
import { CROPS, CLEARABLE, cropBySeed, cropByBlock, isRipe, ageOf, farmProtected, farmDistance, isSaplingItem } from './farming.mjs'
import { inHomeRoom } from './builds.mjs'
import { findTable, findFurnace, stationRefusal, nearbyDrops, blockedDrops, FACE_VECTORS } from './primitives.mjs'
import { goalKeep, spareStacks, isTossable, reserveFor } from './spare.mjs'
import { seedsToToss, seedToss, isSurplusSeed } from './seeds.mjs'
import { smeltingProduct, FUELS, FOOD_EXCLUDED } from './knowledge.mjs'
import { torchesNeeded, TORCH_SPACING } from './trail.mjs'
import { homeChests, chestWith } from './memory.mjs'
import { mapFind } from './worldmap-places.mjs'
import { exposed } from './world.mjs'
import { moveRefusal, isHomeFurniture } from './furniture.mjs'
import { freeGuards } from './guards.mjs'
import { describeRecipe } from './recipes.mjs'
import { usesOf, foodsInfo, loadNames } from './items.mjs'
import { foodNow } from './foodnow.mjs'
import { workCell, layoutAt, layoutStatus, waterAt, mineBodyReason } from './facilities.mjs'
import { BOAT, boatItem, riding } from './boat.mjs'
import { holdsWater } from './breath.mjs'
import { levelTarget, levelStatus, keptByNotDemolishing, keptGround, findCount, fillNeeded, KEPT_REMEDY, MIN_LEVEL, MAX_LEVEL, FARM_MARGIN } from './leveling.mjs'
import { supportFor } from './placing.mjs'
import { inspectCell, inspectSelf, readDiagnostics } from './inspect.mjs'

const { Vec3 } = vec3Pkg

const CREEPER_HOME_NEAR = 12 // クリーパーが近いとき、goto の目的地が玄関からこの距離以内なら断る
const AT_HOME_GOTO_RADIUS = 16 // 家に帰る小目標のとき、goto の目的地が玄関からこの距離まで
const MAX_TOOL_DISTANCE = 256 // goto は着くまで区間ごとに歩く
const MAX_BLOCK_DISTANCE = 32 // 掘る・置く対象
const MAX_ENTITY_DISTANCE = 24
const MAX_CRAFT_TIMES = 16
const FIND_RADIUS = 64
const FIND_SHOWN = 8
const MAP_FIND_RADIUS = 96 // 視界の地図から探す半径（find_blocks の radius は読み込み済みの範囲の分）
export const DROP_RADIUS = 16
const PLANTS = /^(short_grass|tall_grass|fern|large_fern|wheat|carrots|potatoes|beetroots|sweet_berry_bush|dandelion|poppy|blue_orchid|allium|azure_bluet|(red|orange|white|pink)_tulip|oxeye_daisy|cornflower|lily_of_the_valley|sunflower|lilac|rose_bush|peony)$|(_sapling|_propagule)$/
const REPLACEABLE = new Set(['air', 'cave_air', 'water', 'short_grass', 'tall_grass', 'fern', 'large_fern', 'snow', 'dead_bush'])
// use_item で使わない物（設計書 37）と、専用の道具があるもの
const NOT_USED = [
  [/^(flint_and_steel|fire_charge|lava_bucket)$/, 'it starts fires'],
  [/^(tnt|tnt_minecart|end_crystal)$/, 'it explodes'],
  [/_spawn_egg$/, 'it is not part of survival play'],
  [BOAT, 'use ride_boat for boats (goto and other long moves also cross long water by boat on their own)']
]
const OWN_TOOL_BLOCKS = /(_bed|chest|barrel|furnace|smoker|shulker_box|crafting_table)$/
const MAX_HOLD_S = 3

export const ACTION_TOOLS = ['do_suggestion', 'goto', 'dig', 'place', 'craft', 'pickup', 'attack', 'flee', 'eat', 'equip',
  'smelt', 'deposit', 'toss', 'withdraw', 'go_home', 'sleep', 'build_next', 'move_furniture', 'plant', 'till', 'sow', 'water', 'wait',
  'use_item', 'ride_boat', 'level', 'clear_site']
export const QUERY_TOOLS = ['find_blocks', 'recipe_of', 'find_recipes', 'how_to_get', 'uses_of', 'find_foods', 'inspect', 'diagnostics']

class Refused extends Error {}
const refuse = (why) => { throw new Refused(why) }
// 作業知識の断り（設計書 123）。BRIDGE_GUARDS=standard 以外は断らず、結果に添えて通す。standard は断る
const RULE_NOTE = 'not enforced'
const fmt = (p) => `${p.x},${p.y},${p.z}`

function position (args) {
  const [x, y, z] = [args.x, args.y, args.z].map(Number)
  if (![x, y, z].every(Number.isFinite)) {
    // どれが欠けたかを言う（z を落とした goto(x=-98, y=65) が、同じ手のまま 2 回続いた: 2026-10-07）
    const bad = ['x', 'y', 'z'].filter((k) => !Number.isFinite(Number(args[k])))
    refuse(`x, y and z must be numbers: ${bad.join(' and ')} ${bad.length > 1 ? 'are' : 'is'} missing or not a number (got ${['x', 'y', 'z'].map((k) => `${k}=${args[k] ?? 'none'}`).join(', ')})`)
  }
  return new Vec3(Math.floor(x), Math.floor(y), Math.floor(z))
}

// item を times 回作る材料が持ち物にあるか。なければ、一番近いレシピの何がいくつ足りないかで断る
// 持ち物で最大何回作れるか（レシピが複数あれば、いちばん多いもの）
function craftable (knowledge, recipes, inventory) {
  const held = (spec) => knowledge.resolve(spec).members.reduce((n, m) => n + (inventory[m] ?? 0), 0)
  const crafts = (r) => Math.min(...Object.entries(r.ingredients).map(([spec, per]) => Math.floor(held(spec) / per)))
  const best = recipes.map((r) => ({ r, n: crafts(r) })).sort((a, b) => b.n - a.n)[0]
  return { best, held }
}

function short (knowledge, recipes, inventory, item, times) {
  const { best, held } = craftable(knowledge, recipes, inventory)
  if (!best || best.n >= times) return
  const need = Object.entries(best.r.ingredients).map(([spec, per]) => {
    const { label } = knowledge.resolve(spec)
    return { label, per, total: per * times, have: held(spec) }
  })
  const lacksLabels = (all) => all.filter((n) => n.have < n.total).map((n) => n.label).join(' and ')
  const lacks = need.filter((n) => n.have < n.total).map((n) => `${n.total} ${n.label} (${n.per} per craft; you have ${n.have}, ${n.total - n.have} short)`)
  refuse(`cannot craft ${item} x${times}: needs ${lacks.join(' and ')}; ` +
    (best.n > 0 ? `what you carry makes only ${best.n} (${best.r.count} ${item} per craft)` : `what you carry makes none (one craft gives ${best.r.count} ${item})`) +
    `. Get the ${lacksLabels(need)} first (how_to_get)`)
}

// 括弧の中の距離と id だけが違う提案（「craft white_bed x1（7.7m）」、「flee from drowned #15165」）は同じ手。
// 座標は手の中身なので、違えば別の手
const withoutVolatile = (id) => String(id).replace(/[（(][^）)]*[）)]/g, '').replace(/#\d+/g, '#')
// モデルは提案の id を「explore east」でなく「explore_east」と書く（道具の名前の癖）。空白と _ は同じ字として照らす
const looseId = (id) => withoutVolatile(id).replace(/_/g, ' ').replace(/\s+/g, ' ').trim()
// 種類の名前（boat）で品物を頼まれたとき、品物（oak_boat）にできる種類
const KIND_WORDS = new Set(['boat', 'bed', 'door', 'sign', 'fence', 'fence_gate', 'trapdoor', 'button', 'pressure_plate', 'slab', 'stairs', 'wool', 'carpet'])
const SUGGESTIONS_SHOWN = 4
const MAX_BUILD_RUN = 60 // build_next が続けて置く上限

// 何もない座標を指したとき、そのまわり（半径 3）にある固いブロックを種類ごとに近い順に添える
// （座標が 1 ずれただけのことが多い。2026-09-30: 空気を掘れと 15 回断られた）。言い直すための座標を渡す
const NEAR_SOLIDS_RADIUS = 3
const NEAR_SOLIDS_SHOWN = 3
function nearbySolids (bot, state, pos) {
  const best = new Map()
  for (let dx = -NEAR_SOLIDS_RADIUS; dx <= NEAR_SOLIDS_RADIUS; dx++) {
    for (let dy = -NEAR_SOLIDS_RADIUS; dy <= NEAR_SOLIDS_RADIUS; dy++) {
      for (let dz = -NEAR_SOLIDS_RADIUS; dz <= NEAR_SOLIDS_RADIUS; dz++) {
        const p = pos.offset(dx, dy, dz)
        const b = bot.blockAt(p)
        if (b?.boundingBox !== 'block' || /^(bedrock|water|lava)$/.test(b.name)) continue
        if (protectedReason(state, p, 0, b) || mineBodyReason(state, p) || liquidReason(bot, p, b)) continue // 掘れないものは勧めない
        const d = Math.hypot(dx, dy, dz)
        if (!best.has(b.name) || d < best.get(b.name).d) best.set(b.name, { p, d })
      }
    }
  }
  const near = [...best].sort((a, b) => a[1].d - b[1].d).slice(0, NEAR_SOLIDS_SHOWN)
  if (!near.length) return ''
  return `. Solid blocks near there: ${near.map(([name, { p }]) => `${name} at ${fmt(p)}`).join(', ')} (use find_blocks for the exact coordinates)`
}

// 道具の引数の座標（なければ null）
function positionOf (args) {
  const [x, y, z] = [args.x, args.y, args.z].map(Number)
  return [x, y, z].every(Number.isFinite) && args.x != null ? { x, y, z } : null
}

function within (bot, p, max, what) {
  const d = bot.entity.position.distanceTo(p.offset(0.5, 0.5, 0.5))
  if (d > max) {
    // y は高さ（-64〜320）。y と z を取り違えた座標（y=-325, z=1 など）は、入れ替えれば近い
    const swapped = new Vec3(p.x, p.z, p.y)
    const hint = (p.y < -64 || p.y > 320) && bot.entity.position.distanceTo(swapped.offset(0.5, 0.5, 0.5)) <= max
      ? `; y is the height (-64..320), did you swap y and z? ${fmt(swapped)} is ${round(bot.entity.position.distanceTo(swapped.offset(0.5, 0.5, 0.5)))}m away`
      : ''
    // 負の座標のマイナスを落とした座標（z=-285 を 285 と書く）は、符号を戻せば近い。2026-10-03: 今日の「遠すぎる」
    // 拒否 110 件の多くが、dig の z=285（本当は -285）の繰り返しだった
    const flipped = !hint && [[1, -1], [-1, 1], [-1, -1]]
      .map(([sx, sz]) => new Vec3(p.x * sx, p.y, p.z * sz))
      .find((q) => (q.x !== p.x || q.z !== p.z) && bot.entity.position.distanceTo(q.offset(0.5, 0.5, 0.5)) <= max)
    const sign = flipped
      ? `; did you drop a minus sign? coordinates are signed (you are at ${fmt(bot.entity.position.floored())}): ${fmt(flipped)} is ${round(bot.entity.position.distanceTo(flipped.offset(0.5, 0.5, 0.5)))}m away`
      : ''
    refuse(`${what} is ${round(d)}m away (at most ${max}m)${hint}${sign}`)
  }
  return round(d)
}

// 負の座標のマイナスを落とした座標（z=-285 を 285 と書く）を、符号を戻せば届く座標に読み直す。届く候補が複数あれば、
// いちばん近いものが次の 2 倍以上近いときだけ（書くのは近くのものだから。同じくらいなら、どれか分からないので断る）。
// 直したことは notes に残し、結果に添えて、次から正しい座標を書かせる
const SIGN_FIX_RATIO = 2
function signFixed (bot, p, max, notes) {
  const me = bot.entity.position
  const away = (q) => me.distanceTo(q.offset(0.5, 0.5, 0.5))
  if (away(p) <= max) return p
  const fits = [[1, -1], [-1, 1], [-1, -1]]
    .map(([sx, sz]) => new Vec3(p.x * sx, p.y, p.z * sz))
    .filter((q, i, all) => (q.x !== p.x || q.z !== p.z) && away(q) <= max && all.findIndex((o) => o.x === q.x && o.z === q.z) === i)
    .sort((a, b) => away(a) - away(b))
  if (!fits.length || (fits.length > 1 && away(fits[1]) < SIGN_FIX_RATIO * away(fits[0]))) return p
  notes.push(`read ${fmt(p)} as ${fmt(fits[0])}: coordinates are signed, and ${fmt(p)} is ${round(away(p))}m away (you are at ${fmt(me.floored())}); write the minus sign next time`)
  return fits[0]
}

const UNDERGROUND_Y = 54 // これより低い所を地下とみなす（海面は 62）

function holding (bot, item) {
  if (typeof item !== 'string' || !item) refuse('item is required')
  if (!(inventoryCounts(bot)[item] > 0)) refuse(`you have no ${item}`)
}

function entity (bot, args) {
  const e = bot.entities[Number(args.entity)]
  if (!e || e === bot.entity || e.type === 'player') refuse(`no mob with id ${args.entity} nearby (use the ids in mobs)`)
  within(bot, e.position.floored(), MAX_ENTITY_DISTANCE, e.name)
  return e
}

// deps: { bot, state, knowledge, run(c, label), candidates(): 今の候補（ソルバーの提案）,
//         check(specs): 条件の判定（goals.mjs の checkConditions） }
let names = null // 日本語名（find_foods。初めて使うとき読む）

// 座標の下（8 マスまで）の最初の固い物の上が水か。読み込まれていなければ偽（分からない）
function overWater (bot, pos) {
  for (let dy = 0; dy <= 8; dy++) {
    const b = bot.blockAt(pos.offset(0, -dy, 0))
    if (!b) return false
    if (holdsWater(b)) return true
    if (b.boundingBox === 'block') return false
  }
  return false
}

export function createTools (deps) {
  const { bot, state, knowledge } = deps
  // 呼び出しの間に直した座標の説明（結果に添える。道具は一度に 1 つしか動かない）
  let notes = []
  // 届く座標か断る。マイナスの落ちは直した座標を返す
  const reach = (p, max, what) => {
    const q = signFixed(bot, p, max, notes)
    within(bot, q, max, what(q))
    return q
  }

  // 家の部屋の中に置いてよいか（家具も松明・ランタン・花なども）: 室内の空いたセルで、ドアのすぐ内側
  // （通り道: 足と頭の 2 マス）でなく、ベッドがまだなければ床に置いてもベッドの場所が残るとき。
  // どこに置くかは Gemini が決める（2026-09-27: 松明が家の一部として断られ、placed(torch, home) が
  // 進まなかった）
  // 置く先がふさがっているとき、隣の置ける所を教える（既存のチェストの位置に置こうとして断られ、隣に置き直せなかった:
  // 2026-10-08）。同じ高さの 2 マス以内で、空いていて、面を借りる隣があり、置いてよい所を近い順に
  const nearbySpots = (args, pos) => {
    const found = []
    for (let dx = -2; dx <= 2; dx++) {
      for (let dz = -2; dz <= 2; dz++) {
        const p = pos.offset(dx, 0, dz)
        if (p.equals(pos) || !REPLACEABLE.has(bot.blockAt(p)?.name)) continue
        const feet = bot.entity.position.floored()
        if (p.equals(feet) || p.equals(feet.offset(0, 1, 0))) continue
        if (!supportFor(bot, p, args.item, null)) continue
        if (!(inRoom(args.item, p) || (TEMPORARY.test(args.item) && !inStandingBuilding(bot, state, p, 1)) || !protectedReason(state, p))) continue
        if (stationRefusal(bot, state, args.item, p)) continue
        found.push(p)
      }
    }
    found.sort((a, b) => a.distanceTo(pos) - b.distanceTo(pos))
    return found.length ? `. Free spots next to it: ${found.slice(0, 4).map((p) => fmt(p)).join('; ')} (place there instead)` : ''
  }
  const inRoom = (item, pos) => {
    const home = state.home
    if (!home || !inHomeRoom(home, pos)) return false
    if (home.inside && pos.x === home.inside.x && pos.z === home.inside.z && pos.y - home.inside.y <= 1) return false
    if (pos.y !== home.min.y || hasBed(bot, home)) return true
    const occupied = { ...bot, blockAt: (p) => p.equals(pos) ? { name: item, boundingBox: 'block', position: p } : bot.blockAt(p) }
    return !!bedSpot(occupied, home)
  }

  // 場所を省いたときの次のマス（till / sow / plant。設計書 47 §14）: 今の小目標が設計した施設ならそこ、
  // でなければその作物・苗木の設計した施設（まくのは家の畑の空いた耕地も）
  const defaultCell = (kind, { crop = null, sapling = 'sapling' } = {}) => {
    const spec = state.goal?.spec
    const facility = spec?.predicate === 'facility' && state.facilities?.[spec.name]?.layout ? spec.name : null
    return workCell(bot, state, kind, facility ? { facility } : { crop, sapling })?.pos ?? null
  }
  // 設計した畑・植林場の、次に作業するマス（座標を省けばそこを使う）を、断りに添える
  const nextCell = (kind, opts) => {
    const p = defaultCell(kind, opts)
    return p ? `. The next cell to work on is ${fmt(p)} (leave x, y, z out to use it)` : ''
  }
  const advise = (why) => {
    if (!freeGuards()) refuse(why)
    notes.push(`${RULE_NOTE}: ${why}`)
  }
  const NO_FACILITY = 'new farmland and saplings go only in the cells of a designed farm or tree farm (add facility(<name>) to an objective to design one; search_facility shows them)'

  // 行動の道具の引数から、実行するもの（PRIMITIVES の c）を作る
  const build = {
    do_suggestion ({ id }) {
      const now = deps.candidates()
      const c = now.find((x) => x.id === id)
      if (c) return { c, label: id }
      // 距離や id だけが違う提案が 1 つだけあれば、それを選んだこと（距離や id は歩くたびに変わる）
      const same = now.filter((x) => looseId(x.id) === looseId(id))
      if (same.length === 1) return { c: same[0], label: same[0].id }
      // 同じ動詞の提案を先に見せて、言い直せるようにする（言い直しにも 1 回の判断がかかる）
      const verb = String(id).split(' ')[0]
      const shown = [...now.filter((x) => x.id.split(' ')[0] === verb), ...now.filter((x) => x.id.split(' ')[0] !== verb)]
        .slice(0, SUGGESTIONS_SHOWN).map((x) => x.id.slice(0, 80))
      refuse(`${id} is not a current suggestion${shown.length ? ` (now: ${shown.join('; ')})` : ''}`)
    },
    goto (args) {
      const pos = position(args)
      within(bot, pos, MAX_TOOL_DISTANCE, `${fmt(pos)}`)
      // 家に帰る小目標のときは、家の近くにしか歩かない（家の座標を思い違えて、地下の (0,0,-347) へ
      // 歩き続けた: 2026-10-01。帰るには go_home がある）
      const door = state.home?.door
      if (state.goal?.spec?.predicate === 'at_home' && door && Math.hypot(pos.x - door.x, pos.z - door.z) > AT_HOME_GOTO_RADIUS) {
        refuse(`will not walk to ${fmt(pos)}: the goal is at_home and the home's door is at ${fmt(door)}. Use go_home (or goto near the door)`)
      }
      // 追ってくるクリーパーを家のそばへ連れて行かない
      if (door && Math.hypot(pos.x - door.x, pos.z - door.z) <= CREEPER_HOME_NEAR && creeperNear(bot, state)) refuse(`will not walk to ${fmt(pos)}, next to the home: ${CREEPER_HOME_REASON}`)
      // 岩に囲まれていて、目標が岩の向こうの遠い所なら、経路探索が掘る道を探して 5 秒で諦める。先に断る
      const rock = rockBetween(bot, pos, riding(bot), { cliff: true })
      if (rock?.cliff) {
        refuse(`will not walk to ${fmt(pos)}: you stand on a roof or a pillar top with a drop on every side (${rock.cells} cells you can walk on), ${rock.gap} blocks from the target. Get down first: the candidates "climb down the pillar" (a pile you made) or "step off the edge", or dig down through the roof you stand on`)
      }
      if (rock) {
        refuse(`will not walk to ${fmt(pos)}: you are in a closed space (${rock.cells} cells you can walk in) and the target is about ${rock.gap} blocks of rock away. ` +
          'The path search gives up after 5 s on that much digging. Get out first: the candidate "climb out of the closed space" piles up the dirt or cobblestone you carry (a pickaxe is not needed), or dig a stair up, or go_home')
      }
      // 松明なしで深い所へ下りない（暗くて先が見えず、そのまま採掘して体力が 19.5 から 1.5 まで落ちた:
      // 2026-10-01）。下りる前に作る
      // 持つ数は行き先までの距離から（暗い所では約 9 m ごとに置いて進む: trail.mjs）
      if (pos.y <= UNDERGROUND_Y && pos.y <= bot.entity.position.y - 8) {
        const need = torchesNeeded(bot.entity.position.distanceTo(pos))
        const have = inventoryCounts(bot).torch ?? 0
        if (have < need) {
          refuse(`will not walk down to ${fmt(pos)}: it is ${Math.round(bot.entity.position.y - pos.y)} blocks below you, dark down there, and you carry ${have} torch${have === 1 ? '' : 'es'} ` +
            `(${need} needed: one is placed about every ${TORCH_SPACING} m on the way, so no monster can spawn behind you). Make torches first (a torch is 1 coal or charcoal + 1 stick)`)
        }
      }
      // 水の上の座標へは歩かない（海や川や湖の中を歩くと、息が続かず溺れる: 2026-10-07、遠い座標への goto が海を渡って溺死）
      if (!riding(bot) && overWater(bot, pos)) {
        refuse(`will not walk to ${fmt(pos)}: it is over water (sea, river or lake), and walking there goes underwater where you can drown. Pick a coordinate on land, or ride a boat`)
      }
      const range = Math.max(0, Math.min(8, Number(args.range ?? 1)))
      return { c: { verb: 'goto_pos', target: fmt(pos), pos, range } }
    },
    dig (args) {
      const pos = reach(position(args), MAX_BLOCK_DISTANCE, (q) => `the block at ${fmt(q)}`)
      const block = bot.blockAt(pos)
      if (!block) refuse(`the block at ${fmt(pos)} is not loaded`)
      // 草・作物・苗木・花は当たり判定がないが掘れる（草から種、育った作物の収穫。設計書 33）
      if (block.boundingBox !== 'block' && !PLANTS.test(block.name)) refuse(`there is ${block.name} at ${fmt(pos)}, nothing to dig${nearbySolids(bot, state, pos)}`)
      // 水や溶岩をせき止めているブロックは、demolish でも掘らない（壊すと流れ出す。docs/design/55）
      const wet = liquidReason(bot, pos, block)
      if (wet) refuse(`will not dig ${block.name} at ${fmt(pos)}: ${wet}. Leave it, or get the material somewhere else`)
      // 家・建てている家・建物は、ふつうは掘らない。壊すと決めたとき（demolish）だけ掘る
      const why = args.demolish === true ? null : protectedReason(state, pos, 0, block)
      if (why) refuse(`will not dig ${block.name} at ${fmt(pos)}: ${why}. If taking it down is the point of this goal, dig it again with demolish: true`)
      // 採掘場の階段の柱と段の床は、掘ると底から上がれなくなる
      const body = args.demolish === true ? null : mineBodyReason(state, pos)
      if (body) refuse(`will not dig ${block.name} at ${fmt(pos)}: ${body}. Dig the rock beside the mine instead`)
      // 家があるとき、地表に近い石は掘らない（採掘場から。設計書 68）
      if (shallowStone(bot, state, pos, block.name)) refuse(`will not dig ${block.name} at ${fmt(pos)}: it is close to the surface. ${SHALLOW_REMEDY}`)
      // 洞窟の鉱石は取らない（採掘場の壁に出たものだけ。候補と寄り道には掛けていたが、道具の dig が抜けていた: 2026-10-07、
      // 自然の洞窟で鉄を掘った）。壊すと決めたとき（demolish）は別
      if (args.demolish !== true && caveOre(state, pos, block.name)) refuse(`will not dig ${block.name} at ${fmt(pos)}: ${CAVE_ORE_REMEDY}`)
      // 整地する土地の、床より下の地面は掘らない（そろえた地面の下が穴だらけになる）
      if (keptGround(state, pos, block.name)) advise(`will not dig ${block.name} at ${fmt(pos)}: ${KEPT_REMEDY}`)
      // 耕地は畑の地面: 掘ると土になって畑が減る（小麦の収穫に、作物の下の耕地の座標で dig を呼んでいた）
      if (block.name === 'farmland' && args.demolish !== true) {
        const crop = bot.blockAt(pos.offset(0, 1, 0))
        const above = cropByBlock(crop?.name) ? `; the ${crop.name} on it is at ${fmt(pos.offset(0, 1, 0))} (dig that one when it is ripe)` : ''
        refuse(`will not dig farmland at ${fmt(pos)}: it is the ground of a farm and digging it turns the farm into dirt${above}. If taking the farm down is the point of this goal, dig it again with demolish: true`)
      }
      if (!isRipe(block)) advise(`the ${block.name} at ${fmt(pos)} is not ripe yet (age ${ageOf(block)}/${CROPS[cropByBlock(block.name)].mature}): digging it now gives only seeds and loses the crop`)
      const tools = knowledge.harvestTools(block.name)
      if (tools && !tools.some((t) => inventoryCounts(bot)[t] > 0) && !(isUnderground(bot) && closedSpace(bot))) refuse(`${block.name} drops nothing without one of: ${tools.join(', ')}`)
      return { c: { verb: 'dig', target: block.name, block: block.name, pos } }
    },
    place (args) {
      holding(bot, args.item)
      const pos = reach(position(args), MAX_BLOCK_DISTANCE, fmt)
      const cell = bot.blockAt(pos)
      if (!cell) refuse(`${fmt(pos)} is not loaded`)
      if (!REPLACEABLE.has(cell.name)) refuse(`${fmt(pos)} is taken by ${cell.name}${nearbySpots(args, pos)}`)
      // 家の部屋の中、または建築予定地への仮設（建っているもののまわりでなければ。建てる番が来たら壊す）
      const temporary = TEMPORARY.test(args.item) && !inStandingBuilding(bot, state, pos, 1)
      // override: true は、家・建てている家・建物の保護を承知で置くとき（置く先が保護されていても置く）
      const why = inRoom(args.item, pos) || temporary || args.override === true ? null : protectedReason(state, pos)
      if (why) refuse(`will not place at ${fmt(pos)}: ${why} (override: true places it anyway if you mean to)${state.plan?.origin && /current home/.test(why) ? ' (the missing blocks of the house are placed by build_next or the place_plan candidate of built(), which also fills the ground under the house first)' : ''}`)
      const stationWhy = stationRefusal(bot, state, args.item, pos)
      if (stationWhy) advise(`will not place at ${fmt(pos)}: ${stationWhy}`)
      const feet = bot.entity.position.floored()
      if (pos.equals(feet) || pos.equals(feet.offset(0, 1, 0))) refuse(`you are standing in ${fmt(pos)}; move first`)
      // 面を借りる隣: on（floor / wall / ceiling）。省略なら、壁に付けられる物（松明など）は壁を先に
      const on = ['floor', 'wall', 'ceiling'].includes(args.on) ? args.on : null
      const against = supportFor(bot, pos, args.item, on)
      if (!against) refuse(`nothing ${on ? `on the ${on} side` : 'next to'} ${fmt(pos)} to place against (chests, tables, doors and beds would open instead)${nearbySpots(args, pos)}`)
      return { c: { verb: 'place_at', target: args.item, item: args.item, pos, against } }
    },
    craft (args) {
      let item = args.item
      let recipes = typeof item === 'string' ? knowledge.recipes(item) : []
      if (!recipes.length && KIND_WORDS.has(item)) {
        // 種類の名前（boat）は品物ではない（oak_boat）: 持っている材料で作れる木の種類の物を選ぶ。
        // 作れる物がなければ、品物の名前を教える
        const named = Object.keys(bot.registry.itemsByName).filter((n) => n.endsWith(`_${item}`) && !n.includes('chest_') && knowledge.recipes(n).length)
        const inv = inventoryCounts(bot)
        const makeable = named.find((n) => craftable(knowledge, knowledge.recipes(n), inv).best?.n > 0)
        if (makeable) { item = makeable; recipes = knowledge.recipes(item) }
        else if (named.length) refuse(`${item} is not an item name; use one of ${named.slice(0, 6).join(', ')} (the wood of the planks you carry)`)
      }
      if (!recipes.length) refuse(`${item} has no crafting recipe`)
      const times = Math.max(1, Math.min(MAX_CRAFT_TIMES, Math.floor(Number(args.times ?? 1))))
      const needsTable = recipes.every((r) => r.needsTable)
      if (needsTable && !findTable(bot, state)) refuse(`${item} needs a crafting table within reach; craft one (4 planks) and place it`)
      // 材料が足りなければ、実行する前に、何がいくつ足りないかを返す（「材料が足りない?」だけでは、
      // 小麦 2 個でパン 2 個と思い込んだまま作れなかった）
      short(knowledge, recipes, inventoryCounts(bot), item, times)
      // 1 回ずつ作り直させない: 持ち物であと何回作れるかを結果に添える（板材を 4 枚ずつ 5 回、7 秒ずつ作っていた）
      const more = craftable(knowledge, recipes, inventoryCounts(bot)).best.n - times
      return { c: { verb: 'craft', target: item, item, times, needsTable, inPlace: true, more } }
    },
    pickup () {
      const d = nearbyDrops(bot, state, DROP_RADIUS)[0]
      if (!d) {
        if (blockedDrops(bot, state, DROP_RADIUS).length) refuse('the inventory is full and cannot take what lies on the ground: throw away spare items first (toss), or put things in the home chest (deposit)')
        refuse(`no item on the ground within ${DROP_RADIUS}m`)
      }
      const item = d.e.getDroppedItem()?.name ?? 'item'
      return { c: { verb: 'pickup', target: item, entityId: d.e.id, pos: d.e.position } }
    },
    attack (args) {
      const e = entity(bot, args)
      return { c: { verb: 'attack', target: e.name, entityId: e.id, pos: e.position, hostile: isHostile(bot, e) } }
    },
    flee (args) {
      const e = entity(bot, args)
      return { c: { verb: 'flee', target: e.name, entityId: e.id, pos: e.position } }
    },
    eat (args) {
      holding(bot, args.item)
      if (!bot.registry.foodsByName[args.item]) {
        // 食べ物でないものを食べさせようとし続けた（種を 15 回: 2026-10-01）。持っている食べ物を教える
        const foods = Object.entries(inventoryCounts(bot)).filter(([n, c]) => c > 0 && bot.registry.foodsByName[n] && !FOOD_EXCLUDED.has(n))
        refuse(`${args.item} is not food. ` + (foods.length
          ? `Food you carry: ${foods.map(([n, c]) => `${n} x${c}`).join(', ')}`
          : 'You carry no food: get some first (hunt an animal, take from a chest, harvest a ripe crop) — eating is not possible until then'))
      }
      return { c: { verb: 'eat', target: args.item, item: args.item, inPlace: true } }
    },
    equip (args) {
      holding(bot, args.item)
      return { c: { verb: 'equip', target: args.item, item: args.item, inPlace: true } }
    },
    smelt (args) {
      holding(bot, args.input)
      const product = smeltingProduct(args.input)
      if (!product) refuse(`${args.input} cannot be smelted`)
      const furnace = findFurnace(bot, state)
      if (!furnace) refuse(state.home
        ? 'no furnace of the house within reach: smelting and cooking are done only in the furnace inside the house (go home first; place one there if the house has none)'
        : 'no furnace within reach (not underground); place one on the surface first')
      const inv = inventoryCounts(bot)
      const count = Math.max(1, Math.min(inv[args.input], Math.floor(Number(args.count ?? inv[args.input]))))
      for (const { spec, per } of FUELS) {
        const fuel = knowledge.resolve(spec).members.find((m) => inv[m] > 0 && m !== args.input)
        if (!fuel) continue
        const fuelCount = Math.min(inv[fuel], Math.ceil(count / per))
        // 持っている燃料で焼ける数までしか入れない（足りないぶんを入れると、燃料が尽きて入力が焼けないまま残る:
        // 2026-10-03、昆布 4 個に板材 1 枚）
        const doable = Math.floor(fuelCount * per)
        if (doable < 1) continue
        const short = count - Math.min(count, doable)
        return { c: { verb: 'smelt', target: product, item: product, input: args.input, count: count - short, fuel, fuelCount, pos: furnace.position, ...(short ? { short } : {}) } }
      }
      return refuse('no fuel (coal, charcoal, planks or logs)')
    },
    deposit (args) {
      holding(bot, args.item)
      const me = bot.entity.position
      const chest = homeChests(state.memory ?? {}, state.home).sort((a, b) => me.distanceTo(new Vec3(a.x, a.y, a.z)) - me.distanceTo(new Vec3(b.x, b.y, b.z)))[0]
      if (!chest) refuse('no chest in the home: to free inventory slots, throw spare items away (toss); or place a chest in the house (craft one from 8 planks)')
      const count = Math.max(1, Math.floor(Number(args.count ?? inventoryCounts(bot)[args.item])))
      return { c: { verb: 'deposit', target: args.item, item: args.item, count, pos: new Vec3(chest.x, chest.y, chest.z) } }
    },
    // 余った物を捨てて、持ち物の空きを作る。item を書かなければ、余り全部（目標が使う物、道具、食料、
    // 松明は捨てない。土と丸石は 64 個ずつ残す。余りがなければ、余った種）。item を書けば、その物を count 個
    // （省略は残す数を超えた分）
    toss (args) {
      const inv = inventoryCounts(bot)
      const reserve = reserveFor(state)
      // 余った種は、街から離れた所へ歩いて捨てる（足元に捨てると、拾い直す）
      const tossSeeds = (items) => seedToss(bot, state, items) ?? refuse('no place outside the town to throw the seeds away (the ground around the town is not loaded or cannot be stood on)')
      if (args.item === undefined) {
        const keep = goalKeep(knowledge, state.goal?.spec)
        const items = spareStacks(bot, keep, reserve)
        if (items.length) return { c: { verb: 'toss', target: 'items', inPlace: true, items } }
        const seeds = seedsToToss(bot, state, keep)
        if (!seeds.length) refuse(`nothing spare to throw away (dirt is kept up to ${reserve.dirt} and cobblestone up to ${reserve.cobblestone}${reserve.cobbled_deepslate ? ', cobbled deepslate up to ' + reserve.cobbled_deepslate + ' (kept to wall off caves while a mine is being dug)' : ''}; tools, food, torches and what the goal needs are never thrown)`)
        return { c: tossSeeds(seeds) }
      }
      holding(bot, args.item)
      const surplusSeed = seedsToToss(bot, state, goalKeep(knowledge, state.goal?.spec)).find((x) => x.item === args.item)
      if (surplusSeed) return { c: tossSeeds([{ item: args.item, count: args.count === undefined ? surplusSeed.count : Math.min(Math.floor(Number(args.count)), surplusSeed.count) }]) }
      if (isSurplusSeed(args.item)) refuse(`${args.item} is thrown away only beyond one stack per farm (inventory and chests together): there is no surplus now`)
      if (!isTossable(args.item)) refuse(`${args.item} is not thrown away by this tool (only natural ground blocks, cobblestone, flowers and wool); put it in a chest (deposit) instead`)
      const count = args.count === undefined ? inv[args.item] - (reserve[args.item] ?? 0) : Math.floor(Number(args.count))
      if (!(count >= 1)) refuse(`${args.item}: only ${inv[args.item]} held, ${reserve[args.item] ?? 0} are kept${reserve[args.item] > 64 ? ' (to wall off caves while a mine is being dug)' : ''}; pass count to throw more`)
      // 数を書いても、枝掘りの間に塞ぐ丸石の残す数を割る捨て方はしない
      if (reserve[args.item] > 64 && inv[args.item] - count < reserve[args.item]) refuse(`${args.item}: a mine is being dug and ${reserve[args.item]} are kept to wall off caves it opens into; at most ${Math.max(0, inv[args.item] - reserve[args.item])} can be thrown away`)
      return { c: { verb: 'toss', target: args.item, inPlace: true, items: [{ item: args.item, count: Math.min(count, inv[args.item]) }] } }
    },
    withdraw (args) {
      if (typeof args.item !== 'string') refuse('item is required')
      const chest = chestWith(state.memory ?? {}, [args.item], bot.entity.position)
      if (!chest) refuse(`no chest is known to hold ${args.item}`)
      const count = Math.max(1, Math.floor(Number(args.count ?? 1)))
      return { c: { verb: 'withdraw', target: args.item, item: args.item, count, pos: new Vec3(chest.x, chest.y, chest.z) } }
    },
    // 名前なし: いちばん近い前の家（半壊・廃屋）を更地にする（設計書 166）。名前あり: 街の再設計で計画に合わないと
    // 印のついた建物・畑・植林場を更地にする（設計書 167）
    clear_site (args) {
      const name = args?.name
      if (name != null) {
        if (!(state.clearMarks ?? []).includes(name)) refuse(`${name} is not marked for taking down in the town plan${state.clearMarks?.length ? ` (marked: ${state.clearMarks.join(', ')})` : ' (nothing is marked)'}: a redraw of the town map marks what does not fit`)
        const fac = state.facilities?.[name]
        if (!fac) refuse(`there is no facility named ${name} (already cleared?)`)
        const at = fac.kind === 'build' ? state.builds?.[name]?.origin : new Vec3(fac.box.min.x, fac.box.min.y, fac.box.min.z)
        if (!at) refuse(`the build ${name} has no site yet: nothing to clear`)
        within(bot, at, MAX_TOOL_DISTANCE, `${name}`)
        return { c: { verb: 'clear_facility', target: `the ${fac.kind} ${name}`, name, inPlace: true } }
      }
      const former = state.formerHomes ?? []
      if (!former.length) refuse('there is no former home on record (the current home is not cleared this way: it is the one you live in); give name to take down a marked building or farm')
      const mine = bot.entity.position
      const home = [...former].sort((a, b) => a.door.distanceTo(mine) - b.door.distanceTo(mine))[0]
      within(bot, home.door, MAX_TOOL_DISTANCE, `the old house (door ${fmt(home.door)})`)
      return { c: { verb: 'clear_former_home', target: `the old house at ${fmt(home.door)}`, door: home.door, inPlace: true } }
    },
    go_home () {
      if (!state.home) refuse('there is no home yet')
      if (creeperNear(bot, state)) refuse(CREEPER_HOME_REASON)
      return { c: { verb: 'go_home', target: 'home', inPlace: true } }
    },
    sleep () {
      if (!state.home || !hasBed(bot, state.home)) refuse('there is no bed in the home')
      return { c: { verb: 'sleep', target: 'bed', inPlace: true } }
    },
    build_next (args) {
      // 名前付きの建物（docs/design/25_builds.md）: 名前か、今の目標の built(name)
      const name = args?.name ?? (state.goal?.spec?.predicate === 'built' ? state.goal.spec.name : null)
      // 続けて置く個数。省くと、材料と体力の続く限り（夕方・空腹・襲われたらランナーが止める）
      const count = Math.max(1, Math.min(MAX_BUILD_RUN, Math.floor(Number(args?.count ?? MAX_BUILD_RUN))))
      if (args?.reset === true) {
        const plan = name ? state.builds?.[name] : null
        if (!name) refuse('reset needs the build name (build_next {name, reset: true})')
        if (!plan) refuse(`there is no build named ${name}`)
        if (!plan.origin) refuse(`the build ${name} has no site yet: nothing to clear`)
        within(bot, plan.origin, MAX_TOOL_DISTANCE, `the site of ${name}`)
        return { c: { verb: 'reset_build', build: name, target: `the site of the build ${name}`, inPlace: true } }
      }
      if (name) {
        const plan = state.builds?.[name]
        if (!plan) refuse(`there is no build named ${name}`)
        if (plan.status(bot).complete) refuse(`the build ${name} is complete`)
        return { c: { verb: 'place_plan', build: name, target: `the next block of the build ${name}`, inPlace: true, count } }
      }
      if (!state.plan) refuse('there is no house plan')
      if (state.plan.status(bot).complete) refuse('the house plan is complete')
      return { c: { verb: 'place_plan', target: 'the next block of the house', inPlace: true, count } }
    },
    // 置いてある家具（ベッド、作業台、かまど、チェスト）を拾って置き直す。今の家の家具も動かせる
    // （家の中でだけ）。to を省くと、ベッドは家の中の空いた所、ほかは家の中（なければ近く）
    move_furniture (args) {
      const pos = reach(position(args), MAX_BLOCK_DISTANCE, (q) => `the block at ${fmt(q)}`)
      const block = bot.blockAt(pos)
      const to = args.to_x != null ? position({ x: args.to_x, y: args.to_y, z: args.to_z }) : null
      const why = moveRefusal(bot, state, pos, block, to)
      if (why) refuse(why)
      // 家のものでなければ、建物や建てている家の一部でないか（ふつうの掘る決まり）
      const guard = isHomeFurniture(state, pos) || args.override === true ? null : protectedReason(state, pos, 0, block)
      if (guard) refuse(`will not move ${block.name} at ${fmt(pos)}: ${guard} (override: true moves it anyway if you mean to)`)
      if (to) {
        const cell = bot.blockAt(to)
        if (!cell || !REPLACEABLE.has(cell.name)) refuse(`${fmt(to)} is not free`)
      }
      return { c: { verb: 'move_placed', target: block.name, block: block.name, pos, ...(to ? { to } : {}) } }
    },
    // 植林と畑（設計書 33）。耕す・植えるのは設計した施設のマスだけ（設計書 47 §14）。場所を省くと、今の
    // 小目標の施設（facility(name)）か、その作物・苗木の施設の次のマス
    plant (args) {
      const item = args.item ?? Object.keys(inventoryCounts(bot)).find(isSaplingItem)
      if (!item || !isSaplingItem(item)) refuse('item must be a sapling you have (e.g. oak_sapling)')
      holding(bot, item)
      const cell = args.x != null ? position(args) : defaultCell('plant', { sapling: item })
      if (!cell) refuse(`no free cell for ${item} in the tree farms in view: ${NO_FACILITY}`)
      const pos = reach(cell, MAX_TOOL_DISTANCE, fmt)
      const fac = layoutAt(state, 'tree_farm', pos)
      if (!fac) advise(`will not plant at ${fmt(pos)}: ${NO_FACILITY}${nextCell('plant', { sapling: item })}`)
      else if (fac.layout.sapling !== 'sapling' && fac.layout.sapling !== item) advise(`the tree farm ${fac.name} is for ${fac.layout.sapling}, not ${item}`)
      const kept = farmProtected(state, pos)
      if (kept) refuse(`will not plant at ${fmt(pos)}: ${kept}`)
      const why = farmDistance(state, pos)
      if (why) advise(`will not plant at ${fmt(pos)}: ${why}`)
      const here = bot.blockAt(pos)?.name ?? ''
      if ((here !== 'air' && !CLEARABLE.test(here)) || bot.blockAt(pos.offset(0, -1, 0))?.boundingBox !== 'block') refuse(`${fmt(pos)} is not free ground (give the cell above dirt or grass; it holds ${here || 'nothing loaded'}, and flowers or grass there are cleared)${nextCell('plant', { sapling: item })}`)
      return { c: { verb: 'plant', target: item, item, pos } }
    },
    till (args) {
      if (!Object.keys(inventoryCounts(bot)).some((n) => n.endsWith('_hoe'))) refuse('you have no hoe (craft one: 2 sticks + 2 planks or cobblestone)')
      const cell = args.x != null ? position(args) : defaultCell('till', { crop: state.goal?.spec?.predicate === 'farmed' ? state.goal.spec.item : null })
      if (!cell) refuse(`no cell left to till in the farms in view: ${NO_FACILITY}`)
      const pos = reach(cell, MAX_TOOL_DISTANCE, fmt)
      if (!layoutAt(state, 'farm', pos)) advise(`will not till at ${fmt(pos)}: ${NO_FACILITY}${nextCell('till', { crop: state.goal?.spec?.predicate === 'farmed' ? state.goal.spec.item : null })}`)
      const kept = farmProtected(state, pos)
      if (kept) refuse(`will not till at ${fmt(pos)}: ${kept}`)
      const why = farmDistance(state, pos)
      if (why) advise(`will not till at ${fmt(pos)}: ${why}`)
      if (!/^(dirt|grass_block|dirt_path)$/.test(bot.blockAt(pos)?.name ?? '')) refuse(`${fmt(pos)} is not dirt or grass (give the ground block itself)`)
      // 耕しただけで何も植わっていない地面は、水が近くになければ土に戻る: まく種がある分だけ耕す
      const farm = layoutAt(state, 'farm', pos)
      const seed = farm ? CROPS[farm.layout.crop].seed : null
      const seeds = seed ? inventoryCounts(bot)[seed] ?? 0 : 0
      const tilled = farm ? layoutStatus(bot, farm).sow.length : 0
      if (farm && seeds <= tilled) {
        advise(`will not till more: you have ${seeds} ${seed} for ${tilled} tilled cells with nothing planted, and tilled ground with nothing planted turns back to dirt without water. ` +
          `${seeds > 0 ? 'Sow the tilled cells first (sow)' : `Get ${seed} first (dig grass: about 1 in 10 drops a seed)`}, then till the next cell`)
      }
      return { c: { verb: 'till', target: 'farmland', pos } }
    },
    sow (args) {
      const inv = inventoryCounts(bot)
      const item = args.item ?? Object.values(CROPS).map((c) => c.seed).find((s) => inv[s] > 0)
      if (!item || !cropBySeed(item)) refuse(`item must be seeds you have: ${Object.values(CROPS).map((c) => c.seed).join(', ')}`)
      holding(bot, item)
      const crop = cropBySeed(item)
      const cell = args.x != null ? position(args) : defaultCell('sow', { crop })
      if (!cell) refuse(`no free farmland for ${item} in the farms in view (till the farm's cells first)`)
      const pos = reach(cell, MAX_TOOL_DISTANCE, fmt)
      if (bot.blockAt(pos)?.name !== 'farmland') refuse(`${fmt(pos)} is not farmland (give the farmland block itself)`)
      const fac = layoutAt(state, 'farm', pos)
      if (fac && fac.layout.crop !== crop) refuse(`the farm ${fac.name} is for ${fac.layout.crop}, not ${crop}`)
      return { c: { verb: 'sow', target: item, item, pos } }
    },
    // 畑の水のマス（設計書 47 §17）: 地面を掘って穴にし、水のバケツを置く。x,y,z を省くと次の水のマス
    water (args) {
      if (!(inventoryCounts(bot).water_bucket > 0)) {
        refuse('you have no water_bucket (craft a bucket: 3 iron ingots, then use_item {item: bucket, x,y,z} on a water block to fill it)')
      }
      const cell = args.x != null ? position(args) : defaultCell('water', { crop: state.goal?.spec?.predicate === 'farmed' ? state.goal.spec.item : null })
      if (!cell) refuse('no water cell left to fill in the farms in view (search_facility shows the farms)')
      const pos = reach(cell, MAX_TOOL_DISTANCE, fmt)
      if (!waterAt(state, pos)) refuse(`will not put water at ${fmt(pos)}: it is not a water cell of a designed farm (${NO_FACILITY})`)
      return { c: { verb: 'place_water', target: 'water', item: 'water_bucket', pos } }
    },
    // 整地（設計書 41）: where（home か x,y,z: そこに立つ足の高さ）のまわり半径 radius を、近い列から
    // 次々にそろえる（1 回の呼び出しで届くところを全部）
    level (args) {
      const where = String(args.where ?? 'home')
      let target
      try {
        target = levelTarget(state, where)
      } catch (e) {
        refuse(e.message)
      }
      // 設計した畑の上（と周囲 FARM_MARGIN マス）を畑の地面と違う高さにそろえると、周りだけが下がる（畑の耕地・作物・水は
      // 触らないので畑が高く残る）。y は立つ足の高さ（地面はその 1 つ下）: 畑の地面の高さの座標を渡した間違いを防ぐ
      const farm = Object.values(state.facilities ?? {}).find((f) => {
        const r = f.kind === 'farm' && f.layout?.rect
        return r && target.center.x >= r.x0 - FARM_MARGIN && target.center.x < r.x0 + r.w + FARM_MARGIN && target.center.z >= r.z0 - FARM_MARGIN && target.center.z < r.z0 + r.d + FARM_MARGIN
      })
      const demolish = args.demolish === true
      if (farm && !demolish && target.y - 1 !== farm.layout.y) {
        refuse(`${target.center.x},${target.center.z} is on the farm ${farm.name}, whose ground is y=${farm.layout.y}: where's y is the height you STAND on (the ground is one lower), so use ${target.center.x},${farm.layout.y + 1},${target.center.z}. The farm levels itself with the candidate "level the ground of the farm ${farm.name}"`)
      }
      const radius = Number(args.radius ?? 8)
      if (!Number.isInteger(radius) || radius < MIN_LEVEL || radius > MAX_LEVEL) refuse(`radius must be ${MIN_LEVEL}-${MAX_LEVEL}`)
      // 家のまわりを家の床と違う高さにそろえると、玄関の外の地面が盛り上がって玄関が埋まる（2026-10-11: 立つ高さ 80 に
      // 整地して、家の床の高さ 79 の玄関の前が埋まり、中から出られなくなった）
      const h = state.home
      if (h && target.y !== h.inside.y) {
        const near = 3
        const overlaps = target.center.x - radius <= h.max.x + near && target.center.x + radius >= h.min.x - near &&
          target.center.z - radius <= h.max.z + near && target.center.z + radius >= h.min.z - near
        if (overlaps) refuse(`the area around ${fmt(target.center)} (radius ${radius}) includes the home, whose floor you stand on at y=${h.inside.y}: leveling it to y=${target.y} would bury the doorstep or dig under the walls. Use where: home (or y=${h.inside.y}), or a smaller radius that stays away from the home`)
      }
      const s = levelStatus(bot, state, where, radius, { demolish })
      if (!s.off) {
        const kept = demolish ? 0 : keptByNotDemolishing(s.skipped)
        refuse(s.unloaded ? 'part of the ground is out of view: go closer first' : `the ground there is already level${kept ? ` except ${kept} columns left as they are (something placed, a home or build, a tree or a farm stands there): level with demolish: true to take them down too` : ''}`)
      }
      const carried = fillCount(bot)
      if (!s.cuts.length && carried < fillNeeded(s)) refuse(`only holes are left (${s.fills.length} blocks to fill) and you carry ${carried} dirt: gather ${fillNeeded(s)} first, from outside the area being leveled (dig grass or dirt elsewhere, the ground inside is kept)`)
      within(bot, s.center, MAX_TOOL_DISTANCE, fmt(s.center))
      return { c: { verb: 'level', target: 'ground', where, radius, ...(demolish ? { demolish: true } : {}), pos: s.cuts[0] ?? s.fills[0] } }
    },
    // 手に持って使う（右クリック。設計書 37）: entity のモブに、x,y,z のブロックに、どちらもなければ向いている方へ。
    // item を省くと素手で x,y,z のブロックを右クリックする（ドア・レバー・ボタンを押す。行き詰まったときの実験: 設計書 85）。
    // face（押す面）と sneak（スニークして押すか。既定は外す）で、押し方を変えて試せる
    use_item (args) {
      // 座標の代わりに target: home_door と書ける（座標を含まない手は覚えた候補になり、次の同じ場面で Jev の選択肢に出る）
      if (args.target != null) {
        if (args.target !== 'home_door') refuse(`target must be home_door (or give x, y, z)`)
        const door = state.home?.door
        if (!door) refuse('you have no home door yet')
        args = { ...args, x: door.x, y: door.y, z: door.z }
      }
      const bare = args.item == null || args.item === ''
      const face = args.face == null ? null : FACE_VECTORS[String(args.face)]
      if (args.face != null && !face) refuse(`face must be one of ${Object.keys(FACE_VECTORS).join(', ')}`)
      const sneak = args.sneak == null ? null : Boolean(args.sneak)
      const how = { ...(args.face != null ? { face: String(args.face) } : {}), ...(sneak != null ? { sneak } : {}) }
      if (bare) {
        if (args.entity != null) refuse('to use something on a mob you need an item')
        if (args.x == null) refuse('without an item, say which block to click with an empty hand: x, y, z')
        const pos = reach(position(args), MAX_BLOCK_DISTANCE, (q) => `the block at ${fmt(q)}`)
        const block = bot.blockAt(pos)
        if (!block) refuse(`the block at ${fmt(pos)} is not loaded`)
        if (OWN_TOOL_BLOCKS.test(block.name)) refuse(`${block.name} has its own tools (sleep, deposit, withdraw, craft, smelt, move_furniture)`)
        // 素手のクリックは壊さない: 家のドアも押せる（protectedReason は壊す・置くときの断り）
        return { c: { verb: 'use_item', target: `an empty hand on ${block.name}`, item: null, pos, hold: 0, ...how } }
      }
      holding(bot, args.item)
      for (const [re, why] of NOT_USED) if (re.test(args.item)) refuse(`will not use ${args.item}: ${why}`)
      const hold = Math.max(0, Math.min(MAX_HOLD_S, Number(args.hold ?? 0) || 0))
      if (args.entity != null) {
        const e = entity(bot, args)
        return { c: { verb: 'use_item', target: `${args.item} on ${e.name}`, item: args.item, entityId: e.id, pos: e.position.floored(), hold } }
      }
      if (bot.registry.foodsByName[args.item]) refuse(`to eat ${args.item} use eat (use_item with food is for feeding a mob: give entity)`)
      if (args.x == null) return { c: { verb: 'use_item', target: args.item, item: args.item, hold, inPlace: true } }
      const pos = reach(position(args), MAX_BLOCK_DISTANCE, (q) => `the block at ${fmt(q)}`)
      // 畑の中とそのまわりに、水を手で注がない: 穴の外に注ぐと水が畑の表面に流れる（水のマスは water の道具が掘って置く）
      const farm = args.item === 'water_bucket'
        ? Object.values(state.facilities ?? {}).find((f) => f.kind === 'farm' && f.box && pos.x >= f.box.min.x - 1 && pos.x <= f.box.max.x + 1 && pos.z >= f.box.min.z - 1 && pos.z <= f.box.max.z + 1)
        : null
      if (farm) refuse(`will not pour water in or beside the farm ${farm.name} by hand (it would run over the farmland): use the water tool, which digs the farm's water cell and puts the water in the hole`)
      // 畑の水のマスの水を汲むと、その畑の耕地が乾いて土に戻る（置き間違えの汲み戻しは、承知なら通す）
      const drained = args.item === 'bucket' ? waterAt(state, pos) : null
      if (drained) advise(`this water is the water cell of the farm ${drained.name}: taking it dries the farmland there (fine to take back a misplaced one; put it back with the water tool afterwards)`)
      const block = bot.blockAt(pos)
      if (!block) refuse(`the block at ${fmt(pos)} is not loaded`)
      if (OWN_TOOL_BLOCKS.test(block.name)) refuse(`${block.name} has its own tools (sleep, deposit, withdraw, craft, smelt, move_furniture)`)
      const why = args.override === true ? null : protectedReason(state, pos, 0, block)
      if (why) refuse(`will not use ${args.item} on ${block.name} at ${fmt(pos)}: ${why} (override: true uses it anyway if you mean to)`)
      return { c: { verb: 'use_item', target: `${args.item} on ${block.name}`, item: args.item, pos, hold, ...how } }
    },
    // ボートで水の上を x,z に向かって進む（設計書 37）。岸に着いたら降りてボートを拾う
    ride_boat (args) {
      const [x, z] = [Number(args.x), Number(args.z)]
      if (![x, z].every(Number.isFinite)) refuse('x and z must be numbers')
      const pos = new Vec3(Math.floor(x), Math.floor(Number(args.y ?? bot.entity.position.y)), Math.floor(z))
      within(bot, pos, MAX_TOOL_DISTANCE, fmt(pos))
      if (!riding(bot) && !boatItem(bot)) refuse('you have no boat (craft one: 5 planks of one wood at a crafting table)')
      return { c: { verb: 'ride_boat', target: fmt(pos), pos } }
    },
    wait () {
      const inside = !!state.home && shelterOf(bot, state.home).inside
      return { c: { verb: 'wait', target: inside ? 'inside the house' : 'here', inPlace: true, inside } }
    }
  }

  // 作物なら熟しているか（育ち途中は掘らない）と、ブロックの状態（耕地の moisture など）
  const details = (p) => {
    const b = bot.blockAt(p)
    const props = blockProps(b)
    // 原木は、木の幹か（tree: false は村の畑の縁や家の柱など、人が作った物。自動では集めない）、
    // 木の取り残しか（leftover: 葉が散った浮いた幹。集めてよい）、
    // 自分で置いた物か（placed_by_me: 間違えて置いた物は片付けてよい）
    const log = isNaturalLogName(b?.name) ? { tree: isTreeLog(bot, p), ...(isLeftoverLog(bot, p) ? { leftover: true } : {}), ...(placedByMe(bot, state, p) ? { placed_by_me: true } : {}) } : {}
    return { ...(cropByBlock(b?.name) ? { ripe: isRipe(b), age: ageOf(b) } : {}), ...log, ...(props ? { props } : {}) }
  }
  const queries = {
    find_blocks (args) {
      const id = bot.registry.blocksByName[args.block]?.id
      if (id == null) refuse(`unknown block ${args.block}`)
      const radius = Math.max(4, Math.min(FIND_RADIUS, Number(args.radius ?? 32)))
      const me = bot.entity.position
      // 断られる場所（家・建物）と、前に断られた・着けなかった場所は出さない（掘れと言われて断られる）
      // 石・土などの地形は、採掘の場所（quarry.mjs）の中を先に、印（quarry: true）をつけて出す
      const spot = isQuarryKind(args.block) ? quarrySpot(state, me) : null
      const here = spot ? bot.findBlocks({ matching: id, point: spot, maxDistance: QUARRY_RADIUS, count: findCount(state) }) : []
      const seen = new Set()
      const found = [...here, ...bot.findBlocks({ matching: id, maxDistance: radius, count: findCount(state) })]
        .filter((p) => !seen.has(`${p.x},${p.y},${p.z}`) && seen.add(`${p.x},${p.y},${p.z}`))
        .filter((p) => !protectedReason(state, p, 0, bot.blockAt(p)) && !mineBodyReason(state, p) && !liquidReason(bot, p, bot.blockAt(p)) && !refusalAt(state, 'dig', p) && !shallowStone(bot, state, p, args.block) && !keptGround(state, p, args.block))
        .sort((a, b) => (inQuarry(spot, a) ? 0 : 1) - (inQuarry(spot, b) ? 0 : 1) || a.distanceTo(me) - b.distanceTo(me)).slice(0, FIND_SHOWN)
        .map((p) => ({ x: p.x, y: p.y, z: p.z, distance_m: round(p.distanceTo(me)), direction: bearing(me, p), exposed: exposed(bot, p), ...(inQuarry(spot, p) ? { quarry: true } : {}), ...details(p) }))
      // 読み込まれていない所と半径の外は、前に見た記録から（視界の地図。設計書 59）。読み込まれていて実物が
      // 違えば、地図を直して出さない
      if (found.length < FIND_SHOWN && state.worldMap) {
        const age = Number(bot.time.age)
        const inLive = new Set(found.map((f) => `${f.x},${f.y},${f.z}`))
        const remembered = mapFind(state, args.block, me, MAP_FIND_RADIUS, {
          limit: FIND_SHOWN, ticksNow: age,
          live: (x, y, z) => bot.blockAt(new Vec3(x, y, z))?.name
        }).filter((m) => !inLive.has(`${m.x},${m.y},${m.z}`) && !refusalAt(state, 'dig', new Vec3(m.x, m.y, m.z)))
        for (const m of remembered.slice(0, FIND_SHOWN - found.length)) {
          found.push({ x: m.x, y: m.y, z: m.z, distance_m: round(m.distance_m), direction: bearing(me, m), exposed: true, source: 'map', seen_minutes_ago: m.minutes_ago })
        }
      }
      return found.length ? found : `no ${args.block} within ${radius}m${state.worldMap ? ' or in the map of what you have seen' : ''}`
    },
    recipe_of (args) {
      // バニラのレシピ全部から（設計書 29）: 作業台・かまど・石切台など、材料は「どれか 1 つ」も含めて
      if (knowledge.index) {
        const all = knowledge.index.recipesFor(String(args.item)).map(describeRecipe)
        return all.length ? all : `${args.item} has no recipe (gather it, or check the name with find_recipes)`
      }
      const recipes = knowledge.recipes(args.item).map((r) => ({ makes: r.count, ingredients: r.ingredients, needs_crafting_table: r.needsTable }))
      const smeltFrom = knowledge.smeltingInput(args.item)
      return { crafting: recipes, smelted_from: smeltFrom }
    },
    find_recipes (args) {
      if (!knowledge.index) refuse('the recipe list is not loaded')
      const found = knowledge.index.search(String(args.query ?? ''), Math.max(1, Math.min(20, Number(args.limit ?? 8))))
      return found.length ? found : `no recipe matches "${args.query}"`
    },
    // その素材を使うレシピ（逆引き。設計書 47 §4）。have なら今の持ち物だけで作れるもの
    uses_of (args) {
      if (!knowledge.index) refuse('the recipe list is not loaded')
      const item = String(args.item ?? '')
      if (!knowledge.md.itemsByName[item]) refuse(`unknown item ${item} (check the name with item_info)`)
      const { total, recipes } = usesOf(knowledge.index, item, inventoryCounts(bot), Boolean(args.have))
      if (!recipes.length) return args.have ? `nothing can be made now with ${item} and what you carry (${total} recipes use it)` : `no recipe uses ${item}`
      return { total, shown: recipes.length, recipes }
    },
    // 食べられる物と取り方（持ち物で食べられる・作れる物が先。query: 名前・日本語・材料に含まれる語）
    find_foods (args) {
      names ??= loadNames()
      const r = foodsInfo({ md: knowledge.md, knowledge, index: knowledge.index, names }, inventoryCounts(bot), {
        query: args.query, have: Boolean(args.have), limit: args.limit ?? undefined
      })
      // 名前や語で絞らないとき（または now: true）は、いま手に入る所を先に添える（持ち物、チェスト、見えている動物、熟した作物、覚えている場所）
      const now = args.now ?? !String(args.query ?? '').trim()
      const here = now ? { now: foodNow(bot, state, knowledge) } : {}
      return r.foods.length ? { ...here, ...r } : (now ? here : `no food matches "${args.query ?? ''}"${args.have ? ' among what you carry or can make now' : ''}`)
    },
    // 1 マスの今（ブロックの状態、隣、届くか、間に何があるか、断られる理由）。座標を省けば自分の今（設計書 85）
    inspect (args) {
      if (args.x == null && args.y == null && args.z == null) return inspectSelf(bot, state)
      const pos = reach(position(args), MAX_TOOL_DISTANCE, (q) => `the block at ${fmt(q)}`)
      return inspectCell(bot, state, pos)
    },
    // ブリッジが残した直近の失敗の記録（door: 開かないドア、place: 置けなかった、stuck: 歩けなかった）
    diagnostics (args) {
      return readDiagnostics(args.kind ? String(args.kind) : null, args.n)
    },
    how_to_get (args) {
      const count = Math.max(1, Math.floor(Number(args.count ?? 1)))
      const [r] = deps.check([{ predicate: 'have', item: args.item, count }])
      return { met: r.met, steps: r.lines, impossible: r.impossible }
    }
  }

  // 1 つの道具を呼ぶ: { ok, result, seconds, refused? }
  // 関門の順序（unknown → reflex → busy → 台帳 → 道具ごとの検査 → 夜の籠もり → 実行）は test/tool-gates.test.mjs が守る
  return async function callTool (name, args = {}) {
    try {
      if (queries[name]) return { ok: true, result: queries[name](args), seconds: 0 }
      if (!build[name]) refuse(`unknown tool ${name}`)
      notes = []
      if (state.reflex) refuse('the reflex is handling a nearby threat')
      if (state.busy) refuse('another action is running')
      // 前に同じ場所へ同じ手を断られた・着けなかったなら、やり直さない（理由と、別の手を選ぶよう返す）
      // （壊すと決めた掘り直し demolish と、保護を承知の override は、家や建物を断られた台帳を通り越す）
      const tried = args.demolish === true || args.override === true ? null : refusalAt(state, name, positionOf(args))
      if (tried && freeGuards()) notes.push(`an earlier ${name} at ${fmt(tried.pos)} failed ${Math.max(0, Math.round((Date.now() - tried.at) / 60000))} min ago: ${tried.reason}`)
      else if (tried) refuse(`${name} at ${fmt(tried.pos)} was tried before and will not work: ${tried.reason}; choose another place or another way`)
      const { c, label } = build[name](args)
      const home = state.home
      if (home && needsOutside(c, home)) {
        const { sheltering, day, why } = shelterOf(bot, home)
        // 外に出る危なさが極めて高いとき（risk.mjs の SHELTER_LEVEL）だけ止める。昼にドアの前で待つ敵とは
        // 戦ってよい（cleared と同じ）。夜は、今の小目標が cleared（倒しに出ると決めた）のときだけ
        const fighting = day || state.goal?.spec?.predicate === 'cleared'
        const confront = fighting && c.verb === 'attack' && c.hostile
        if (sheltering && !confront) {
          refuse(`staying inside: too dangerous to go out (${why}). ` +
            (day ? 'Attack them, wait, or reduce the risk' : 'Wait, reduce the risk, or choose cleared to fight'))
        }
        if (confront) c.confront = true
      }
      const r = await deps.run(c, label ?? `${name}(${JSON.stringify(args)})`)
      return notes.length ? { ...r, result: `${r.result} (${notes.join('; ')})` } : r
    } catch (e) {
      // 実行の失敗は run() が結果として返す。ここに来るのは、断ったときと、引数が知識と合わない
      // とき（知らないアイテムなど）: どちらも理由を返し、プレイは止めない
      if (!(e instanceof Refused)) console.log(`[tool] ${name} の引数を使えなかった: ${e.stack}`)
      else noteRefusal(state, name, positionOf(args), e.message)
      return { ok: false, refused: true, result: `refused: ${e.message}`, seconds: 0 }
    }
  }
}
