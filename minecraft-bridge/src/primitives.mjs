// プリミティブの行動と、それらが共有する移動の規則。
//
// 各プリミティブは具体的な対象（ブロック、エンティティ、アイテム）に対して有界な1ステップを実行し、
// ワールドで確かめた結果を返す。失敗は投げ、成功として返すことはない。
// 実行関数は AbortSignal（ダメージを受けた、必要が出た、POST /abort）を受け取り、発火したらすぐ止まる:
// ループがそれを確かめ、呼び出し側が経路移動と採掘を取り消す。

import { once } from 'node:events'
import pathfinderPkg from 'mineflayer-pathfinder'
import vec3Pkg from 'vec3'
import { torchesNeeded, TORCH_SPACING } from './trail.mjs'
import { THREAT_RADIUS, round, inventoryCounts, nearbyEntities, threats, isHostile, fillItem, standingCell, isLog, isUnderground } from './observe.mjs'
import { surfaceExits } from './surface.mjs'
import { watchDrop } from './dropwatch.mjs'
import { findSite, placeOne } from './build.mjs'
import { buildAllowsDig, growHome, inHomeRoom, isHomeCell } from './builds.mjs'
import { craftWithRecipeBook } from './craft.mjs'
import { jumpAndPlace, shelteredFrom, enterHome, leaveHome, doorRouteOk, exitSpots, EXIT_DROP, isDoorstepFloor, doorstepColumn, isDoorOpen, bedSpot, chestSpot, inHouse, isInside, digExit, stepOut, repairWall, shelterOf, hasBed, inStandingBuilding } from './home.mjs'
import { homeChests, rememberChest, forgetChest, rememberFurnace, forgetFurnace, rememberSite } from './memory.mjs'
import { smeltingProduct, CHANCE_DROP_BLOCKS } from './knowledge.mjs'
import { surveySite, SURVEY_REACH } from './survey.mjs'
import { walkTo as goto, isLoopCell, markNoPath, isNoPath, STUCK_MS } from './move.mjs'
import { teachDoors } from './doors.mjs'
import { digTargets, logHeightAbove, dirtForLogHeight, SCAFFOLD_SOURCES } from './world.mjs'
import { isSaplingItem, recordPlanting, cropByBlock, isRipe, CROPS, CLEARABLE } from './farming.mjs'
import { levelStatus, noteLevelZone, noteLevelArea, farmLevel, FARM_MARGIN, TERRAIN, farmRebuildable, soilItem } from './leveling.mjs'
import { noteDig, noteShaft, usableShaft } from './quarry.mjs'
import { canHold, isTossed, noteTossed, emptySlots } from './spare.mjs'
import { liquidReason } from './liquid.mjs'
import { supportFor } from './placing.mjs'
import { isSleeping, sleeperOf } from './sleepers.mjs'
import { SINGLE_CHEST, defragChests, contentsSignature } from './storage.mjs'
import { burrowSpot, inBurrow, inOpenShaft, sealSupport, canStepOut, BURROW_DEPTH } from './burrow.mjs'
import { furnitureInHome } from './furniture.mjs'
import { refusalAt } from './refusals.mjs'
import { mineStatus, mineDanger, mineBlocks, plannedBlocks, stopMine, updateStatus, onMineCell, inMineBody, designMine, layoutStatus, onFarmGround, FARM_DIG_COST, FENCE_ITEM, FENCE_GATE_ITEM, FacilityError } from './facilities.mjs'
import { mineFromHome, mineSites, protectedRock } from './mining.mjs'
import { sealStock, openings, sealCell, plugCell } from './sealing.mjs'
import { isSwimming, swimToward } from './swim.mjs'
import { closedSpace, closedSpaceCached, isolatedSpace, pillarUnder } from './reach.mjs'
import { tooDarkAt, skyLightAt, MIN_LIGHT } from './light.mjs'
import { holdsWater } from './breath.mjs'
import { reachLand } from './land.mjs'
import { digOutRoute } from './dig-out.mjs'
import { campSite, campAt, campSources, campCount, buildCamp, campExit, HAND_BLOCKS } from './camp.mjs'
import { straySources, scoopBack } from './water-spill.mjs'
import { boatItem, riding, waterCrossing, surfaceAt, placeBoat, mountBoat, drive, leaveBoat, breakBoat, nearbyBoat, nearestShore, driveToShore, EXTEND_M, BoatUnusable, CROSSING_LOOK_M, BOAT } from './boat.mjs'

const { Movements, goals } = pathfinderPkg
const { Vec3 } = vec3Pkg
export const REACH = 4.5 // サバイバルで目からブロックに届く距離
// 体力がここまで下がったら危険: 欲求として示し、戦いや逃走はここで止める
export const HEALTH_CRITICAL = 8
// 7 未満でダッシュできなくなる。ここから先は飢えているので、少し害のあるものでも食べる価値がある
export const HUNGER_URGENT = 6
export const EAT_WASTE_OK = 3 // 食べて満腹度 20 を超えて捨ててよい点数（これを超える食べ物は、満腹度が足りなくなってから）
const EAT_MAX = 8 // 1 回の食べる手で続けて食べる数の上限
export const TABLE_SEARCH_RADIUS = 32
const HOSTILE_AVOID_RADIUS = 5
const HOSTILE_STEP_COST = 20
const HOSTILE_CACHE_MS = 1000
const LEAVES_STEP_COST = 10
const FLEE_DISTANCE = 16
const FLEE_MS = 15000 // これだけ逃げても離れられなければ、逃げるのをやめる（続きは反射が数える）
const FLEE_DAMAGE = 6 // 逃げる間にこれだけ（骷髏の矢 2 本）ダメージを受けたら、逃げるのをやめる
const FIGHT_LOST_M = 24 // 相手がこれより離れたら見失った
const FIGHT_NO_DIG_BELOW = 3 // 相手がこれ以上下にいる間は、掘って近づかない
const FIGHT_MAX_DESCENT = 4 // 追って降りるのは、戦い始めの高さからこれまで（それより下の相手は追わない）
const FIGHT_MELEE_M = 3 // これより近い相手とは殴り合える（遠くから撃たれているのとは別）
const FIGHT_UNDER_FIRE = 6 // 近づけない相手から（殴り合えない距離で）これだけ（ピリジャーの矢 2 本）ダメージを受けたら、戦いをやめる
const FIGHT_OUT_OF_REACH_HEALTH = 6 // 武器があっても、近づけない敵がいる間にこの体力を切ったら、戦いをやめる
// 戦いがこの理由でやめた結果（反射が、同じ相手とまた戦わずに逃げる）
export const FIGHT_OUT_OF_REACH = /still alive: (under fire|out of reach)/
const FIGHT_LOST_SLACK = 4 // 始めから 24 m より遠い相手（狩りの候補は 32 m まで）は、始めの距離よりこれ以上離れたら見失った
const PICKUP_WAIT_MS = 1500
const DROP_COLLECT_RADIUS = 8
const RECOVER_RADIUS = 24 // 死んだ場所のまわりに散らばった持ち物
const WAIT_TICKS = 200
const GLANCE_TICKS = 50
const GLANCE_ANGLE = Math.PI / 3
export const EXPLORE_DISTANCE = 24
const EXPLORE_MIN_PROGRESS = 5
const RECIPE_UNLOCK_TICKS = 40
// この時刻から眠れる（Mineflayer 自身の判定: 12541..23458）
export const SLEEP_FROM = 12541
export const SLEEP_UNTIL = 23458
const TIME_UPDATE_TIMEOUT_MS = 3000 // サーバーは毎秒時刻を送る

const WEAPONS = ['netherite_sword', 'diamond_sword', 'iron_sword', 'stone_sword', 'golden_sword', 'wooden_sword',
  'netherite_axe', 'diamond_axe', 'iron_axe', 'stone_axe', 'golden_axe', 'wooden_axe']
export const isLeaves = (name) => !!name?.endsWith('_leaves')

// 注意: 経路移動の取り消しには setGoal(null) を使う。pathfinder.stop() は次の移動ティックで
// 消えるフラグを立てるだけで、止まっているときに呼ぶと次の goto() がすぐ失敗する。
// 着いたかを、歩いた後の実際の位置で言う（2026-09-28: 家の前の穴の中で動かないまま「arrived near」を
// 返し、着いたのに入れないと読まれた）。着いていなければ失敗にする
export function arrivalReport (bot, goal, target) {
  const p = bot.entity.position
  const at = `${round(p.x)},${round(p.y)},${round(p.z)}`
  const f = standingCell(bot)
  // 水の中にいて、目標が水でないなら、陸に着いていない（岸の手前で「着いた」と読んでいた: 2026-10-01）
  if (isSwimming(bot) && !holdsWater(bot.blockAt(new Vec3(target.x, target.y, target.z)))) {
    throw new Error(`did not reach ${target.x},${target.y},${target.z}: you are in the water at ${at}, not on land ` +
      `(${round(Math.hypot(target.x + 0.5 - p.x, target.z + 0.5 - p.z))} m away horizontally)`)
  }
  // 目的地が空中（そのマスも、その下の立っているセルまでも空気）で、水平には着いていれば、着いた: LLM が書いた y は地面の
  // 高さの推測で、2 ブロック上の空中を指していた（水平 0 m で「高さ -2」の失敗が 3 回続いた: 2026-10-07）
  const horizontal = Math.hypot(target.x + 0.5 - p.x, target.z + 0.5 - p.z)
  if (!goal.isEnd(f) && f.y < target.y && horizontal <= Math.sqrt(goal.rangeSq ?? 1) && airBetween(bot, f, target, target.y - 1)) {
    return `arrived near ${target.x},${target.y},${target.z} (now at ${at}: the target is in the air, the ground there is at y ${f.y})`
  }
  if (!goal.isEnd(f)) {
    throw new Error(`did not reach ${target.x},${target.y},${target.z}: stopped at ${at} ` +
      `(${round(Math.hypot(target.x + 0.5 - p.x, target.z + 0.5 - p.z))} m away horizontally, height ${f.y - target.y >= 0 ? '+' : ''}${f.y - target.y})`)
  }
  return `arrived near ${target.x},${target.y},${target.z} (now at ${at}, height ${f.y - target.y >= 0 ? '+' : ''}${f.y - target.y})`
}

// 目的地の列で、立っているセルの上から目的地まで（と、目的地の下の 1 マス）、すべて空気（通れる）か。
// 目的地の下が床なら、目的地は立てるセルで、空中ではない（穴の中から壁のこちら側へ着けていない）
function airBetween (bot, from, to, belowY) {
  for (let y = Math.min(from.y + 1, belowY); y <= to.y; y++) {
    const b = bot.blockAt(new Vec3(to.x, y, to.z))
    if (!b || b.boundingBox !== 'empty') return false
  }
  return true
}

// 積む土として掘れる土（原木の真下とそのまわりの列は、積む足場の地面なので掘らない）: 原木に近い順
function scaffoldSources (bot, state, log) {
  return SCAFFOLD_SOURCES.flatMap((n) => digTargets(bot, state, n, 12))
    .filter((p) => HAND_BLOCKS.test(bot.blockAt(p)?.name ?? '') && Math.max(Math.abs(p.x - log.x), Math.abs(p.z - log.z)) > 1)
    .sort((a, b) => a.distanceTo(log) - b.distanceTo(log))
}

// 原木に届くのに積む土が足りなければ、素手で近くの土を掘って集める（docs/design/106）
async function gatherScaffold (bot, state, log, signal) {
  const height = logHeightAbove(bot, log)
  if (height === null) return
  const need = dirtForLogHeight(height)
  let dug = 0
  while ((inventoryCounts(bot).dirt ?? 0) < need) {
    signal.throwIfAborted()
    const have = inventoryCounts(bot).dirt ?? 0
    const [target] = scaffoldSources(bot, state, log)
    if (!target) throw new Error(`the log at ${log.x},${log.y},${log.z} is ${height} blocks above the ground: reaching it takes ${need} dirt and you carry ${have}; no dirt within reach to dig for the rest`)
    if (++dug > need + 6) throw new Error(`dug ${dug - 1} blocks of dirt and still carry only ${have} of the ${need} needed to reach the log at ${log.x},${log.y},${log.z}`)
    await PRIMITIVES.dig(bot, state, { block: bot.blockAt(target).name, pos: target }, signal)
  }
}

// 次に置く計画のブロック: 候補が指したブロックがあればそれ。ただし足元がない（床が抜けたドア）なら、足元の土から
// （pending の先頭）: ドアだけを先に置こうとして、床がなく失敗し続けた（2026-10-06）
export function planBlockToPlace (bot, plan, planBlock) {
  const wanted = planBlock && !plan.isPlaced(bot, planBlock) ? planBlock : null
  const floorBelow = wanted && bot.blockAt(plan.worldPos(wanted).offset(0, -1, 0))
  return wanted && !(floorBelow && floorBelow.boundingBox === 'empty') ? wanted : plan.pending(bot)[0]
}

const ROCK_ESCAPE = new Set(['go_home', 'surface', 'recover_items', 'dig_in', 'wall_in', 'reach_land', 'leave_burrow', 'climb_out', 'dig_out', 'flee', 'sleep_at', 'exit_wall']) // 深い岩も掘ってよい行動（出られなくならないように）
const CLIMB_MAX = 24 // 閉じた所から出るために積む高さの上限
const DESCEND_MAX = 200 // 柱を掘り下げる最大の数
const CLIMB_BLOCK_MS = 5 * 60 * 1000 // 積んで出るのに失敗したあと、聞かずに実行しない間
const DIG_OUT_ROUNDS = 3 // 壁を掘って出るのを、道を選び直して試す回数
const HANDLESS_CACHE_MS = 2000 // 持ち物が変わる（道具を作る・壊れる）のを、この間隔で読み直す

// 水に入るコスト（pathfinder の既定は 1 で、水の中がほぼ無料）: 遠い座標への goto が海を歩いて渡り、海底で息が尽きて溺れた
// （2026-10-07）。水の中の 1 マスを、陸の 10 マスぶんの遠回りとして数える（浅い川を渡るのは今までどおり。ほかに道がなければ渡る）
const LIQUID_STEP_COST = 10
export function configureMovements (bot, state) {
  const m = new Movements(bot)
  m.liquidCost = LIQUID_STEP_COST
  // ケルプ・海草は水の中の植物: pathfinder は当たり判定のない空気とみなし、海底を歩く道（水面から 3 ブロック下
  // から始まる）を作って、水面で動けなくなった（2026-10-01、docs/design/73_swimming.md）
  for (const name of ['kelp', 'kelp_plant', 'seagrass', 'tall_seagrass', 'bubble_column']) {
    const b = bot.registry.blocksByName[name]
    if (b) m.liquids.add(b.id)
  }
  // まだ読み込まれていないチャンクでは、pathfinder は位置のない代わりのブロックを渡す（歩けることは
  // ない）。その位置を読むコスト関数は物理ティックの中で例外を投げてしまう。
  const step = (cost) => m.exclusionAreasStep.push((block) => block.position ? cost(block) : 0)
  // 建築中の建物の上は歩かない: 途中の壁が段差になってボットが登り、壁の上から屋根を置いたり、
  // 上で動けなくなったりする。
  // 壁の輪の内側（部屋）は、足が 1 段高い所（ベッド、チェスト、作業台の上）とその頭の高さ（o.y + 2 まで）を
  // 除く: 100 以上は「通れない」なので、ベッドの上に立つと頭のセルがここに掛かり、どこへも経路が立たず、
  // 家の中から出る道も戦いに行く道も「No path to the goal!」になった（2026-10-07、docs/design/93）
  step((block) => {
    const o = state.plan?.origin
    if (!o) return 0
    const p = block.position
    const { width, depth } = state.plan.size
    const inside = p.x >= o.x && p.x < o.x + width && p.z >= o.z && p.z < o.z + depth
    if (!inside || p.y <= o.y + 1) return 0
    const room = p.x > o.x && p.x < o.x + width - 1 && p.z > o.z && p.z < o.z + depth - 1
    return room && p.y <= o.y + 2 ? 0 : 101
  })
  // 敵対モブから離れる: そのまわりを通る経路のコストを上げ、家に歩いて帰るときに、逃げた直後の
  // クリーパーにまた突っ込まずに迂回するようにする。
  let hostiles = []
  let hostilesAt = 0
  step((block) => {
    if (Date.now() - hostilesAt > HOSTILE_CACHE_MS) {
      hostiles = nearbyEntities(bot).filter(({ e, dist }) => dist <= THREAT_RADIUS * 2 && isHostile(bot, e)).map(({ e }) => e.position.clone())
      hostilesAt = Date.now()
    }
    return hostiles.some((h) => h.distanceTo(block.position) <= HOSTILE_AVOID_RADIUS) ? HOSTILE_STEP_COST : 0
  })
  // 木の上に行かない: 葉の上を歩くと、降りにくい樹冠に出てしまう。
  step((block) => isLeaves(bot.blockAt(block.position.offset(0, -1, 0))?.name) ? LEAVES_STEP_COST : 0)
  // プレイヤーのように、歩きながら葉と自然の地形を掘り、土を積んで登る。掘れない・積めないと、
  // 下りられても戻れない穴ができる（town4c: 豚を追って洞窟に下り、出られなくなった）。
  // 家、前の家、建てている家は壊さないし、そこに足場も置かない。
  // 自然の地形（leveling.mjs の TERRAIN）。置いた物（丸石、板材、チェストなど）は入れない
  m.exclusionAreasBreak.push((block) => block.position && (isLeaves(block.name) || TERRAIN.test(block.name)) && !inHouse(state, block.position) ? 0 : 100)
  // 道具が要るブロック（石にツルハシ）は、その道具を持っていないと掘って進まない: 素手で石や深層岩を
  // 掘り続け、何も落ちないまま地下の奥へ潜った（2026-10-01: 逃走・回収中。docs/design/80_underground_deaths.md）
  // ただし閉じ込められて出られない（歩ける所が閉じている）ときは、出る行動に限り素手で掘ってよい: 道具を作る材料も
  // 取りに行けないまま動けなくなる（2026-10-04: 採掘場の底で木のつるはしが壊れ、約 50 分出られなかった）
  const handless = new Map()
  let handlessAt = 0
  let shutIn = false
  m.exclusionAreasBreak.push((block) => {
    if (!block.position || !block.harvestTools || !Object.keys(block.harvestTools).length) return 0
    if (Date.now() - handlessAt > HANDLESS_CACHE_MS) {
      handless.clear()
      handlessAt = Date.now()
      shutIn = ROCK_ESCAPE.has(state.current?.verb) && isUnderground(bot) && !!closedSpaceCached(bot)
    }
    if (!handless.has(block.type)) handless.set(block.type, !chooseTool(bot, block))
    return handless.get(block.type) && !shutIn ? 100 : 0
  })
  // （水や溶岩に接したブロックは、mineflayer-pathfinder の dontCreateFlow（既定で真）がもともと壊さない。
  // 同じ判定をここに足すと、壊す候補ごとの読み取りが増えるだけで、経路探索が遅くなった）
  // ただしドアの前の床の穴は埋めてよい（その上に立ってドアへ入る。home.mjs の isDoorstepFloor）
  m.exclusionAreasPlace.push((block) => block.position && (!inHouse(state, block.position, 1) || isDoorstepFloor(state, block.position)) ? 0 : 100)
  // 採掘場の空ける所には足場を置かない（階段や坑道を埋めない。設計書 47 §16）
  m.exclusionAreasPlace.push((block) => block.position && onMineCell(state, block.position) ? 100 : 0)
  // らせん階段の柱と段の床は壊さない（経路探しが近道に掘ると階段が崩れる）
  m.exclusionAreasBreak.push((block) => block.position && inMineBody(state, block.position) ? 100 : 0)
  // 採掘場のほかの深い岩は掘らない（岩の中は採掘場の坑道と枝だけ。設計書 86）。帰る・脱出する行動は、閉じた所から
  // 出られなくならないように除く
  m.exclusionAreasBreak.push((block) => block.position && !ROCK_ESCAPE.has(state.current?.verb) && protectedRock(state, block.position, block.name) ? 100 : 0)
  // ドアの前の列（外に立つセルとその下）は、どの行動でも掘らない: 水を汲むのに戸口から 18 マス掘り下げて、家に入れない穴を
  // 残した（2026-10-07、docs/design/100）
  m.exclusionAreasBreak.push((block) => block.position && doorstepColumn(state, block.position) ? 100 : 0)
  // 畑の作物のマスの地面は、掘るのに費用をかける（禁止はしない: 直す手を妨げない。facilities.mjs の FARM_DIG_COST）。
  // 閉じた所から出る行動は除く
  m.exclusionAreasBreak.push((block) => block.position && !ROCK_ESCAPE.has(state.current?.verb) && onFarmGround(state, block.position) ? FARM_DIG_COST : 0)
  // 掘っては置く繰り返しになったセルは、しばらく掘らず置かない（move.mjs の guardDigLoops）
  m.exclusionAreasBreak.push((block) => block.position && isLoopCell(state, block.position) ? 100 : 0)
  m.exclusionAreasPlace.push((block) => block.position && isLoopCell(state, block.position) ? 100 : 0)
  // 丸石は石の道具とかまどの材料なので足場にしない。土は掘ればいつも手に入る
  m.scafoldingBlocks = [bot.registry.itemsByName.dirt.id]
  m.allow1by1towers = true
  // ドアと柵のゲートは、向きに沿ってまっすぐなら通れる（歩きながら開け閉めする。家のドアは除く）
  teachDoors(m, state)
  bot.pathfinder.setMovements(m)
  // 歩きながら掘るときの道具も同じ選び方で（元のものは速い道具がないと持ち物の最初のものを返す）
  bot.pathfinder.bestHarvestTool = (block) => chooseTool(bot, block)
}

// エンティティから `distance` 以上離れたどこかに行く。エンティティが2ブロックほど動くたびに経路を
// 立て直す。GoalInvert(GoalFollow(e, d)) は同じに見えるが、エンティティが d ブロック動いてからしか
// 立て直さない: ボットはモブが「いた」場所から遠いところまで走り、モブが近づく間立ち止まっていた。
const REPLAN_MOVE = 2
class GoalAwayFrom extends goals.Goal {
  constructor (entity, distance) {
    super()
    this.entity = entity
    this.distance = distance
    this.from = entity.position.clone()
  }

  heuristic (node) {
    return Math.max(0, this.distance - Math.hypot(node.x - this.from.x, node.z - this.from.z))
  }

  isEnd (node) {
    return Math.hypot(node.x - this.from.x, node.z - this.from.z) >= this.distance
  }

  hasChanged () {
    if (this.entity.position.distanceTo(this.from) < REPLAN_MOVE) return false
    this.from = this.entity.position.clone()
    return true
  }

  isValid () {
    return this.entity.isValid !== false
  }
}

const goNear = (bot, pos, range, signal) => goto(bot, new goals.GoalNear(pos.x, pos.y, pos.z, range), signal)

// 作業台やかまどを置ける場所: [{ pos, where: 'inside' | 'nearby' }]。どちらに置くかは選ぶ側
// （Jev か Gemini）が決める。家の中は、ドアの通り道を空け、ベッドを置く場所を残せるときだけ
// （roomSpot）。建てる予定の家の敷地と、家の壁のまわりには置かない（壁が建てられなくなる）。
// かまど類は家の中だけ（外や地下で腰を据えて作業すると襲われる。2026-10-04: 地下でかまどを置いて精錬して死んだ。
// 外にいるときは、置く手は家へ帰る手になる: candidates.mjs の place）。作業台は家の外にも置くが、地下は除く
export function stationSpots (bot, state, item = null) {
  const out = []
  if (state.home && isInside(bot, state.home)) {
    const pos = roomSpot(bot, state.home)
    if (pos) out.push({ pos, where: 'inside' })
  }
  if (state.home && FURNACE_ITEM.test(item ?? '')) return out
  if (state.home && shelterOf(bot, state.home).sheltering) return out
  if (isUnderground(bot)) return out
  const pos = nearbySpot(bot, state)
  if (pos) out.push({ pos, where: 'nearby' })
  return out
}

// 1 つだけ要るとき（置けるかの確認、候補に位置がないとき）: 家の中を先に
export function stationSpot (bot, state, item = null) {
  return stationSpots(bot, state, item)[0]?.pos ?? null
}

// 家の中の空いたセル（ドアの列を外す。chestSpot と同じ）。ベッドがまだなければ、置いた後も
// ベッドの場所が残るものだけ
function roomSpot (bot, home) {
  const spot = chestSpot(bot, home)
  if (!spot || hasBed(bot, home)) return spot
  const occupied = { ...bot, blockAt: (p) => p.equals(spot) ? { name: 'crafting_table', boundingBox: 'block', position: p } : bot.blockAt(p) }
  return bedSpot(occupied, home) ? spot : null
}

// 外: 完全なブロックの上の空気で、ボットから 2〜3 ブロック（立っている場所ではない）、上下1ブロック
// まで（自分の高さの決まった輪だけでは、坂や洞窟で何も見つからなかった）。家と計画中の敷地の
// まわりには置かない。近い順
function nearbySpot (bot, state) {
  const me = bot.entity.position.floored()
  const spots = []
  for (let dx = -3; dx <= 3; dx++) {
    for (let dz = -3; dz <= 3; dz++) {
      if (Math.max(Math.abs(dx), Math.abs(dz)) < 2) continue
      for (const dy of [0, -1, 1]) {
        const pos = me.offset(dx, dy, dz)
        const spot = bot.blockAt(pos)
        const ground = bot.blockAt(pos.offset(0, -1, 0))
        // 建っているもののまわりには置かない。建築予定地には仮設してよい（建てる側が壊す）
        if (spot?.name !== 'air' || ground?.boundingBox !== 'block' || inStandingBuilding(bot, state, pos, 1)) continue
        spots.push({ pos, d: dx * dx + dz * dz + dy * dy })
        break
      }
    }
  }
  return spots.sort((a, b) => a.d - b.d)[0]?.pos ?? null
}

// たいまつはボットが立っている場所に置く: 完全なブロックの上の空いたセル（液体でない）
export function torchSpot (bot) {
  const feet = bot.entity.position.floored()
  const cell = bot.blockAt(feet)
  const floor = bot.blockAt(feet.offset(0, -1, 0))
  return cell?.boundingBox === 'empty' && !/water|lava/.test(cell.name) && floor?.boundingBox === 'block' ? feet : null
}

// 長い道のりは、着くまで 1 回の行動で区間（LEG m）ごとに歩く。区間ごとに、先の長い水をボートで渡るかを
// 見直す（チャンクは進むにつれて届く）。時間では止めない: 止まるのは必要が出たとき（runner）、動けない・
// 経路がないとき（move.mjs）。区間を歩き終えたら null を返し、呼び出し側が最後の数 m を歩く
export const LEG = 48
async function legToward (bot, state, target, what, signal) {
  for (;;) {
    signal.throwIfAborted()
    const crossed = await crossWater(bot, state, target, what, signal)
    // 渡った結果は、行動の結果には出ない（呼び出し側が捨てる）ので、ここで残す
    if (crossed) console.log(`[boat] ${what} への途中で水を渡った: ${crossed}`)
    // 岸に着かずにボートの上で止まった（進めない、先が読み込まれていない）: 乗ったままでは歩けない
    // （pathfinder は乗っている間に動かない）ので、理由を返して次の手に任せる
    if (riding(bot)) throw new Error(crossed ?? `still in the boat on the way to ${what}`)
    const me = bot.entity.position
    const far = Math.hypot(target.x - me.x, target.z - me.z)
    if (far <= LEG) return null
    const k = LEG / far
    bot.actionPhase = `walking toward ${what} (${round(far)}m away)`
    await goto(bot, new goals.GoalNearXZ(me.x + (target.x - me.x) * k, me.z + (target.z - me.z) * k, 3), signal)
    bot.actionPhase = ''
  }
}

// 長い水（設計書 37）: ボートを持っていれば、岸まで歩いて置き、乗って渡る（岸に着いたら降りてボートを
// 拾う）。乗っている途中なら続きを漕ぐ。持っていなければ泳いで歩き、水があったことを覚える（goals が、
// 材料があればボートを作ることを勧める）
export const WATER_AHEAD_MS = 10 * 60 * 1000
const BOARD_WITHIN_M = 24 // 水がこれより先から始まるなら、まず歩く（次の区間でまた見る）
const MIN_BOARD_M = 1.8 // ボートを置くセルの中心から自分までの、水平の最小の距離
export async function crossWater (bot, state, target, what, signal, minWater = undefined) {
  if (riding(bot)) return await rideBoat(bot, state, target, what, signal)
  const crossing = waterCrossing(bot, target, undefined, undefined, minWater)
  if (!crossing) return null
  if (!boatItem(bot)) {
    state.waterAhead = { at: Date.now(), length: crossing.length }
    // ボートがなければ泳いで渡る（pathfinder は水の中から動けない: docs/design/73_swimming.md）。水が遠くても、
    // 先に水際まで歩く（区間の歩きが水の上に伸びると、pathfinder は道を作れない）
    return await swimAcross(bot, target, what, signal, crossing)
  }
  if (crossing.from > BOARD_WITHIN_M) return null
  try {
    return await rideBoat(bot, state, target, what, signal, crossing)
  } catch (e) {
    if (!(e instanceof BoatUnusable)) throw e
    return await swimInstead(bot, target, what, signal, crossing, e)
  }
}

// ボートを置けない・乗れないとき（水の中で毎回置けず、沖で動けなくなった: 2026-10-07）、泳いで渡る。
// すでに水の中で、向こう岸が見えないなら（沖へ向かう途中）、目標へ泳がず、いちばん近い岸へ戻る
async function swimInstead (bot, target, what, signal, crossing, why) {
  console.log(`[boat] ボートが使えない（${why.message}）: 泳ぐ`)
  const note = `the boat could not be used (${why.message}); `
  if (crossing.landAfter || !isSwimming(bot)) return note + await swimAcross(bot, target, what, signal, crossing)
  const shore = nearestShore(bot)
  if (!shore) throw new Error(`${why.message}; you are in open water and no shore is loaded within ${CROSSING_LOOK_M}m to swim to`)
  bot.actionPhase = `swimming back to the nearest shore (${round(shore.x)},${round(shore.z)})`
  const r = await swimToward(bot, shore, signal)
  bot.actionPhase = ''
  signal.throwIfAborted()
  const p = bot.entity.position
  if (!r.landed && r.why !== 'arrived') throw new Error(`${note}swam ${r.moved}m toward the shore at ${round(shore.x)},${round(shore.z)} but ${r.why}: you are still in the water at ${round(p.x)},${round(p.y)},${round(p.z)}`)
  return `${note}no shore was in sight toward ${what}, so swam ${r.moved}m back to the nearest shore at ${round(shore.x)},${round(shore.z)} (${r.why})`
}

// 水際まで歩き、向こう岸まで泳いで上がる。岸に着けなければ、水の中にいることを言って失敗にする
async function swimAcross (bot, target, what, signal, crossing) {
  if (!isSwimming(bot) && crossing.from > 2) {
    bot.actionPhase = `walking to the water on the way to ${what}`
    await goto(bot, new goals.GoalNear(crossing.entry.x, crossing.entry.y + 1, crossing.entry.z, 2), signal)
  }
  bot.actionPhase = `swimming toward ${what}`
  const r = await swimToward(bot, target, signal)
  bot.actionPhase = ''
  signal.throwIfAborted()
  const p = bot.entity.position
  if (!r.landed && r.why !== 'arrived') throw new Error(`swam ${r.moved}m toward ${what} but ${r.why}: you are still in the water at ${round(p.x)},${round(p.y)},${round(p.z)}`)
  return `swam ${r.moved}m across the water toward ${what} (${r.why})`
}

async function rideBoat (bot, state, target, what, signal, crossing = null) {
  let boat = bot.vehicle
  if (!riding(bot)) {
    // 届く所に水面がなければ（離れている、高い土手の上）、水際まで行く
    let water = waterToBoard(bot, target)
    if (!water) {
      bot.actionPhase = `walking to the water on the way to ${what}`
      await goto(bot, new goals.GoalNear(crossing.entry.x, crossing.entry.y + 1, crossing.entry.z, 1), signal)
      bot.actionPhase = ''
      water = waterToBoard(bot, target)
    }
    if (!water) throw new BoatUnusable(`no open water within reach to put the boat on (toward ${what})`)
    boat = await placeBoat(bot, water, signal)
    try {
      await mountBoat(bot, boat)
    } catch (e) {
      await pickUpBoat(bot, state, boat, signal)
      throw e
    }
  }
  bot.actionPhase = `riding a boat toward ${what}`
  const r = await driveToShore(bot, boat, target, signal, { log: (line) => console.log(line) })
  bot.actionPhase = ''
  signal.throwIfAborted()
  if (!r.landed) return `rode a boat ${r.moved}m toward ${what}; still on the water (${r.why})`
  if (r.openWater) return `rode a boat ${r.moved}m toward ${what}; still on open water, no shore within ${r.extended * EXTEND_M}m past the target (stay in the boat)`
  await leaveBoat(bot, `向こう岸に着いた（${r.why}）`)
  const kept = await pickUpBoat(bot, state, boat, signal)
  // 降りた所がまだ水の中なら、岸まで泳ぐ（降りたら陸、とは限らない: 2026-10-01）
  let swim = null
  if (isSwimming(bot)) swim = await swimToward(bot, target, signal)
  const p = bot.entity.position
  const landing = !isSwimming(bot) ? `got off at ${round(p.x)},${round(p.z)}` : `got off, but you are still in the water at ${round(p.x)},${round(p.y)},${round(p.z)} (${swim?.why ?? 'no swim'})`
  return `rode a boat ${r.moved}m across the water toward ${what} and ${landing} (${r.why})` +
    (kept ? '' : '; the boat could not be picked up again')
}

const RIDE_MIN_WATER = 3 // 道具 ride_boat で渡る水の長さの下限（自動で乗るのは BOAT_MIN_WATER から）
// 水の上で続きを漕げる行動（legToward を通る）。ほかの行動の前には降りる（runner）
export const TRAVELS = new Set(['place_plan', 'go_home', 'deposit', 'stash', 'tidy', 'withdraw', 'smelt', 'goto_memory', 'survey', 'goto_pos', 'explore', 'ride_boat'])

// ブロックのどの面を使うか: ボットの目に向いている面
// use_item の face（右クリックするブロックの面）の名前 → 向き
export const FACE_VECTORS = { up: [0, 1, 0], down: [0, -1, 0], north: [0, 0, -1], south: [0, 0, 1], east: [1, 0, 0], west: [-1, 0, 0] }

function faceToward (bot, pos) {
  const d = bot.entity.position.offset(0, 1.6, 0).minus(pos.offset(0.5, 0.5, 0.5))
  const axis = ['x', 'y', 'z'].reduce((a, b) => (Math.abs(d[b]) > Math.abs(d[a]) ? b : a))
  return new Vec3(axis === 'x' ? Math.sign(d.x) : 0, axis === 'y' ? Math.sign(d.y) : 0, axis === 'z' ? Math.sign(d.z) : 0)
}

// ボートを置く水: target への向きで、届く所の水面。ボート（幅 1.375）がまわりのブロックに当たると置けない
// ので、まわり 8 マスも水で上が空いている所（岸のすぐ隣は置けなかった: 2026-09-27）
function waterToBoard (bot, target) {
  const me = bot.entity.position
  const far = Math.hypot(target.x - me.x, target.z - me.z)
  const [ux, uz] = [(target.x - me.x) / far, (target.z - me.z) / far]
  const open = (cell) => [-1, 0, 1].every((dx) => [-1, 0, 1].every((dz) => {
    const p = cell.offset(dx, 0, dz)
    return /^(water|bubble_column|kelp|kelp_plant|seagrass|tall_seagrass)$/.test(bot.blockAt(p)?.name ?? '') &&
      /^(air|cave_air)$/.test(bot.blockAt(p.offset(0, 1, 0))?.name ?? '')
  }))
  for (let d = 1; d <= 5; d += 0.5) {
    const x = me.x + ux * d
    const z = me.z + uz * d
    const s = surfaceAt(bot, x, z, me.y)
    if (!s?.water) continue
    const cell = new Vec3(Math.floor(x), s.y, Math.floor(z))
    // 自分に近すぎる所には置かない: 水の中では、自分のすぐ隣のセル（中心まで 0.9m）に毎回置けなかった（2026-10-07。
    // ボートの箱は半幅 0.69 で、自分の半幅 0.3 と合わせて 1m 離れていないと重なる、という推測。boat-refusals.jsonl で確かめる）
    if (Math.hypot(cell.x + 0.5 - me.x, cell.z + 0.5 - me.z) < MIN_BOARD_M) continue
    if (open(cell) && cell.offset(0.5, 1, 0.5).distanceTo(bot.entity.position.offset(0, 1.6, 0)) <= REACH) return cell
  }
  return null
}

// 降りたボートを壊して拾う。持ち物に戻れば真
async function pickUpBoat (bot, state, boat, signal) {
  const had = bot.inventory.items().filter((i) => BOAT.test(i.name)).length
  const target = bot.entities[boat.id] ?? nearbyBoat(bot)
  if (target) await breakBoat(bot, target, signal)
  await collectNearbyDrops(bot, state, signal).catch((e) => { if (signal.aborted) throw e })
  return bot.inventory.items().filter((i) => BOAT.test(i.name)).length > had
}

// 行動の前に、乗っているボートから降りる（水の上を移動する行動は crossWater が続きを漕ぐ。runner が使う）
export async function disembark (bot, state, signal, why = '') {
  const boat = bot.vehicle
  await leaveBoat(bot, why)
  if (boat) await pickUpBoat(bot, state, boat, signal)
}

// 家から出る。ドアの道が使えれば leaveHome。使えなければ（爆発で床とドアの前がなくなった、など）、壁を掘って出る。
// 出口の外は EXIT_DROP 段まで低くてよい。出たら壁を閉じる（閉じられなければ、壁の穴を直す手が残る）
export function planLeave (bot, home) {
  if (doorRouteOk(bot, home)) return { route: 'door' }
  const spot = exitSpots(bot, home, [], { drop: EXIT_DROP })[0] ?? null
  return { route: 'wall', spot }
}

export async function leaveHouse (bot, state, home, signal, opts = {}) {
  if (!isInside(bot, home)) return
  const plan = planLeave(bot, home)
  if (plan.route === 'door') return await leaveHome(bot, home, signal, opts)
  const { sheltering, why } = shelterOf(bot, home)
  if (sheltering && !opts.confront) throw new Error(`staying inside: ${why}`)
  if (!plan.spot) throw new Error(`the way out through the door is gone (no floor in front of it, or no ground within ${EXIT_DROP} blocks outside) and no wall can be dug through to a place that is at most ${EXIT_DROP} blocks down`)
  console.log(`[home] ドアの外に出る道がない: ${plan.spot.side} の壁を掘って出る（外は ${plan.spot.step.y} の高さ）`)
  await digExit(bot, home, plan.spot, signal)
  // 掘ったブロックは壁を閉じるのに使う: 出る前に拾う（出てから拾うと、取りに中へ戻って閉じ込められる）
  await collectNearbyDrops(bot, state, signal)
  signal.throwIfAborted()
  await stepOut(bot, plan.spot, signal)
  try {
    await repairWall(bot, home, signal)
  } catch (e) {
    if (signal.aborted) throw e
    console.log(`[home] 壁を閉じられない（あとで直す）: ${e.message}`)
  }
}

// ボットに届く脅威: 閉じた家の中にいる間は外からのものはない
export function reachableThreats (bot, state) {
  return threats(bot).filter(({ e }) => !shelteredFrom(bot, state.home, e))
}

export const bestWeapon = (bot) => {
  const inv = inventoryCounts(bot)
  const name = WEAPONS.find((w) => inv[w])
  return name ? bot.inventory.items().find((i) => i.name === name) : null
}

// 相手がいなくなるまで戦う。見失った（FIGHT_LOST_M より離れた）、STUCK_MS の間近づけない・動けないときは
// やめる（時間では止めない）
export async function fight (bot, target, signal, maxMs = Infinity, minHealth = 0) {
  // 追う目標は一度だけ置く（動く相手には pathfinder が経路を立て直す）。置き直すたびに、歩く
  // 途中の掘り（雪など）が止まる（town4d: 同じ雪を 4 回掘りかけたまま、ゾンビに倒された）
  const follow = new goals.GoalFollow(target, 2)
  const stuck = stuckWatch(bot, () => target.position.distanceTo(bot.entity.position))
  let why = ''
  // 狩りの候補は 32 m まで出す。始めから 24 m より遠い相手を、始めの 1 回目で「見失った」にしない
  // （2026-09-30: 26 m の牛・羊を 0.0 秒で見失って、食料が集まらなかった）
  const lostAt = Math.max(FIGHT_LOST_M, target.position.distanceTo(bot.entity.position) + FIGHT_LOST_SLACK)
  const startedAt = Date.now()
  const startY = bot.entity.position.y
  let lastHealth = bot.health
  let underFire = 0
  const movements = bot.pathfinder.movements
  const canDig = movements?.canDig
  try {
    while (bot.entities[target.id] && !signal.aborted) {
      const d = target.position.distanceTo(bot.entity.position)
      // 下にいる相手には掘って近づかない（蜘蛛を追って石を掘り下り、洞窟の骷髏のところへ 15 m 落ちて死んだ:
      // 2026-10-03）。同じ高さか上の相手には、これまでどおり掘る（雪など）
      if (movements) movements.canDig = canDig && target.position.y > bot.entity.position.y - FIGHT_NO_DIG_BELOW
      // 反射の戦いだけに上限がある（maxMs）: 殴っても倒れない相手（壁の向こう、など）と永久に殴り合わない
      if (Date.now() - startedAt > maxMs) { why = `did not kill it in ${maxMs / 1000}s`; break }
      // 素手で殴り合う反射の戦い: 体力がここまで減ったらやめる（次の反射は、体力が足りなければ逃げる）
      if (bot.health < minHealth) { why = `health is down to ${round(bot.health)}/20: not trading blows with bare hands any longer`; break }
      if (d > lostAt) { why = `lost it (${round(d)}m away)`; break }
      // 近づけない敵に撃たれ続けない（ピリジャーに 18 秒で 18 ダメージを受けて死んだ: 2026-10-07。武器があると体力の下限が
      // なく、「近づけない」の判定は 6 秒動かないことを待つので、立ったまま撃たれ続けた）。殴り合えない距離で受けた
      // ダメージを数え、敵対のモブ（狩りの獲物ではない）の戦いだけやめる。続きは反射が、逃げる手で選ぶ
      if (typeof bot.health === 'number') {
        if (typeof lastHealth === 'number' && bot.health < lastHealth && d > FIGHT_MELEE_M) underFire += lastHealth - bot.health
        lastHealth = bot.health
        if (d > FIGHT_MELEE_M && (underFire >= FIGHT_UNDER_FIRE || bot.health <= FIGHT_OUT_OF_REACH_HEALTH) && isHostile(bot, target)) {
          why = underFire >= FIGHT_UNDER_FIRE
            ? `under fire: lost ${round(underFire)} health while ${target.name} was ${round(d)}m away, out of reach; not standing here to be shot`
            : `out of reach: health is down to ${round(bot.health)}/20 and ${target.name} is ${round(d)}m away`
          break
        }
      }
      if (d > 3 && stuck()) { why = `could not get to it (${round(d)}m away)`; break }
      // 洞窟へ逃げ込む相手を歩いて追い、持ち物を持ったまま地下へ降りた（2026-10-04: 畑から蜘蛛を追って y63 の洞窟へ）。
      // 離れた相手は、戦い始めの高さから FIGHT_MAX_DESCENT より下へは追わない（近寄ってくる相手とは戦う）
      if (d > 3 && bot.entity.position.y < startY - FIGHT_MAX_DESCENT) { why = `it went down; will not follow it ${round(startY - bot.entity.position.y)}m below where the fight began`; break }
      if (target.position.distanceTo(bot.entity.position) > 3) {
        if (bot.pathfinder.goal !== follow) bot.pathfinder.setGoal(follow, true)
      } else {
        // 追う途中で掘ると道具に持ち替わる: 殴る前に武器に戻す
        const weapon = bestWeapon(bot)
        if (weapon && bot.heldItem?.name !== weapon.name) await bot.equip(weapon, 'hand')
        await bot.lookAt(target.position.offset(0, target.height * 0.8, 0), true)
        bot.attack(target)
      }
      await bot.waitForTicks(12)
    }
  } finally {
    bot.pathfinder.setGoal(null)
    if (movements) movements.canDig = canDig
  }
  return bot.entities[target.id] ? `${target.name} still alive${why ? `: ${why}` : ''}` : `${target.name} gone (killed or despawned)`
}

// distance（離れたい距離）まで逃げる。STUCK_MS の間それ以上離れられなければ、FLEE_MS たっても離れられなければ、
// 逃げる間に FLEE_DAMAGE 以上ダメージを受ければやめる。逃げるときは掘らない（石を掘って潜りながら逃げ、
// 骷髏に 30 秒撃たれ続けて死んだ: 2026-10-01 19:44。掘る間は進まず、同じ直線上で当たり続ける）
export async function flee (bot, h, signal, distance = FLEE_DISTANCE) {
  const movements = bot.pathfinder.movements
  const canDig = movements?.canDig
  if (movements) movements.canDig = false
  bot.pathfinder.setGoal(new GoalAwayFrom(h, distance + REPLAN_MOVE), true)
  const stuck = stuckWatch(bot, () => -h.position.distanceTo(bot.entity.position))
  const startedAt = Date.now()
  const health = bot.health
  let why = ''
  try {
    while (!signal.aborted && bot.entities[h.id] && h.position.distanceTo(bot.entity.position) < distance && !stuck()) {
      if (health - bot.health >= FLEE_DAMAGE) { why = `took ${round(health - bot.health)} damage while fleeing`; break }
      if (Date.now() - startedAt > FLEE_MS) { why = `${FLEE_MS / 1000}s`; break }
      await bot.waitForTicks(5)
    }
  } finally {
    bot.pathfinder.setGoal(null)
    if (movements) movements.canDig = canDig
  }
  const left = round(h.position.distanceTo(bot.entity.position))
  // 離れられなかった: 逃げられたことにしない（反射が同じ相手から逃げ続ける繰り返しを数える）
  if (bot.entities[h.id] && left < distance && (why || stuck())) {
    throw new Error(`could not get away from ${h.name}: still ${left}m${why ? ` (${why})` : ` after ${STUCK_MS / 1000}s`}`)
  }
  return `now ${left}m from ${h.name}`
}

// 動けないかを見る: 呼ぶたびに、score（小さいほどよい: 相手までの距離など）が STUCK_MS の間よくならず、
// 位置もほとんど変わっていなければ真
function stuckWatch (bot, score) {
  let best = score()
  let pos = bot.entity.position.clone()
  let since = Date.now()
  return () => {
    const now = score()
    if (now < best - 0.5 || bot.entity.position.distanceTo(pos) > 1) {
      best = Math.min(best, now)
      pos = bot.entity.position.clone()
      since = Date.now()
      return false
    }
    return Date.now() - since > STUCK_MS
  }
}

// ボットが何かを拾ったか、ms たったら解決する
function waitForCollect (bot, ms) {
  return new Promise((resolve) => {
    const onCollect = (collector) => {
      if (collector !== bot.entity) return
      clearTimeout(timer)
      bot.off('playerCollect', onCollect)
      resolve(true)
    }
    const timer = setTimeout(() => { bot.off('playerCollect', onCollect); resolve(false) }, ms)
    bot.on('playerCollect', onCollect)
  })
}

// 拾える落とし物。捨てた物（spare.mjs）と、持ち物に入らない物（いっぱいで、まだ入るスタックもない）は含めない:
// 出すと拾いに行って「拾えなかった」と失敗する（2026-09-30: 土 447 で埋まったまま、拾えない肉に何度も向かった）
export function nearbyDrops (bot, state, radius) {
  return groundItems(bot, state, radius).filter(({ e }) => canHold(bot, e.getDroppedItem?.()?.name))
}

// 近くにあるが、持ち物に入らないもの（pickup を断る理由に使う）
export function blockedDrops (bot, state, radius) {
  return groundItems(bot, state, radius).filter(({ e }) => !canHold(bot, e.getDroppedItem?.()?.name))
}

function groundItems (bot, state, radius) {
  return nearbyEntities(bot).filter(({ e, dist }) => e.name === 'item' && dist <= radius &&
    Math.abs(e.position.y - bot.entity.position.y) < 4 && !state.unreachableDrops.has(e.id) && !isTossed(state, bot, e))
}

// ドロップは少し待たないと拾えない。すでにその上に立っていると、歩くのはすぐ終わる。
// 近くに落ちたドロップを集め、それぞれ拾うのを待つ。
// まわりのドロップを拾う。最後のドロップに届かなかったら、その理由を返す
// 落ちてくる途中のドロップ: 高い所の原木を切ると、ドロップは切った高さから落ちてくる。落とし物の位置は
// サーバーから 1 秒に 1 回しか届かない（アイテムの追跡間隔）ので、その間は切った高さで止まって見え、
// nearbyDrops の高さの範囲（±4）の外なら見えもしない。幹の 4 段目以上を切るたびに、ドロップが足元へ
// 落ちて拾える前に「拾えなかった」と失敗にしていた（2026-09-30 の drop-watch: 失敗は log_above_bot 4 以上に集中）
const FALL_WAIT_MS = 1500 // 位置が届く 1 秒 + 落ちる + 拾えるまでの 0.5 秒
const FALL_WAITS = 2
const FALLING_HORIZONTAL = 3
const FALLING_MIN_ABOVE = 2.5
const FALLING_MAX_ABOVE = 12
function fallingDrop (bot) {
  const me = bot.entity.position
  return nearbyEntities(bot).find(({ e }) => e.name === 'item' &&
    Math.hypot(e.position.x - me.x, e.position.z - me.z) <= FALLING_HORIZONTAL &&
    e.position.y - me.y >= FALLING_MIN_ABOVE && e.position.y - me.y <= FALLING_MAX_ABOVE) ?? null
}

export async function collectNearbyDrops (bot, state, signal) {
  let why = ''
  let waits = 0
  for (let i = 0; i < 3; i++) {
    await bot.waitForTicks(10)
    signal.throwIfAborted()
    // 頭より上にあるドロップは追わず、足元へ落ちてくるのを待つ（追っても着かない）
    if (fallingDrop(bot) && waits < FALL_WAITS) {
      waits++
      await waitForCollect(bot, FALL_WAIT_MS)
      signal.throwIfAborted()
      i--
      continue
    }
    const drop = nearbyDrops(bot, state, DROP_COLLECT_RADIUS)[0]
    if (!drop) return why
    const collected = waitForCollect(bot, PICKUP_WAIT_MS)
    why = await goNear(bot, drop.e.position, 0.5, signal).then(() => '', (e) => {
      if (signal.aborted) throw signal.reason
      return e.message
    })
    await collected
  }
  return why
}

// まわりのアイテムがボットから見てどこにあるか（拾えなかったドロップの説明になる）
function dropsAround (bot) {
  const me = bot.entity.position
  const items = nearbyEntities(bot).filter(({ e, dist }) => e.name === 'item' && dist <= DROP_COLLECT_RADIUS)
  if (!items.length) return 'no item on the ground within 8m'
  return items.slice(0, 3).map(({ e, dist }) => `item ${round(dist)}m away, ${round(e.position.y - me.y)} up`).join(', ')
}

const itemCount = (bot, name) => bot.inventory.items().filter((i) => i.name === name).reduce((n, i) => n + i.count, 0)
const totalItems = (bot) => bot.inventory.items().reduce((n, i) => n + i.count, 0)

// 拾った数は、増えた物の増え分だけを数える（物ごとの増減の、増えた側の合計）。総数の純増で見ると、歩く間に
// 使った物（足場に置いた土、trail が置いた松明）で差し引かれ、拾えたのに「拾えなかった」と失敗していた
// （2026-10-04 02:40: 松明 2 本を置いたあとに鉄鉱石を掘り、鉄 1 + 丸石 1 - 松明 2 = 0 で失敗。原木の「3〜5 本目で
// 拾えない」も、幹に登る足場の土）
export function gainedSince (bot, baseline) {
  const now = inventoryCounts(bot)
  let gained = 0
  for (const [name, n] of Object.entries(now)) gained += Math.max(0, n - (baseline[name] ?? 0))
  return gained
}

// 使える作業台。避難中（夜に家の中）は家の中のものだけ（外のものへは行けない）
export function findTable (bot, state = null) {
  const matching = bot.registry.blocksByName.crafting_table.id
  if (state?.home && shelterOf(bot, state.home).sheltering) {
    return bot.findBlocks({ matching, maxDistance: TABLE_SEARCH_RADIUS, count: 16 })
      .map((p) => bot.blockAt(p))
      .find((b) => b && isInside({ entity: { position: b.position } }, state.home)) ?? null
  }
  return bot.findBlock({ matching, maxDistance: TABLE_SEARCH_RADIUS })
}

// 使えるかまど。精錬・調理は家の中のかまどだけ: 地下の洞窟でかまどを置いて 50 秒立ち止まり、骸骨に撃たれ続けて
// 死んだ（2026-10-04、docs/design/86_safe_work_and_branch_mining.md）。家がまだないときだけ、近くのかまど（地上のもの。
// 地下では使わない）
export function findFurnace (bot, state = null) {
  const matching = bot.registry.blocksByName.furnace.id
  if (!state?.home) return isUnderground(bot) ? null : bot.findBlock({ matching, maxDistance: TABLE_SEARCH_RADIUS })
  return bot.findBlocks({ matching, maxDistance: TABLE_SEARCH_RADIUS, count: 16 })
    .map((p) => bot.blockAt(p))
    .find((b) => b && inHomeRoom(state.home, b.position)) ?? null
}

// 焼けるかまど: 近くにあればそれ、なければ家のかまど（遠ければ歩いて焼く。家がまだないときは最後に見たもの）。
// { position, near } か null（state を渡したときだけ遠くのを探す）
export function knownFurnace (bot, state = null) {
  const near = findFurnace(bot, state)
  if (near) {
    if (state && !state.home) state.lastFurnace = { x: near.position.x, y: near.position.y, z: near.position.z }
    return { position: near.position, near: true }
  }
  const p = state?.home
    ? furnitureInHome(bot, state.home, 'furnace', (q) => bot.blockAt(q)?.name === 'furnace')
    : (!isUnderground(bot) ? state?.lastFurnace : null)
  return p ? { position: new Vec3(p.x, p.y, p.z), near: false } : null
}

// かまど類は、置くのも使うのも家の中だけ（家がまだないときは地下を除く）。作業台は地下で置かない
// （建築予定地への仮設など、地上の昼の作業はこれまでどおり）。置く場所を選ぶ側と、道具 place が使う。
// 置けないときは理由
export const FURNACE_ITEM = /^(furnace|blast_furnace|smoker)$/
export function stationRefusal (bot, state, item, pos) {
  if (FURNACE_ITEM.test(item) && state.home) {
    return inHomeRoom(state.home, pos)
      ? ''
      : `a ${item} is placed only inside the house (smelting and cooking are done where it is safe, not in the field or underground): go home and place it there`
  }
  // かまどは地下に置かない（立ち止まって焼く間に撃たれる）。作業台は置くかどうかを LLM が決める: 作業台がなければツルハシを
  // 作れず、閉じ込められたまま出られなかった（2026-10-07: 約 55 分）。置く場所を選ぶ側（候補）は今までどおり地下に出さない
  if (FURNACE_ITEM.test(item) && isUnderground(bot)) {
    return `a ${item} is not placed underground (monsters spawn in the dark and you stand still while using it): do it on the surface or in the house`
  }
  return ''
}

// 経路が届かなかったブロックは、このセッションでは再び出さない（town2: 何度も選ばれた）
const blockKey = (p) => `${p.x},${p.y},${p.z}`
const markUnreachable = (state, pos) => state.unreachableBlocks?.add(blockKey(pos))
export const isUnreachable = (state, pos) => !!state.unreachableBlocks?.has(blockKey(pos))

// そのブロックに一番速い道具を持っていれば装備する
// 掘るのに使う道具: 採るのに要る道具（石にツルハシ）か、素手より速く掘れる道具のうち一番速いもの。
// 同じ速さなら安い方（木 < 石 < 金 < 鉄 < ダイヤ < ネザライト）。壊れかけの道具は要るときだけ。
// どれも素手より速くなければ null（素手）。pathfinder の bestHarvestTool は、速い道具がないと
// 持ち物の最初のもの（ツルハシや土）を返していて、原木をツルハシで切っていた
const TIERS = ['wooden', 'stone', 'golden', 'iron', 'diamond', 'netherite']
const tierOf = (name) => { const i = TIERS.indexOf(name.split('_')[0]); return i < 0 ? TIERS.length : i }
const ALMOST_BROKEN = 0.05
function almostBroken (item) {
  const max = item.maxDurability
  return !!max && (item.durabilityUsed ?? 0) >= max * (1 - ALMOST_BROKEN)
}
export function chooseTool (bot, block) {
  if (typeof block?.digTime !== 'function' || !bot.inventory?.items) return null
  const effects = bot.entity?.effects ?? {}
  const hand = block.digTime(null, false, false, false, [], effects)
  const needs = block.harvestTools ? Object.keys(block.harvestTools).map(Number) : null
  let best = null
  for (const item of bot.inventory.items()) {
    const required = !!needs && needs.includes(item.type)
    if (needs && !required) continue
    const t = block.digTime(item.type, false, false, false, item.enchants ?? [], effects)
    if (!required && t >= hand) continue
    if (!required && almostBroken(item)) continue
    if (!best || t < best.t || (t === best.t && tierOf(item.name) < tierOf(best.item.name))) best = { item, t }
  }
  return best?.item ?? null
}

// 採るのに道具が要るブロック（石にツルハシ）を素手で掘っても何も落ちない: 掘らずに理由を返す
// （ツルハシが壊れたあとも、素手で石を掘り続けていた）
export function checkHarvestable (bot, state, block, tool = chooseTool(bot, block)) {
  if (tool || !block?.harvestTools || !Object.keys(block.harvestTools).length) return
  // 閉じ込められたら素手でも掘る（出る道を空けるため。何も落ちなくてよい）。地下の判定（岩の厚み 8 以上）は要らない:
  // 家の床より 6 下の縦穴（屋根が岩 3 + 土 2）は地下と数えられず、素手で屋根を掘れないまま約 40 分閉じ込められた（2026-10-07）。家の中は除く
  if (closedSpace(bot) && !(state?.home && isInside(bot, state.home))) return
  const last = (state?.brokenTools ?? []).at(-1)
  throw new Error(`no tool that can mine ${block.name}${last ? ` (the ${last.item} broke)` : ''}: make one first`)
}

// drops: false は、壊すだけでよいとき（自分で積んだ柱を崩す。何も落ちなくてよい）: 掘れる道具がなくても素手で壊す
async function equipToolFor (bot, state, block, { drops = true } = {}) {
  const tool = chooseTool(bot, block)
  if (drops) checkHarvestable(bot, state, block, tool)
  if (tool) {
    if (bot.heldItem?.name !== tool.name) await bot.equip(tool, 'hand')
  } else if (TOOL_ITEM.test(bot.heldItem?.name ?? '')) {
    // 素手のほうがよい（道具を減らさない）
    await bot.unequip?.('hand')?.catch(() => {})
  }
}
const TOOL_ITEM = /_(pickaxe|axe|shovel|hoe|sword)$|^shears$/

// 閉じた所の中、または壁の外のセルへ動く: 水の中は泳ぎ（経路探索器は水の中から動かない）、乾いていれば歩く
async function stepTo (bot, cell, signal) {
  if (isSwimming(bot)) {
    await swimToward(bot, { x: cell.x + 0.5, y: cell.y, z: cell.z + 0.5 }, signal, { maxMs: 20000 })
    return
  }
  await goto(bot, new goals.GoalBlock(cell.x, cell.y, cell.z), signal)
}

// 階段掘り（docs/design/17_dig_down.md）: 1 マス幅・2 マスの高さで、前に 1、下に 1 ずつ下りる。
// 真下には掘らない（溶岩に落ちる、出られない穴になる）。階段なら歩いて戻れる
const FALLING = /(^|_)(sand|gravel)$|concrete_powder$/
const FLUID = /water|lava/
const STAIR_DIRECTIONS = [[1, 0], [-1, 0], [0, 1], [0, -1]]

// 次の段で掘る 3 マス（頭、足、1 段下の足）と、その下の床
function stairCells (me, [dx, dz]) {
  return { cells: [me.offset(dx, 1, dz), me.offset(dx, 0, dz), me.offset(dx, -1, dz)], floor: me.offset(dx, -2, dz) }
}

// その方向に 1 段下りられない理由（なし: ''）
function stairDanger (bot, state, { cells, floor }) {
  for (const p of cells) {
    const b = bot.blockAt(p)
    if (!b) return 'the ground ahead is not loaded'
    if (inHouse(state, p)) return 'the house is in the way'
    if (b.name === 'bedrock') return 'bedrock'
    for (const [x, y, z] of [[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1], [0, 0, 0]]) {
      const n = bot.blockAt(p.offset(x, y, z))
      if (n && FLUID.test(n.name)) return `${n.name.includes('lava') ? 'lava' : 'water'} next to the stairs`
    }
  }
  if (FALLING.test(bot.blockAt(cells[0].offset(0, 1, 0))?.name ?? '')) return 'sand or gravel above would fall in'
  const below = bot.blockAt(floor)
  if (!below || below.boundingBox !== 'block') return 'a cave or a drop below the next step'
  return ''
}

// 整地: 同じ列の掘るセルのいちばん上、埋めるセルのいちばん下
const topOf = (cells, p) => cells.filter((q) => q.x === p.x && q.z === p.z).reduce((a, b) => (b.y > a.y ? b : a))
const bottomOf = (cells, p) => cells.filter((q) => q.x === p.x && q.z === p.z).reduce((a, b) => (b.y < a.y ? b : a))

// 掘ったあとの拾い集めと結果の文（PRIMITIVES.dig から）
async function afterDig (bot, state, c, before, signal) {
  // 確率でしか落ちない物（草から種 1/8、葉から苗木 1/20）: 1 回の行動でまわりの同じものを続けて
  // 刈る（1 本ずつだと、ほとんどの行動が空振りで種集めが行き詰まった）
  if (CHANCE_DROP_BLOCKS.test(c.block)) await sweepNearby(bot, state, c.block, before, signal)
  // 熟した作物: まわりの熟した同じ作物も続けて収穫し、拾ってから植え直す（畑を減らさない）
  const harvested = cropByBlock(c.block) ? [c.pos, ...await harvestNearby(bot, state, c.block, signal)] : []
  const why = await collectNearbyDrops(bot, state, signal)
  if (harvested.length) {
    const replanted = await replant(bot, harvested, c.block)
    const gained = gainedSince(bot, before)
    return `harvested ${harvested.length} ${c.block}, replanted ${replanted}; picked up ${gained} items` +
      (replanted < harvested.length ? ` (no ${CROPS[cropByBlock(c.block)].seed} left to replant ${harvested.length - replanted})` : '')
  }
  const gained = gainedSince(bot, before)
  // 葉や草は、苗木や種を確率でしか落とさない: 何も落ちなくても失敗ではない（設計書 33）
  if (gained <= 0 && CHANCE_DROP_BLOCKS.test(c.block)) return `dug ${c.block}; nothing dropped this time (it drops only sometimes)`
  if (gained <= 0) {
    const full = bot.inventory.emptySlotCount() === 0 ? '; the inventory is full' : ''
    throw new Error(`dug ${c.block} but picked nothing up (${dropsAround(bot)}${why ? `; path: ${why}` : ''}${full})`)
  }
  return `dug ${c.block}, picked up ${gained} items`
}

// 実行関数: (bot, state, candidate, signal) -> 結果の文字列。対象は候補が持っている
const MINE_SITE_WALK = 32 // 採掘場を設計するとき、家からこの内にいないと近づく（外周の輪は家から 40 m まである）
// 松明を置く間隔（掘ったセルの数）と、枝で置く番（枝の何マス目か）。松明は置いた所が 14 で、1 マスごとに 1 下がる。
// 坑道の松明の間は最大 3 マス離れ、枝の先まで 10 マス行くと 13 先（光 1）になるので、枝の途中にも 1 本置く
// 坑道の松明は、枝の根本（坑道の 3 マス目から 3 マスおき）を避けた 4 マス目から（根本のセルは両側の壁が枝で空くので、置けない）
const MINE_TORCH = { stairs: 6, tunnel: 6, tunnelFrom: 3, branch: 5 }
const LIGHT_WINDOW = 6 // 掘ったあと、この内（直線の距離）にある、掘り終えたセルの明るさを見る
const LIGHT_SETTLE_MS = 1500 // 松明を置いてから、光が届くまで待つ（この間は暗いと数えない）
const RELIGHT_MAX = 10 // 1 回の見直しで足す松明の上限
const ORE_CHECK_EVERY = 3 // 探している鉱石が壁に出たかを、これだけ掘るごとに見る

// 探している鉱石で、今掘れるもの（空気に面して、届き、さっき断られていない）: { name, pos } か null。solver の見え方
// （world.dig = digTargets）と同じ判定なので、これが出れば次の手は掘る
function oreShowing (bot, state, sources) {
  for (const name of sources) {
    const pos = digTargets(bot, state, name, 4).find((p) => !refusalAt(state, 'dig', p))
    if (pos) return { name, pos }
  }
  return null
}
const cutNote = (cut) => cut.length ? `; branches cut short for danger: ${cut.join('; ')}` : ''
const sealNote = (n) => n ? `; walled off ${n} cave openings with cobblestone (floor, walls and ceiling)` : ''

// セルの壁に松明を 1 本（置けなければ false）。置いたあとは、持っていた物（ツルハシ）に戻す:
// 松明を持ったまま掘ると素手と同じ遅さになる（2026-10-07）
async function wallTorch (bot, cells, cell, torch) {
  const head = new Vec3(cell.x, cell.y + 1, cell.z)
  if (bot.blockAt(head)?.boundingBox !== 'empty') return false
  const planned = plannedBlocks(cells)
  for (const [dx, dz] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
    const wall = bot.blockAt(head.offset(dx, 0, dz))
    if (wall?.boundingBox !== 'block' || planned.has(`${wall.position.x},${wall.position.y},${wall.position.z}`)) continue
    const before = bot.heldItem
    try {
      await bot.equip(torch, 'hand')
      await bot.placeBlock(wall, new Vec3(-dx, 0, -dz))
      return true
    } catch (e) {
      console.log(`[mine] 松明を置けなかった: ${e.message}`)
    } finally {
      if (before && before.name !== 'torch') await bot.equip(before, 'hand').catch(() => {})
    }
  }
  return false
}
const torchOf = (bot) => bot.inventory.items().find((i) => i.name === 'torch')

// 掘り終えたセルの壁に、間隔をおいて松明を 1 本（置けなくても掘りは続ける）。置く番なのに持っていなければ
// 'missing' を返す（暗いまま掘り進めない: 呼ぶ側が止めて、足りないと知らせる）。置いたら 'placed'
async function torchInMine (bot, cells, index) {
  const cell = cells[index]
  const k = cells.slice(0, index).filter((x) => x.part === cell.part).length
  const due = cell.part === 'stairs' ? k % MINE_TORCH.stairs === MINE_TORCH.stairs - 1
    : cell.part === 'room' ? k === 0
      : cell.part.startsWith('branch') ? k === MINE_TORCH.branch - 1
        : k % MINE_TORCH.tunnel === MINE_TORCH.tunnelFrom
  if (!due) return null
  const torch = torchOf(bot)
  if (!torch) return 'missing'
  return (await wallTorch(bot, cells, cell, torch)) ? 'placed' : null
}

// 掘り終えて空いている（足元と頭の上）セルのうち、光が MIN_LIGHT 未満と分かっているもの（光が届いていないセルは数えない）。
// 空の光が届く所（入口の近く）は、地下の暗がりとは別なので数えない
const openCell = (bot, c) => mineBlocks(c).slice(0, 2).every((p) => bot.blockAt(p)?.boundingBox === 'empty')
const darkCells = (bot, cells) => cells.filter((c) => {
  const at = new Vec3(c.x, c.y, c.z)
  return openCell(bot, c) && tooDarkAt(bot, at) && !(skyLightAt(bot, at) > 0)
})

// 暗いセルを、近いものから松明で明るくする。分かる光が 0 のセルが残らないまで（上限 RELIGHT_MAX 本）。
// 持っていなければ 'missing'
async function relightMine (bot, cells, signal) {
  const failed = new Set()
  for (let n = 0; n < RELIGHT_MAX; n++) {
    signal.throwIfAborted()
    const here = bot.entity.position
    const dark = darkCells(bot, cells).filter((c) => !failed.has(c))
      .sort((a, b) => here.distanceTo(new Vec3(a.x, a.y, a.z)) - here.distanceTo(new Vec3(b.x, b.y, b.z)))
    if (!dark.length) return null
    const torch = torchOf(bot)
    if (!torch) return 'missing'
    const cell = dark[0]
    await goto(bot, new goals.GoalBlock(cell.x, cell.y, cell.z), signal)
    if (!(await wallTorch(bot, cells, cell, torch))) { failed.add(cell); continue }
    await bot.waitForTicks(Math.ceil(LIGHT_SETTLE_MS / 50))
  }
  return null
}

// かまどを開け、できたものを取り、材料と燃料を入れ、全部焼けるまで待つ。燃料が尽きて焼けないまま入力が残った
// ら取り戻す。残りは後のステップで取り出す（その間もかまどは動く）
export async function tendFurnace (bot, state, c, block, signal) {
  const furnace = await bot.openFurnace(block)
  const got = {}
  let returned = ''
  const take = async () => {
    const out = furnace.outputItem()
    if (!out) return
    await furnace.takeOutput()
    got[out.name] = (got[out.name] ?? 0) + out.count
  }
  try {
    await take()
    if (c.input) {
      const inSlot = furnace.inputItem()?.name === c.input ? furnace.inputItem().count : 0
      const n = Math.min(c.count - inSlot, inventoryCounts(bot)[c.input] ?? 0)
      if (c.fuel && (furnace.fuelItem()?.count ?? 0) < c.fuelCount) {
        await furnace.putFuel(bot.registry.itemsByName[c.fuel].id, null, Math.min(c.fuelCount, inventoryCounts(bot)[c.fuel] ?? 0))
      }
      if (n > 0) await furnace.putInput(bot.registry.itemsByName[c.input].id, null, n)
    }
    // 全部焼けるまで待つ（1 個 10 秒）。燃料が切れて火が消えたらやめる。中断（反射・ダメージ）は 1 秒ごとに見る:
    // 見ないと 51 秒の精錬が終わるまで反射が待たされ、その間に骸骨に撃たれ続けて死んだ（2026-10-04）
    while (furnace.inputItem() && (furnace.fuelItem() || furnace.fuel > 0)) {
      signal.throwIfAborted()
      await bot.waitForTicks(20)
      await take()
    }
    await take()
    // 燃料が尽きて、焼けないまま入力が残った: 取り戻す（かまどに入れっぱなしだと、記録が「焼けているはず」の
    // ままで、取りに行っても何も出ない手を成功と数えて空回りした: 2026-10-03、昆布 3 個）
    const stuck = furnace.inputItem()
    if (stuck && !furnace.fuelItem() && !(furnace.fuel > 0)) {
      await furnace.takeInput()
      returned = `${stuck.count} ${stuck.name}`
    }
  } finally {
    const making = {}
    if (furnace.outputItem()) making[furnace.outputItem().name] = furnace.outputItem().count
    const input = furnace.inputItem()
    const product = input && smeltingProduct(input.name)
    if (product) making[product] = (making[product] ?? 0) + input.count
    rememberFurnace(state.memory, c.pos, making, Number(bot.time.age))
    furnace.close()
  }
  const took = Object.entries(got).map(([name, n]) => `${n} ${name}`).join(', ')
  const left = furnace.inputItem()?.count ?? 0
  const back = returned ? `${took ? '; ' : ''}the furnace ran out of fuel: took back ${returned} (add fuel and smelt again)` : ''
  const short = c.short ? `; the fuel you carry burns only ${c.count} of them: ${c.short} not put in, add fuel for the rest` : ''
  return `${took ? `took ${took}` : returned ? '' : 'nothing done yet'}${back}${left ? `; ${left} still smelting` : ''}${short}`
}

export const PRIMITIVES = {
  async dig (bot, state, c, signal) {
    const block = bot.blockAt(c.pos)
    if (!block || block.name !== c.block) throw new Error(`${c.block} is gone from ${c.pos}`)
    checkHarvestable(bot, state, block) // 歩いて行く前に
    // 水や溶岩をせき止めているブロックは掘らない（候補にも道具にも出さないが、掘る直前にも確かめる）
    const wet = liquidReason(bot, c.pos, block)
    if (wet) throw new Error(`will not dig ${block.name} at ${c.pos.x},${c.pos.y},${c.pos.z}: ${wet}`)
    // 高い原木には、土を積んで届く。持っていなければ、近くの土を先に掘って集める（取り残しの幹を全部取れるように）
    if (isLog(c.block)) await gatherScaffold(bot, state, c.pos, signal)
    // 近くではなく、ブロックに届く場所に立つ: そうしないと、頭より高い原木には木の葉の上からしか
    // 「近く」にならない。
    // 当たり判定のない草・花・作物は、視線の先に見える位置を探す GoalLookAtBlock が決して成り立たない
    // （レイが通り抜ける）: 届く距離まで近づくだけにする（種を集める小目標が毎回失敗した）
    const goal = block.boundingBox === 'block'
      ? new goals.GoalLookAtBlock(c.pos, bot.world, { reach: REACH })
      : new goals.GoalNear(c.pos.x, c.pos.y, c.pos.z, 2)
    try {
      // 草などは、もう手が届くならその場で刈る（段差の上の草へ回り込む経路探しが時間切れになった）
      if (block.boundingBox === 'block' || !withinReach(bot, c.pos)) await goto(bot, goal, signal)
    } catch (e) {
      if (!signal.aborted) markUnreachable(state, c.pos)
      throw e
    }
    signal.throwIfAborted()
    // 空中で掘ると5倍遅い（例えば、立っていたブロックを切った直後）
    for (let i = 0; i < 40 && !bot.entity.onGround; i++) await bot.waitForTicks(1)
    await equipToolFor(bot, state, block)
    const before = inventoryCounts(bot) // 掘る直前から数える: 歩く間に拾った物は、この物のドロップではない
    await bot.dig(bot.blockAt(c.pos), true)
    noteDig(state, c.pos, c.block, bot.entity.position) // 地形を掘った場所を採掘の場所にする（quarry.mjs）
    signal.throwIfAborted()
    // 原木は、ドロップがどこに落ちて拾えたかを記録する（dropwatch.mjs）
    const watch = isLog(c.block) ? watchDrop(bot, { block: c.block, pos: c.pos }) : null
    try {
      const result = await afterDig(bot, state, c, before, signal)
      watch?.finish(`ok: ${result}`)
      return result
    } catch (e) {
      watch?.finish(`failed: ${e.message}`)
      throw e
    }
  },
  async pickup (bot, state, c, signal) {
    const drop = bot.entities[c.entityId]
    if (!drop) throw new Error('the item is gone')
    const before = inventoryCounts(bot)
    const collected = waitForCollect(bot, PICKUP_WAIT_MS)
    try {
      await goNear(bot, drop.position, 0.5, signal)
    } catch (e) {
      state.unreachableDrops.add(c.entityId)
      throw e
    }
    await collected
    const gained = gainedSince(bot, before)
    // 葉や草は、苗木や種を確率でしか落とさない: 何も落ちなくても失敗ではない（設計書 33）
    if (gained <= 0 && CHANCE_DROP_BLOCKS.test(c.block)) return `dug ${c.block}; nothing dropped this time (it drops only sometimes)`
    if (gained <= 0) {
      state.unreachableDrops.add(c.entityId)
      const full = bot.inventory.emptySlotCount() === 0 ? '; the inventory is full' : ''
      throw new Error(`reached the item but picked nothing up (${dropsAround(bot)}${full})`)
    }
    return `picked up ${gained} items`
  },
  async attack (bot, state, c, signal) {
    const target = bot.entities[c.entityId]
    if (!target) throw new Error(`${c.target} is gone`)
    const weapon = bestWeapon(bot)
    if (weapon) await bot.equip(weapon, 'hand')
    const before = totalItems(bot)
    const result = await fight(bot, target, signal)
    if (bot.entities[c.entityId]) throw new Error(result)
    if (!c.hostile) await collectNearbyDrops(bot, state, signal)
    const gained = totalItems(bot) - before
    return gained > 0 ? `${result}; picked up ${gained} items` : result
  },
  // ベッドで寝ているモブ（村人）を 1 回だけ素手で殴って起こす（docs/design/56）。倒すまで戦う attack と違い、
  // 起きてベッドが空けばよい。殴った後は追わない
  async wake_sleeper (bot, state, c, signal) {
    const target = bot.entities[c.entityId]
    if (!target) throw new Error(`${c.target} is gone`)
    if (!isSleeping(bot, target)) return `the ${c.target} is already awake`
    await goNear(bot, target.position, 2, signal)
    signal.throwIfAborted()
    await bot.unequip('hand') // 素手（武器で殴ると、起きる前に倒してしまう）
    await bot.lookAt(target.position.offset(0, target.height * 0.5, 0), true)
    bot.attack(target)
    for (let i = 0; i < 10 && bot.entities[c.entityId] && isSleeping(bot, target); i++) await bot.waitForTicks(4)
    if (!bot.entities[c.entityId]) return `the ${c.target} is gone after one hit`
    if (isSleeping(bot, target)) throw new Error(`hit the ${c.target} once but it is still asleep`)
    return `woke the ${c.target} #${c.entityId} up with one hit: the bed is free`
  },
  // 家も寝床もない夜: 足元に 3 マスの縦穴を掘って入り、地面と同じ高さの上のマスを塞ぐ（docs/design/61）。
  // 掘って出た土がそのまま塞ぐブロックになる。塞いだら朝まで穴の中で待つ
  async dig_in (bot, state, c, signal) {
    if (inBurrow(bot)) return 'already in a sealed hole'
    const { spot, why } = burrowSpot(bot, state)
    if (!spot) throw new Error(`no place to dig in within reach: ${why}`)
    const { feet, cells, seal } = spot
    // 掘りかけの穴の中（前に塞ぐのに失敗した）なら、掘らずに塞ぐだけ
    if (!spot.open && !standingCell(bot).equals(feet)) await goto(bot, new goals.GoalBlock(feet.x, feet.y, feet.z), signal)
    for (const cell of cells) {
      signal.throwIfAborted()
      const block = bot.blockAt(cell)
      if (block.boundingBox === 'block') {
        await equipToolFor(bot, state, block)
        await bot.dig(block, true)
      }
      // 足元が抜けたら、落ちて穴の底へ（次のマスを掘るのはそこから）
      for (let i = 0; i < 20 && bot.entity.position.y > cell.y + 0.1; i++) await bot.waitForTicks(1)
    }
    await bot.waitForTicks(6) // 掘った土を拾う
    const support = sealSupport(bot, seal)
    const item = fillItem(bot)
    if (!support || !item) {
      throw new Error(`dug the hole but could not seal it (${!item ? 'no dirt or cobblestone to seal with' : 'nothing beside the top to place against'})`)
    }
    await bot.equip(item, 'hand')
    await bot.lookAt(seal.offset(0.5, 0.5, 0.5), true)
    await bot.placeBlock(support.ref, support.face)
    await bot.waitForTicks(4)
    if (bot.blockAt(seal)?.boundingBox !== 'block') throw new Error(`dug the hole but the top at ${seal.x},${seal.y},${seal.z} is still open`)
    return `dug in: a ${BURROW_DEPTH}-block hole at ${seal.x},${seal.z}, sealed on top with ${item.name}; wait here until morning`
  },
  // 水から陸へ泳いで上がる（docs/design/89）。経路探索器は水の中から動かないので、泳ぎ（swim.mjs）で渡る
  async reach_land (bot, state, c, signal) {
    return await reachLand(bot, signal)
  },
  // 家も寝床も掘れる地面もない夜: 立った所の四方（足・頭・天井の高さ）と真上を土などで囲み、朝まで待つ（docs/design/89）。
  // 持っていなければ、近くの土を掘って集める（素手でよい）。囲めば inBurrow が真になる
  async wall_in (bot, state, c, signal) {
    if (inBurrow(bot)) return 'already enclosed'
    if (isSwimming(bot)) throw new Error('you are in the water: swim to land first (reach_land)')
    const { site, why } = campSite(bot, state)
    if (!site) throw new Error(`no place to make a camp within reach: ${why}`)
    let dug = 0
    // 材料: 足りなければ、囲む場所のまわりの土を掘る（囲みと足元は掘らない）
    while (campCount(bot) < site.need) {
      signal.throwIfAborted()
      const [target] = campSources(bot, state, site)
      if (!target) throw new Error(`the camp needs ${site.need} blocks and you carry ${campCount(bot)}: no dirt within reach to dig for the rest`)
      if (++dug > site.need + 6) throw new Error(`dug ${dug - 1} blocks of dirt and still carry only ${campCount(bot)} of the ${site.need} needed`)
      const block = bot.blockAt(target)
      await PRIMITIVES.dig(bot, state, { block: block.name, pos: target }, signal)
    }
    // 立つ場所へ戻る（掘りに歩いたとき）。戻ったあとで場所をもう一度確かめる
    if (!standingCell(bot).equals(site.feet)) await goto(bot, new goals.GoalBlock(site.feet.x, site.feet.y, site.feet.z), signal)
    const again = campAt(bot, state, site.feet)
    if (typeof again === 'string') throw new Error(`the camp spot is no longer usable: ${again}`)
    const placed = await buildCamp(bot, again, signal)
    if (!inBurrow(bot)) throw new Error(`placed ${placed} blocks but still not enclosed (a wall or the roof is missing)`)
    return `enclosed myself at ${site.feet.x},${site.feet.y},${site.feet.z} with ${placed} blocks: wait here until morning`
  },
  // 穴から出る: 上の塞ぎを壊し、足元に土を置いて地面の高さまで上がる
  async leave_burrow (bot, state, c, signal) {
    if (!inBurrow(bot) && !inOpenShaft(bot)) return 'not in a hole'
    // 囲い（wall_in）は、壁の 1 方を掘って歩いて出る（縦穴のように上へ積まない）
    const exit = campExit(bot)
    if (exit) {
      for (const cell of exit.cells) {
        signal.throwIfAborted()
        const block = bot.blockAt(cell)
        if (block?.boundingBox !== 'block') continue
        await equipToolFor(bot, state, block)
        await bot.dig(block, true)
      }
      await goto(bot, new goals.GoalBlock(exit.outside.x, exit.outside.y, exit.outside.z), signal)
      await collectNearbyDrops(bot, state, signal)
      return `dug out through the ${exit.dir[0] ? (exit.dir[0] > 0 ? 'east' : 'west') : (exit.dir[1] > 0 ? 'south' : 'north')} wall to ${exit.outside.x},${exit.outside.y},${exit.outside.z}`
    }
    const start = standingCell(bot)
    let roof = start.offset(0, 2, 0)
    let up = 0
    while (bot.blockAt(roof)?.boundingBox === 'block' && up < BURROW_DEPTH) { // 塞ぎの上に土が続いていても掘り抜ける
      signal.throwIfAborted()
      await equipToolFor(bot, state, bot.blockAt(roof))
      await bot.dig(bot.blockAt(roof), true)
      roof = roof.offset(0, 1, 0)
      up++
    }
    // 頭の上が開いたら、足場を置いて上がる。横に出られる高さ（隣が空いていて、その下に地面がある）まで
    for (let i = 0; i < BURROW_DEPTH; i++) {
      signal.throwIfAborted()
      const cell = standingCell(bot)
      if (canStepOut(bot, cell)) break
      const item = fillItem(bot)
      if (!item) throw new Error('opened the top but carry no dirt or cobblestone to climb out with')
      await jumpAndPlace(bot, cell, item)
      await bot.waitForTicks(2)
    }
    if (!canStepOut(bot, standingCell(bot))) throw new Error('climbed up but still cannot step out of the hole')
    await collectNearbyDrops(bot, state, signal)
    return `climbed out of the hole to ${standingCell(bot).x},${standingCell(bot).y},${standingCell(bot).z}`
  },
  // 閉じた所から出る: 足元に土か丸石を積んで、歩いて出られるようになる高さまで上がる（階段の床が抜けて 1 段ずつ
  // 上がれないとき、深い穴に落ちたとき）。頭の上が岩なら掘る（道具がなければ素手で: 閉じ込められたときだけ許す）
  async climb_out (bot, state, c, signal) {
    if (!closedSpace(bot)) return 'not shut in: you can walk out from here'
    let raised = 0
    try {
      while (closedSpace(bot)) {
        signal.throwIfAborted()
        if (raised >= CLIMB_MAX) throw new Error(`piled up ${raised} blocks and still cannot walk out`)
        const cell = standingCell(bot)
        const item = fillItem(bot)
        if (!item) throw new Error(`piled up ${raised} blocks and ran out of dirt and cobblestone`)
        // 水が入った小部屋では、水はもう浴びている（溶岩だけは止める）。乾いた所では、水も流れ込むので止める
        const hazard = bot.entity.isInWater ? /lava/ : FLUID
        const roof = bot.blockAt(cell.offset(0, 2, 0)) // 1 つ上がった足の高さの頭
        if (!roof) throw new Error('the space above is not loaded')
        if (roof.boundingBox === 'block') {
          // 掘る前に、上と横に水・溶岩・落ちる砂がないか（1 マスの縦穴では、流れ込むと逃げ場がない）
          const bad = [[0, 0, 0], [0, 1, 0], [1, 0, 0], [-1, 0, 0], [0, 0, 1], [0, 0, -1]]
            .map(([x, y, z]) => bot.blockAt(roof.position.offset(x, y, z)))
            .find((b) => b && (hazard.test(b.name) || (FALLING.test(b.name) && b.position.y > roof.position.y)))
          if (bad) throw new Error(`${bad.name} at ${bad.position.x},${bad.position.y},${bad.position.z} beside the rock above: digging it would let it in on you`)
          await equipToolFor(bot, state, roof)
          await bot.dig(roof, true)
        } else if (hazard.test(roof.name)) {
          throw new Error(`${roof.name} above at ${roof.position.x},${roof.position.y},${roof.position.z}: not piling up into it`)
        }
        await jumpAndPlace(bot, cell, item)
        raised++
        await bot.waitForTicks(4)
      }
    } catch (e) {
      // 続けて出し直すと、同じ失敗で土を使い切る: しばらくは聞かずに実行しない。反射や中止で止められたのは失敗ではない
      if (!signal.aborted) state.climbBlockedUntil = Date.now() + CLIMB_BLOCK_MS
      throw e
    }
    if (isolatedSpace(bot)) {
      state.climbBlockedUntil = Date.now() + CLIMB_BLOCK_MS
      throw new Error(`piled up ${raised} block${raised === 1 ? '' : 's'} and now stand over a drop on every side, not in a closed space: climb down the pillar instead`)
    }
    const at = standingCell(bot)
    return `piled up ${raised} block${raised === 1 ? '' : 's'} and can walk out now (at ${at.x},${at.y},${at.z})`
  },
  // 閉じた所から、壁を掘って歩いて出る（docs/design/90 §5.1）。土や丸石がなくても、素手でも掘る。壁は
  // 掘る数がいちばん少ない向きを選び、掘れない壁（保護、溶岩に接する、大きな水を止める）は避ける
  async dig_out (bot, state, c, signal) {
    if (!closedSpace(bot)) return 'not shut in: you can walk out from here'
    let dug = 0
    try {
      for (let round = 0; round < DIG_OUT_ROUNDS; round++) {
        signal.throwIfAborted()
        const route = digOutRoute(bot, state)
        if (route.why) throw new Error(`no way to dig out: ${route.why}`)
        // 掘る壁に届く所まで（小部屋の中で）寄る
        if (bot.entity.position.distanceTo(route.from.offset(0.5, 0, 0.5)) > 2.5) await stepTo(bot, route.from, signal)
        for (const cell of route.cells) {
          signal.throwIfAborted()
          const block = bot.blockAt(cell)
          if (block?.boundingBox !== 'block') continue
          await equipToolFor(bot, state, block, { drops: false })
          await bot.dig(block, true)
          dug++
        }
        await stepTo(bot, route.outside, signal)
        if (!closedSpace(bot)) {
          await collectNearbyDrops(bot, state, signal)
          const at = standingCell(bot)
          return `dug out through the wall (${dug} block${dug === 1 ? '' : 's'}) and can walk out now (at ${at.x},${at.y},${at.z})`
        }
      }
      throw new Error(`dug ${dug} blocks and still cannot walk out`)
    } catch (e) {
      // 続けて出し直さない（反射や中止で止められたのは失敗ではない）
      if (!signal.aborted) state.digOutBlockedUntil = Date.now() + CLIMB_BLOCK_MS
      throw e
    }
  },
  // 1 本の柱の頂上（自分で積んだ塔。まわりは崖）から降りる: 足元のブロックを掘って 1 つずつ落ちる。掘ったブロックは
  // 拾えるので、降りるほど持ち物が戻る。柱でない所（自然の崖の縁）は掘らない。落ちる先が 3 ブロックを超えるなら止まる
  async climb_down (bot, state, c, signal) {
    if (!isolatedSpace(bot)) return 'not on a pillar over a drop: you can walk from here'
    let dug = 0
    try {
      while (dug < DESCEND_MAX) {
        signal.throwIfAborted()
        if (!isolatedSpace(bot)) break
        const under = pillarUnder(bot)
        if (!under) throw new Error('the block under your feet is not a free-standing pillar (natural ground or a wall beside it): it will not be dug away')
        const y0 = bot.entity.position.y
        await equipToolFor(bot, state, under, { drops: false })
        await bot.dig(under, true)
        dug++
        const t0 = Date.now()
        while (bot.entity.position.y > y0 - 0.9 && Date.now() - t0 < 1500) await bot.waitForTicks(1)
      }
    } catch (e) {
      if (!signal.aborted) state.descendBlockedUntil = Date.now() + CLIMB_BLOCK_MS
      throw e
    }
    await collectNearbyDrops(bot, state, signal)
    const at = standingCell(bot)
    if (isolatedSpace(bot)) throw new Error(`dug down ${dug} blocks and still stand over a drop at ${at.x},${at.y},${at.z}`)
    return `dug down ${dug} block${dug === 1 ? '' : 's'} of the pillar and can walk off now (at ${at.x},${at.y},${at.z})`
  },
  async flee (bot, state, c, signal) {
    const h = bot.entities[c.entityId]
    if (!h) return `${c.target} is gone`
    return await flee(bot, h, signal)
  },
  // 同じ食べ物を、満腹度が 20 になるまで続けて食べる。次の 1 個が捨てる点数が EAT_WASTE_OK を超えるなら
  // やめる（healTo: 回復のために食べるときは、その満腹度に届くまでは捨ててもやめない）
  async eat (bot, state, c, signal) {
    const before = bot.food
    const points = bot.registry.foodsByName[c.item]?.foodPoints ?? 0
    let ate = 0
    while (ate < EAT_MAX) {
      signal.throwIfAborted()
      const food = bot.inventory.items().find((i) => i.name === c.item)
      if (!food) break
      if (bot.food >= 20) break
      if (ate && points - (20 - bot.food) > EAT_WASTE_OK && bot.food >= (c.healTo ?? 0)) break
      await bot.equip(food, 'hand')
      await bot.consume()
      ate++
    }
    if (!ate) throw new Error(bot.food >= 20 ? 'already full' : `no ${c.item}`)
    return `ate ${ate} ${c.item}, hunger ${before} -> ${bot.food}`
  },
  async equip (bot, state, c, signal) {
    const item = bot.inventory.items().find((i) => i.name === c.item)
    if (!item) throw new Error(`no ${c.item}`)
    await bot.equip(item, 'hand')
    return `holding ${c.item}`
  },
  async craft (bot, state, c, signal) {
    // 新しい材料を持つとレシピが解放される。サーバーはその直後にレシピを送る
    for (let i = 0; i < RECIPE_UNLOCK_TICKS && !state.recipeBook.recipesFor(c.item).length; i++) await bot.waitForTicks(1)
    let table = null
    if (c.needsTable) {
      table = findTable(bot, state)
      const carried = itemCount(bot, 'crafting_table') > 0
      if (!table) throw new Error(`no crafting table nearby${carried ? ': you carry a crafting_table, place it next to you (place) and craft again' : ''}`)
      try {
        await goNear(bot, table.position, 3, signal)
      } catch (e) {
        // 壁の向こうの作業台へ歩こうとして届かない（閉じた縦穴の中で、持っている作業台を置かずに No path）
        if (!carried || /abort/i.test(e.message)) throw e
        throw new Error(`${e.message} (the crafting table at ${table.position.x},${table.position.y},${table.position.z} cannot be reached; you carry a crafting_table: place it next to you (place) and craft again)`)
      }
    }
    const before = itemCount(bot, c.item)
    for (let i = 0; i < c.times; i++) {
      signal.throwIfAborted()
      await craftWithRecipeBook(bot, state.recipeBook, c.item, { table })
    }
    const more = c.more > 0 ? ` (you carry enough for ${c.more} more craft${c.more > 1 ? 's' : ''}: times=${c.more})` : ''
    return `crafted ${itemCount(bot, c.item) - before} ${c.item}${more}`
  },
  // ボットのそばに作業台かかまどを置く
  // 置いてある家具を拾って置き直す（furniture.mjs）: 壊して拾い、ベッドは家に、ほかは指定の場所か
  // 家の中の空いた所（なければ近く）に置く
  async move_placed (bot, state, c, signal) {
    const from = `${c.pos.x},${c.pos.y},${c.pos.z}`
    const wasHomeBed = !!state.home?.bed && c.block.endsWith('_bed') &&
      bot.blockAt(state.home.bed)?.name === c.block && state.home.bed.distanceTo(c.pos) <= 1
    await PRIMITIVES.dig(bot, state, { pos: c.pos, block: c.block }, signal)
    if (/chest|barrel/.test(c.block)) forgetChest(state.memory, c.pos)
    if (wasHomeBed) state.home.bed = null
    signal.throwIfAborted()
    if (c.block.endsWith('_bed')) return `${await PRIMITIVES.place_bed(bot, state, c, signal)} (moved from ${from})`
    const inside = stationSpots(bot, state, c.block).find((s) => s.where === 'inside')?.pos
    const placed = await PRIMITIVES.place_station(bot, state, { item: c.block, pos: c.to ?? inside }, signal)
    return `${placed} (moved from ${from})`
  },
  async place_station (bot, state, c, signal) {
    const item = bot.inventory.items().find((i) => i.name === c.item)
    if (!item) throw new Error(`no ${c.item}`)
    // 候補が選んだ場所（家の中か外）。まだ空いていなければ、今置ける場所
    const chosen = c.pos && bot.blockAt(c.pos)?.name === 'air' ? c.pos : null
    const pos = chosen ?? stationSpot(bot, state, c.item)
    if (!pos) throw new Error(`no free spot for the ${c.item}`)
    await bot.equip(item, 'hand')
    await bot.placeBlock(bot.blockAt(pos.offset(0, -1, 0)), { x: 0, y: 1, z: 0 })
    return `placed ${c.item} at ${pos}`
  },
  // 続けて c.count 個置く（道具 build_next。1 ブロックごとに LLM が呼ばれ、144 ブロックの家が判断だけで
  // 14 分かかっていた）。材料が尽きた・置けなかったときは、置けた分を返して止まる。候補（planBlock）は 1 個
  async place_plan (bot, state, c, signal) {
    const total = c.planBlock ? 1 : Math.max(1, Math.floor(c.count ?? 1))
    let placed = 0
    let lastPlaced = ''
    for (let i = 0; i < total; i++) {
      signal.throwIfAborted()
      let result
      try {
        result = await PRIMITIVES.place_plan_once(bot, state, c, signal)
      } catch (e) {
        if (!placed) throw e
        return `${lastPlaced}; stopped after ${placed} blocks: ${e.message}`
      }
      if (/^the (plan|build .+) is complete$/.test(result)) return placed ? lastPlaced : result // もう置く物がなかった
      placed++
      lastPlaced = result
      if (/ is complete/.test(result)) break // 最後の 1 個を置いた
    }
    return placed > 1 ? `${lastPlaced} — ${placed} blocks placed in a row` : lastPlaced
  },
  async place_plan_once (bot, state, c, signal) {
    if (c.build) return placeBuildBlock(bot, state, c.build, signal)
    const plan = state.plan
    if (!plan.origin) {
      // 選んだ場所に建てる計画なら、まずそこまで歩く
      if (plan.site) {
        await legToward(bot, state, plan.site, 'the site of the house', signal)
        await goto(bot, new goals.GoalNearXZ(plan.site.x, plan.site.z, 4), signal)
      }
      const origin = findSite(bot, plan.size)
      if (!origin) {
        plan.siteSearchFailedAt = bot.entity.position.clone()
        throw new Error(`no flat ${plan.size.width}x${plan.size.depth} site nearby; explore elsewhere`)
      }
      plan.origin = origin
    }
    // 候補が指したブロック（向きの違うドアの置き直しなど）を置く。前は計画の最初の未完成の
    // ブロックを置いていて、「place door」を選んでも別のブロックで失敗し続けた
    const b = planBlockToPlace(bot, plan, c.planBlock)
    if (!b) return 'the plan is complete'
    if (b.block === 'door') {
      // 家から遠ければ、まずドアへ区間ごとに歩く（遠くから直しに行って毎回時間切れになった）
      await legToward(bot, state, plan.worldPos(b), 'the door of the house', signal)
    }
    try {
      await placeOne(bot, plan, b, signal)
      // 床が直ったら、ドアの失敗の数え直し（床がなくて置けなかった失敗を、ドアのやり方の失敗として数え続けない）
      if (b.block === 'door' || b.foundation) state.doorFailures = 0
    } catch (e) {
      // 時間切れも数える（襲われて止まったのは数えない）
      const why = signal.aborted ? String(signal.reason?.message ?? '') : ''
      if (b.block === 'door' && !/^(interrupted|reflex)/.test(why)) state.doorFailures = (state.doorFailures ?? 0) + 1
      throw e
    }
    const s = plan.status(bot)
    return `placed ${b.block} (${s.placed}/${s.total})`
  },
  async place_bed (bot, state, c, signal) {
    await enterHome(bot, state.home, signal)
    const spot = bedSpot(bot, state.home)
    if (!spot) throw new Error('no free spot for the bed in the house')
    const { stand } = spot
    await goto(bot, new goals.GoalBlock(stand.x, stand.y, stand.z), signal)
    // ベッドの頭側は、プレイヤーが向いている方向に1ブロック先になる。向きは次の移動パケットで
    // やっとサーバーに届く: すぐに置くと、サーバーはドアを閉めたときの向きを使い、ベッドの頭側が
    // ドアの内側のセルをふさいだ。
    // 色の決まったベッド（placed(blue_bed)）ならそれを、なければどれでも
    const bed = bot.inventory.items().find((i) => i.name === c.item) ?? bot.inventory.items().find((i) => i.name.endsWith('_bed'))
    await bot.equip(bed, 'hand')
    await bot.lookAt(spot.foot.offset(0.5, 0, 0.5), true)
    await bot.waitForTicks(2)
    await bot.placeBlock(bot.blockAt(spot.foot.offset(0, -1, 0)), { x: 0, y: 1, z: 0 })
    // placeBlock が待つのはクリックしたセルだけ。頭側の更新は別に届く
    const head = spot.foot.plus(spot.inward)
    for (let i = 0; i < 20 && !bot.blockAt(head)?.name.endsWith('_bed'); i++) await bot.waitForTicks(1)
    if (!bot.blockAt(spot.foot)?.name.endsWith('_bed')) throw new Error('the bed was not placed')
    if (!bot.blockAt(head)?.name.endsWith('_bed')) throw new Error(`the bed was placed facing the wrong way (head not at ${head})`)
    state.home.bed = spot.foot
    return `placed ${bot.blockAt(spot.foot).name} in the house`
  },
  async sleep (bot, state, c, signal) {
    await enterHome(bot, state.home, signal)
    await sleepOn(bot, bot.blockAt(state.home.bed))
    // 全プレイヤーが眠ると夜が飛ばされ、サーバーがボットを起こす
    while (bot.isSleeping && !signal.aborted) await bot.waitForTicks(10)
    if (bot.isSleeping) await bot.wake()
    // サーバーは新しい時刻を送る前にボットを起こす: 次の更新を待ってから、本当に夜が明けたかを
    // 確かめる（モンスターに起こされても眠りは終わる）
    await once(bot, 'time', { signal: AbortSignal.timeout(TIME_UPDATE_TIMEOUT_MS) })
    const tod = bot.time.timeOfDay
    if (tod >= SLEEP_FROM && tod <= SLEEP_UNTIL) throw new Error(`woke up but the night was not skipped (time ${tod})`)
    return `slept through the night; time ${tod}`
  },
  async sleep_at (bot, state, c, signal) {
    try {
      // 村の家のベッドはドアの向こう（歩くときはドアを開けて通る: doors.mjs）
      await goto(bot, new goals.GoalNear(c.pos.x, c.pos.y, c.pos.z, 2), signal)
    } catch (e) {
      if (!isNoPath(e)) throw e
      markNoPath(state, c.pos)
      throw new Error(`No path to the bed at ${c.pos.x},${c.pos.y},${c.pos.z} (closed in or cut off): it is not offered for a while`)
    }
    const bed = bot.blockAt(c.pos)
    if (!bed?.name.endsWith('_bed')) throw new Error(`no bed at ${c.pos.x},${c.pos.y},${c.pos.z} any more`)
    return sleepIn(bot, bed, signal)
  },
  async bed_here (bot, state, c, signal) {
    const item = bot.inventory.items().find((i) => i.name.endsWith('_bed'))
    if (!item) throw new Error('no bed to place')
    const spot = bedSpotHere(bot, state)
    if (!spot) throw new Error('no flat free spot for a bed here')
    await bot.equip(item, 'hand')
    await bot.lookAt(spot.head.offset(0.5, 0, 0.5), true)
    await bot.placeBlock(bot.blockAt(spot.foot.offset(0, -1, 0)), { x: 0, y: 1, z: 0 })
    const bed = bot.blockAt(spot.foot)
    if (!bed?.name.endsWith('_bed')) throw new Error('the bed was not placed')
    return sleepIn(bot, bed, signal)
  },
  async exit_wall (bot, state, c, signal) {
    await digExit(bot, state.home, c.spot, signal)
    // 出る前に掘ったブロック（壁を閉じるのに使う）を拾う: ブロックは内側にも落ちるので、出てから
    // 拾おうとすると、ボットは取りに中へ戻り、自分を閉じ込めた。
    await collectNearbyDrops(bot, state, signal)
    signal.throwIfAborted()
    await stepOut(bot, c.spot, signal)
    await repairWall(bot, state.home, signal)
    if (isInside(bot, state.home)) throw new Error('closed the wall again but is still inside')
    return `left the house through the ${c.target} and closed it behind`
  },
  async repair_wall (bot, state, c, signal) {
    await repairWall(bot, state.home, signal)
    return 'the house wall is closed again'
  },
  async go_home (bot, state, c, signal) {
    let note = ''
    try {
      await legToward(bot, state, state.home.outside, 'home', signal)
      note = await enterHome(bot, state.home, signal)
    } catch (e) {
      // 行けない家を夜の選択肢に出し続けない（ほかの夜の越し方を選ばせる）
      if (isNoPath(e)) markNoPath(state, state.home.outside)
      throw e
    }
    return note ? `inside the house; ${note}` : 'inside the house with the door closed'
  },
  async wait (bot, state, c, signal) {
    if (c.inside) {
      await enterHome(bot, state.home, signal) // 開いたままならドアを閉める
      // ドアのほうを向いてじっとし、ときどき横を見る（毎秒向きを変えると、配信の画面が一晩中
      // 回り続けた）
      await bot.lookAt(state.home.door.offset(0.5, 1.2, 0.5))
    }
    const facing = bot.entity.yaw
    for (let i = 0; i < WAIT_TICKS / GLANCE_TICKS && !signal.aborted; i++) {
      await bot.waitForTicks(GLANCE_TICKS)
      await bot.look(facing + (i % 2 ? 0 : (Math.random() - 0.5) * GLANCE_ANGLE), 0)
    }
    const door = state.home ? `, door ${isDoorOpen(bot, state.home) ? 'open' : 'closed'}` : ''
    return `waited (time ${bot.time.timeOfDay}${door})`
  },
  async place_chest (bot, state, c, signal) {
    await enterHome(bot, state.home, signal)
    const spot = chestSpot(bot, state.home)
    if (!spot) throw new Error('no free spot for a chest in the house')
    const chest = bot.inventory.items().find((i) => i.name === 'chest')
    if (!chest) throw new Error('no chest')
    await bot.equip(chest, 'hand')
    await bot.lookAt(spot.offset(0.5, 0, 0.5), true)
    await bot.placeBlock(bot.blockAt(spot.offset(0, -1, 0)), { x: 0, y: 1, z: 0 })
    if (bot.blockAt(spot)?.name !== 'chest') throw new Error('the chest was not placed')
    rememberChest(state.memory, spot, {}, Number(bot.time.age))
    return `placed a chest in the house at ${spot.x},${spot.y},${spot.z}`
  },
  async deposit (bot, state, c, signal) {
    const n = Math.min(c.count, inventoryCounts(bot)[c.item] ?? 0)
    if (!n) throw new Error(`no ${c.item} to put in`)
    const put = await putInChests(bot, state, c, signal, [{ item: c.item, count: n }])
    if (!put.length) throw new Error('could not put anything in the chest: it is full')
    return `put ${put.join(', ')} in the chest`
  },
  // 余った物を捨てる（家のチェストがない・遠いとき。spare.mjs）。前に向かって捨てる: 下を向いたまま
  // 捨てると、足元に落ちて数秒後に拾い直す。捨てた物は state.tossed に覚えて、拾う候補に出さない
  async toss (bot, state, c, signal) {
    // 場所の指定（余った種: 街から離れた所）があれば、そこまで歩いて、街から離れる向きに捨てる
    if (c.pos) await goto(bot, new goals.GoalNear(c.pos.x, c.pos.y, c.pos.z, 2), signal)
    await bot.look(c.yaw ?? bot.entity.yaw, 0, true)
    const threw = []
    for (const { item, count } of c.items) {
      signal.throwIfAborted()
      const n = Math.min(count, inventoryCounts(bot)[item] ?? 0)
      if (!n) continue
      await bot.toss(bot.registry.itemsByName[item].id, null, n)
      noteTossed(state, bot, item)
      threw.push(`${n} ${item}`)
    }
    if (!threw.length) throw new Error('nothing to throw away: it is all gone from the inventory')
    return `threw away ${threw.join(', ')}; ${emptySlots(bot)} slots free`
  },
  async stash (bot, state, c, signal) {
    try {
      const items = c.items.map(({ item }) => ({ item, count: inventoryCounts(bot)[item] ?? 0 })).filter((i) => i.count > 0)
      const put = await putInChests(bot, state, c, signal, items)
      if (!put.length) throw new Error('could not put anything in the chest: it is full')
      return `put ${put.join(', ')} in the chest (kept the weapon, armor, food and torches)`
    } catch (e) {
      state.tidyBlockedUntil = Number(bot.time.age) + 5 * 60 * 20 // いっぱいなどで預けられない: しばらく出さない
      throw e
    }
  },
  // 今使わない物を、数どおりに家のチェストへ（candidates.mjs tidyUp）。入らなかったら、しばらく出さない
  async tidy (bot, state, c, signal) {
    let put = []
    try {
      const items = c.stow.map(({ item, count }) => ({ item, count: Math.min(count, inventoryCounts(bot)[item] ?? 0) })).filter((i) => i.count > 0)
      put = await putInChests(bot, state, c, signal, items)
    } catch (e) {
      state.tidyBlockedUntil = Number(bot.time.age) + 5 * 60 * 20
      throw e
    }
    if (!put.length) {
      state.tidyBlockedUntil = Number(bot.time.age) + 5 * 60 * 20
      throw new Error('could not put anything in the chest: it is full (or it is all gone from the inventory)')
    }
    return `put ${put.join(', ')} in the chest`
  },
  // 家のチェストの中身をまとめる（同じ物を 1 つのチェストに、半端なスタックを 1 つに: storage.mjs）。
  // 終わったら、中身の署名を覚えて、中身が変わるまで出さない
  async defrag (bot, state, c, signal) {
    await enterHome(bot, state.home, signal)
    const positions = homeChests(state.memory, state.home).map((r) => new Vec3(r.x, r.y, r.z))
    if (positions.length < 1) throw new Error('no chest in the home')
    try {
      const result = await defragChests(bot, positions, (pos, use) => withOpenChest(bot, state, { pos }, signal, use), signal)
      state.defragSig = contentsSignature(state)
      return result
    } catch (e) {
      state.defragBlockedUntil = Number(bot.time.age) + 5 * 60 * 20
      throw e
    }
  },
  async withdraw (bot, state, c, signal) {
    return withOpenChest(bot, state, c, signal, async (window) => {
      const inside = window.containerItems().filter((i) => i.name === c.item).reduce((s, i) => s + i.count, 0)
      const n = Math.min(c.count, inside)
      if (!n) throw new Error(`no ${c.item} in the chest (the record was wrong)`)
      await window.withdraw(bot.registry.itemsByName[c.item].id, null, n)
      state.withdrawn = { ...(state.withdrawn ?? {}), [c.item]: Number(bot.time.age) } // 預け直さない（candidates.mjs の recentlyTaken）
      return `took ${n} ${c.item} from the chest`
    })
  },
  // できたものを取り出し、材料と燃料を入れ、しばらく待ってできたものを取る。
  // 残りは後のステップで取り出す（その間もかまどは動く）
  async smelt (bot, state, c, signal) {
    // 精錬・調理は家の中のかまどだけ（候補は家のかまどしか出さないが、道具や古い記録からの呼び出しも断る）
    if (state.home && !inHomeRoom(state.home, c.pos)) throw new Error(`the furnace at ${c.pos.x},${c.pos.y},${c.pos.z} is not inside the house: smelting and cooking are done only in the furnace inside the house (go home first)`)
    await legToward(bot, state, c.pos, 'the furnace', signal)
    await goNear(bot, c.pos, 2, signal) // 遠いブロックは null になる: 着いてから判定する
    if (!state.home && isUnderground(bot)) throw new Error('smelting is not done underground (monsters spawn in the dark and a smelt keeps you standing still): do it on the surface or in the house')
    const block = bot.blockAt(c.pos)
    if (block?.name !== 'furnace') {
      forgetFurnace(state.memory, c.pos)
      if (state.lastFurnace && state.lastFurnace.x === c.pos.x && state.lastFurnace.y === c.pos.y && state.lastFurnace.z === c.pos.z) delete state.lastFurnace
      throw new Error(`no furnace at ${c.pos.x},${c.pos.y},${c.pos.z} any more`)
    }
    return tendFurnace(bot, state, c, block, signal)
  },
  // 地下から地上に出る: 最後に地上にいた場所、鉱山の入口、家の玄関の外の順に、地上に出るまで歩く（surface.mjs）
  async surface (bot, state, c, signal) {
    const exits = surfaceExits(bot, state)
    if (!exits.length) throw new Error('no known way out (nowhere higher that you stood): dig upward with a pickaxe, or explore toward daylight')
    let last = null
    for (const way of exits) {
      const pos = new Vec3(way.x, way.y, way.z)
      try {
        await legToward(bot, state, pos, 'a way to the surface', signal)
        await goNear(bot, pos, 3, signal)
      } catch (e) {
        if (signal.aborted) throw e
        last = e
        continue
      }
      if (!isUnderground(bot)) { const p = bot.entity.position.floored(); return `back on the surface at ${p.x},${p.y},${p.z}` }
    }
    throw new Error(`could not get to the surface by the ways known (${exits.length}): ${last?.message ?? 'still underground'}`)
  },
  // c.pos の地面に置く（完全なブロックの上の空いたセル）
  async place_torch_at (bot, state, c, signal) {
    const torch = bot.inventory.items().find((i) => i.name === 'torch')
    if (!torch) throw new Error('no torch')
    await goNear(bot, c.pos, 2, signal)
    const floor = bot.blockAt(c.pos.offset(0, -1, 0))
    if (floor?.boundingBox !== 'block' || bot.blockAt(c.pos)?.boundingBox !== 'empty') throw new Error(`no ground for a torch at ${c.pos.x},${c.pos.z} any more`)
    await bot.equip(torch, 'hand')
    await bot.placeBlock(floor, { x: 0, y: 1, z: 0 })
    if (bot.blockAt(c.pos)?.name !== 'torch') throw new Error('the torch was not placed')
    return `placed a torch at ${c.pos.x},${c.pos.y},${c.pos.z}`
  },
  async place_torch (bot, state, c, signal) {
    const spot = torchSpot(bot)
    if (!spot) throw new Error('no floor to stand a torch on here')
    const torch = bot.inventory.items().find((i) => i.name === 'torch')
    if (!torch) throw new Error('no torch')
    await bot.equip(torch, 'hand')
    await bot.placeBlock(bot.blockAt(spot.offset(0, -1, 0)), { x: 0, y: 1, z: 0 })
    if (bot.blockAt(spot)?.name !== 'torch') throw new Error('the torch was not placed')
    return `placed a torch at ${spot.x},${spot.y},${spot.z}`
  },
  async goto_memory (bot, state, c, signal) {
    await legToward(bot, state, c.pos, `where ${c.target} was seen`, signal)
    await goto(bot, new goals.GoalNearXZ(c.pos.x, c.pos.z, 3), signal)
    return `arrived where ${c.target} was seen (${c.pos.x},${c.pos.z})`
  },
  async survey (bot, state, c, signal) {
    await legToward(bot, state, c.pos, `the ${c.target} site`, signal)
    // 海や崖で中心まで行けなくても、ここまで来ていればまわりのチャンクは読み込まれている
    // （読み込めた割合は loaded_pct に出る）
    try {
      await goto(bot, new goals.GoalNearXZ(c.site.x, c.site.z, SURVEY_REACH), signal)
    } catch (e) {
      if (Math.hypot(c.site.x - bot.entity.position.x, c.site.z - bot.entity.position.z) > LEG) throw e
    }
    await bot.waitForTicks(20) // 着いた所のチャンクと動物が届くのを待つ
    const numbers = surveySite(bot, state, c.site)
    rememberSite(state.memory, numbers, Number(bot.time.age))
    return `surveyed the ${c.target} site: ${numbers.flat_plots} flat plots, water ${numbers.water_pct}%, steep ${numbers.steep_pct}%, ` +
      `stone ${numbers.stone}, coal ${numbers.coal}, iron ${numbers.iron}, logs ${numbers.logs}, animals ${numbers.animals}`
  },
  async dig_down (bot, state, c, signal) {
    // 階段で下りる間、約 9 m ごとに松明を置いて進む（trail.mjs）: 持つ数は掘る長さ（深さの 1.5 倍）から
    const downBy = bot.entity.position.y - c.pos.y
    if (c.pos.y <= 54 && downBy >= 8) {
      const need = torchesNeeded(downBy * 1.5)
      const have = inventoryCounts(bot).torch ?? 0
      if (have < need) throw new Error(`dig_down needs ${need} torches for ${Math.round(downBy)} blocks down (one about every ${TORCH_SPACING} m, so no monster can spawn behind you); you carry ${have}: make torches first (a torch is 1 coal or charcoal + 1 stick)`)
    }
    let surfaceY = state.goal?.surfaceY ?? Math.floor(bot.entity.position.y)
    const maxDepth = state.goal?.spec?.dig_depth ?? Infinity // 上限なし（溶岩・水・砂利・岩盤で止まる）
    let steps = 0
    let stop = ''
    // 前に掘った階段の底から続ける（毎回新しい階段を掘って、家のまわりを穴だらけにしない。quarry.mjs）。
    // 底まで歩けなければ、ここから掘る（歩けなかったのは鉱石のせいではないので、鉱石は届かない印にしない）
    const shaft = usableShaft(state, bot.entity.position, c.pos.y)
    let top = bot.entity.position.floored()
    let walkedDown = false
    if (shaft) {
      try {
        await goto(bot, new goals.GoalBlock(shaft.bottom.x, shaft.bottom.y, shaft.bottom.z), signal)
        top = new Vec3(shaft.top.x, shaft.top.y, shaft.top.z)
        surfaceY = shaft.top.y
        walkedDown = true
      } catch (e) {
        if (signal.aborted) throw e
        console.log(`[quarry] 階段の底まで歩けなかった: ${e.message}`)
      }
    }
    // 掘れる所が見えるか、下りられなくなるまで（dig_depth、溶岩・水・砂利・岩盤）下りる
    for (; ; steps++) {
      signal.throwIfAborted()
      if (digTargets(bot, state, c.target).length) { stop = `${c.target} can be dug now`; break }
      const me = bot.entity.position.floored()
      if (surfaceY - (me.y - 1) > maxDepth) { stop = `dig_depth ${maxDepth} reached`; break }
      // 目標へ向かう向きから順に試し、下りられる最初の向き（砂利の下や水のそばは避ける）
      const toward = ([dx, dz]) => -(dx * (c.pos.x - me.x) + dz * (c.pos.z - me.z))
      const tried = [...STAIR_DIRECTIONS].sort((a, b) => toward(a) - toward(b)).map((d) => ({ d, step: stairCells(me, d) }))
        .map((t) => ({ ...t, danger: stairDanger(bot, state, t.step) }))
      const next = tried.find((t) => !t.danger)
      if (!next) { stop = `cannot go down here (${[...new Set(tried.map((t) => t.danger))].join('; ')})`; break }
      for (const p of next.step.cells) {
        const block = bot.blockAt(p)
        if (!block || block.boundingBox === 'empty') continue
        await equipToolFor(bot, state, block)
        await bot.dig(block, true)
        signal.throwIfAborted()
      }
      const [feet] = next.step.cells.slice(-1)
      await goto(bot, new goals.GoalBlock(feet.x, feet.y, feet.z), signal)
    }
    if (!steps && !walkedDown) {
      markUnreachable(state, c.pos)
      throw new Error(`could not dig stairs down toward ${c.target}: ${stop}`)
    }
    if (steps || walkedDown) noteShaft(state, top, bot.entity.position.floored())
    await collectNearbyDrops(bot, state, signal)
    const depth = surfaceY - Math.floor(bot.entity.position.y)
    return `${walkedDown ? 'walked down the stairs dug before, then ' : ''}dug ${steps} steps down toward ${c.target} (${depth} blocks below where the goal started)${stop ? `; stopped: ${stop}` : ''}`
  },
  // 採掘場を設計どおりに掘る（設計書 47 §16）: 並びの次のセルから順に、隣の空いたセル（前の段）に立って
  // 上から空ける。時間では止めない（設計書 38）。危ないもの（溶岩・水・落ちる砂利・洞窟）に当たったら、
  // そこから先を設計から外して理由を残す（黙って浅くしない）
  async dig_mine (bot, state, c, signal) {
    const fac = state.facilities?.[c.facility]
    if (!fac?.layout || fac.kind !== 'mine') throw new Error(`there is no mine named ${c.facility}`)
    // 松明なしでは掘り始めない（暗い坑道はモブが湧く。夜も昼も同じ）: 足りないと知らせて、作りに戻らせる
    if (mineStatus(bot, fac).next && !bot.inventory.items().some((i) => i.name === 'torch')) {
      throw new Error(`no torch to light the mine ${fac.name} (a torch goes on the wall every ${MINE_TORCH.tunnel} cells in a tunnel, ${MINE_TORCH.stairs} on the stairs, once in each branch): make torches first (a torch is 1 coal or charcoal + 1 stick)`)
    }
    // すでに掘ったセルに暗い所があれば、先に明るくする（松明が壊された、枝の先に届いていなかった、など）
    if (await relightMine(bot, fac.layout.cells, signal) === 'missing') {
      throw new Error(`the mine ${fac.name} has dark cells (light below ${MIN_LIGHT}: monsters spawn there) and you carry no torch: make torches first (a torch is 1 coal or charcoal + 1 stick)`)
    }
    let dug = 0
    let last = -1
    let lastTorchAt = 0
    let walled = 0 // 丸石で塞いだ面の数
    const cut = [] // 危なくて打ち切った枝
    for (;;) {
      signal.throwIfAborted()
      const m = mineStatus(bot, fac)
      if (!m.next) break
      if (m.index === last) throw new Error(`could not clear ${m.next.x},${m.next.y},${m.next.z} of the mine ${fac.name} (dug ${dug} cells)`)
      last = m.index
      const cells = fac.layout.cells
      const cell = m.next
      const adjacent = (a) => Math.abs(a.x - cell.x) + Math.abs(a.z - cell.z) === 1 && a.y - cell.y >= 0 && a.y - cell.y <= 1
      const stand = cells.slice(0, m.index).reverse().find((a) => adjacent(a) && mineBlocks(a).slice(0, 2).every((p) => bot.blockAt(p)?.boundingBox === 'empty'))
      await goto(bot, stand ? new goals.GoalBlock(stand.x, stand.y, stand.z) : new goals.GoalNear(cell.x, cell.y, cell.z, 1), signal)
      signal.throwIfAborted()
      // この採掘場に出会った危ないもの（水・溶岩・岩盤・落ちる砂利）か、封じ切れない洞窟: 枝ならその枝だけ打ち切って
      // ほかの枝を掘り続ける。階段・部屋・坑道は、そこから先がなくなる
      const giveUp = (reason) => {
        stopMine(fac, m.index, reason)
        if (cell.part.startsWith('branch')) {
          cut.push(`${cell.part} at ${cell.x},${cell.y},${cell.z}: ${reason}`)
          last = -1
          return null
        }
        updateStatus(bot, fac)
        return `dug ${dug} cells of the mine ${fac.name}; stopped at ${cell.x},${cell.y},${cell.z}: ${reason} (the mine now ends before it)${cutNote(cut)}${sealNote(walled)}`
      }
      // 坑道と枝は、空洞（洞窟）に出たら、手元の丸石で床・壁・天井を作って塞ぐ（sealing.mjs）。床の穴は埋めて通る
      const sealable = !!fac.layout.sealing && cell.part !== 'stairs'
      const why = mineDanger(bot, cell, plannedBlocks(cells), sealable)
      if (why) {
        const stopped = giveUp(why)
        if (stopped) return stopped
        continue
      }
      const gaps = sealable ? openings(bot, state, cell) : []
      if (gaps.length && sealStock(bot) < gaps.length) {
        const g = gaps[0].pos
        const stopped = giveUp(`a cave opens at ${g.x},${g.y},${g.z}: walling it off takes ${gaps.length} cobblestone and you carry ${sealStock(bot)}`)
        if (stopped) return stopped
        continue
      }
      let dugAny = false
      for (const p of mineBlocks(cell).reverse()) {
        const block = bot.blockAt(p)
        if (!block || block.boundingBox === 'empty') continue
        await equipToolFor(bot, state, block)
        await bot.dig(block, true)
        signal.throwIfAborted()
        dugAny = true
      }
      if (gaps.length) {
        const sealed = await sealCell(bot, state, cell, signal, () => goto(bot, new goals.GoalBlock(cell.x, cell.y, cell.z), signal))
        walled += sealed.placed
        if (!sealed.ok) {
          // 塞げなかった: 前のセルから丸石で通路をふさぎ（洞窟とつながったままにしない）、先を打ち切る
          if (stand) await goto(bot, new goals.GoalBlock(stand.x, stand.y, stand.z), signal)
          await plugCell(bot, cell, signal)
          const r = sealed.remaining[0].pos
          const stopped = giveUp(`could not wall off the cave at ${r.x},${r.y},${r.z} with cobblestone (the passage was plugged)`)
          if (stopped) return stopped
          continue
        }
      }
      if (sealable) cell.sealed = true
      // もう空いていて、囲まれてもいる（洞窟に出ない）セルは、印だけ付けて次へ（掘ったことにしない）
      if (!dugAny && !gaps.length) continue
      dug++
      // 置く番で松明が尽きた: 暗いまま先へ掘らない。ここまでは掘ったので、成功として理由を返す
      const placed = await torchInMine(bot, cells, m.index)
      if (placed === 'placed') lastTorchAt = Date.now()
      if (placed === 'missing') {
        updateStatus(bot, fac)
        return `dug ${dug} cells of the mine ${fac.name}; stopped: out of torches (one is due on the wall here, every ${MINE_TORCH.tunnel} cells in a tunnel, ${MINE_TORCH.stairs} on the stairs, ${MINE_TORCH.branch}th in a branch): make torches first (a torch is 1 coal or charcoal + 1 stick), then dig on${cutNote(cut)}${sealNote(walled)}`
      }
      // 実際の光を見る: 間隔どおりに置いても、枝の先や、壊された松明のあとは暗いままになる。ここから近い、掘り終えた
      // セル（いま掘ったセルは光がまだ届かないので除く）に光が足りないものがあれば、ここにも 1 本。松明を置いた直後は
      // 光が届くまで見ない
      if (Date.now() - lastTorchAt > LIGHT_SETTLE_MS) {
        const near = darkCells(bot, cells.filter((c) => c !== cell && Math.hypot(c.x - cell.x, c.y - cell.y, c.z - cell.z) <= LIGHT_WINDOW))
        if (near.length) {
          const torch = torchOf(bot)
          if (!torch) {
            updateStatus(bot, fac)
            return `dug ${dug} cells of the mine ${fac.name}; stopped: ${near.length} cells near ${cell.x},${cell.y},${cell.z} are too dark (light below ${MIN_LIGHT}) and you carry no torch: make torches first (a torch is 1 coal or charcoal + 1 stick), then dig on${cutNote(cut)}${sealNote(walled)}`
          }
          if (await wallTorch(bot, cells, cell, torch)) lastTorchAt = Date.now()
        }
      }
      // 探している鉱石が壁に出た（見えているのは、空気に面した、届くもの: 設計書 86）: 掘るのは次の手に任せる。
      // 3 セルごとに見る（探す処理が重い）。1 セルは掘ってから止まる（同じ鉱石の拒否でここで止まり続けない）
      if (c.sources?.length && dug % ORE_CHECK_EVERY === 0) {
        const shown = oreShowing(bot, state, c.sources)
        if (shown) {
          updateStatus(bot, fac)
          return `dug ${dug} cells of the mine ${fac.name}; ${shown.name} shows in the wall at ${shown.pos.x},${shown.pos.y},${shown.pos.z}: dig it${cutNote(cut)}${sealNote(walled)}`
        }
      }
    }
    updateStatus(bot, fac)
    await collectNearbyDrops(bot, state, signal)
    // 掘り終えたら、最後のセルを含めて、暗い所を残さない
    const unlit = await relightMine(bot, fac.layout.cells, signal) === 'missing' ? `; some cells are still too dark (light below ${MIN_LIGHT}) and you carry no torch: make torches first` : ''
    const shown = c.sources?.length ? oreShowing(bot, state, c.sources) : null
    return `dug ${dug} cells of the mine ${fac.name}; the mine is ${fac.status === 'done' ? 'complete' : fac.status}${unlit}${shown ? `; ${shown.name} shows in the wall at ${shown.pos.x},${shown.pos.y},${shown.pos.z}: dig it` : ''}${cutNote(cut)}${sealNote(walled)}`
  },
  // 採掘場を自分で設計して掘る（設計書 68）: 家の外周（壁から 10〜30 m）の、鉱石の高さまで下りられる所に、
  // 階段と坑道を設計し、そのまま掘り始める。掘り切る前に止まっても、次は dig_mine が続きから掘る
  async make_mine (bot, state, c, signal) {
    if (!mineFromHome(state)) throw new Error('a mine is designed near a home you built (there is none)')
    // 家の外周は、遠いと読み込まれていない
    if (bot.entity.position.distanceTo(state.home.inside) > MINE_SITE_WALK) await legToward(bot, state, state.home.inside, 'the home', signal)
    // 掘り尽くして鉱石が出なかった採掘場に印を付ける（次の計画で、同じ採掘場へ下りない）
    if (c.exhausted && state.facilities?.[c.exhausted]?.layout) state.facilities[c.exhausted].layout.exhausted = true
    let fac
    try {
      fac = designMine(bot, state, c.bottom_y, mineSites(state))
    } catch (e) {
      if (e instanceof FacilityError) throw new Error(`could not design a mine down to y ${c.bottom_y} near the home: ${e.message}`)
      throw e
    }
    const dug = await PRIMITIVES.dig_mine(bot, state, { facility: fac.name, sources: c.sources }, signal)
    return `designed the mine ${fac.name} (entrance ${fac.entrance.x},${fac.entrance.y},${fac.entrance.z}, ${fac.layout.tunnels.length} tunnels of ${fac.layout.tunnel_length} with branches of ${fac.layout.branch_length} every 3 blocks); ${dug}`
  },
  // 道具 goto（設計書 21）: 位置へ向かう。遠ければ 1 回 1 区間
  // 死んだ場所へ戻って、落とした持ち物を拾う（5 分で消える）。着いても何も落ちていなければ諦める
  async recover_items (bot, state, c, signal) {
    const death = state.memory.deaths[0]
    if (!death) throw new Error('no death is remembered')
    death.tries = (death.tries ?? 0) + 1
    await legToward(bot, state, c.pos, `${c.pos.x},${c.pos.y},${c.pos.z}`, signal)
    await goto(bot, new goals.GoalNear(c.pos.x, c.pos.y, c.pos.z, 3), signal)
    const before = inventoryCounts(bot)
    for (let i = 0; i < 6 && nearbyDrops(bot, state, RECOVER_RADIUS).length; i++) {
      const drop = nearbyDrops(bot, state, RECOVER_RADIUS)[0]
      const collected = waitForCollect(bot, PICKUP_WAIT_MS)
      // 行き着けない落とし物（水の中、囲まれた所）は、ここで諦める（続けて別の物へ行くと、その分も
      // 動けなくなるまで待つ）。行った回数（death.tries）が 2 に達すれば、もう出さない
      const reached = await goNear(bot, drop.e.position, 0.5, signal).then(() => true, (e) => { if (signal.aborted) throw signal.reason; return false })
      await collected
      if (!reached) break
    }
    const gained = gainedSince(bot, before)
    if (!nearbyDrops(bot, state, RECOVER_RADIUS).length) death.recovered = true
    if (gained <= 0) throw new Error(`nothing left to pick up where you died (${dropsAround(bot)})`)
    return `recovered ${gained} items where you died`
  },
  async goto_pos (bot, state, c, signal) {
    await legToward(bot, state, c.pos, `${c.pos.x},${c.pos.y},${c.pos.z}`, signal)
    const goal = new goals.GoalNear(c.pos.x, c.pos.y, c.pos.z, c.range ?? 1)
    await goto(bot, goal, signal)
    return arrivalReport(bot, goal, c.pos)
  },
  // c.pos の空いたセルに c.item を置く。c.against は面を借りる隣のブロック（下なら床、横なら壁に付く）。
  // 道具 place（設計書 21）と placed(item, where) の提案（placing.mjs）が使う
  async place_at (bot, state, c, signal) {
    const item = bot.inventory.items().find((i) => i.name === c.item)
    if (!item) throw new Error(`no ${c.item}`)
    // 家の中のセルは、家に入ってから（閉じた家の外から経路を探すと、時間切れになる: 2026-10-08）
    if (state.home && isHomeCell(state.home, c.pos) && !isInside(bot, state.home)) await enterHome(bot, state.home, signal)
    await goto(bot, new goals.GoalPlaceBlock(c.pos, bot.world, { range: REACH }), signal)
    signal.throwIfAborted()
    const against = bot.blockAt(c.against)
    if (against?.boundingBox !== 'block') throw new Error(`nothing to place against at ${c.against.x},${c.against.y},${c.against.z} any more`)
    await bot.equip(item, 'hand')
    const before = bot.blockAt(c.pos)?.name
    await bot.placeBlock(against, c.pos.minus(c.against))
    // 置けたかはセルが変わったかで見る（松明・看板などは当たり判定がなく、名前も wall_torch などに変わる。
    // 2026-09-27: 当たり判定で見ていて、壁に付いた松明を「置けなかった」としていた）
    const placed = bot.blockAt(c.pos)
    if (!placed || placed.name === before) throw new Error(`the ${c.item} was not placed`)
    return `placed ${placed.name} at ${c.pos.x},${c.pos.y},${c.pos.z}`
  },
  // 掘った跡に水が流れ込んでいる: 同じ物（なければ土や丸石）で埋める（docs/design/55）。行った回数を数え、
  // 2 回で諦める（ledger.mjs PLUG_TRIES）
  async plug_hole (bot, state, c, signal) {
    const hole = (state.dug ?? []).find((d) => d.x === c.pos.x && d.y === c.pos.y && d.z === c.pos.z && !d.plugged)
    if (hole) hole.tries = (hole.tries ?? 0) + 1
    const against = supportFor(bot, c.pos, c.item, 'floor') ?? supportFor(bot, c.pos, c.item)
    if (!against) throw new Error(`nothing next to ${c.pos.x},${c.pos.y},${c.pos.z} to plug against`)
    const result = await PRIMITIVES.place_at(bot, state, { item: c.item, pos: c.pos, against }, signal)
    if (hole) hole.plugged = true
    return `plugged the hole at ${c.pos.x},${c.pos.y},${c.pos.z} (${result})`
  },
  // 整地（設計書 41）: c.where のまわり半径 c.radius を、近い列から次々にそろえる。出っ張りを先に
  // （土が出る）、埋めるのは持っている土で。行けない・掘れない列は飛ばす。時間では止めない
  // （夕方・空腹・考え直しでは runner が止める）。そろえた列がなければ失敗
  async level (bot, state, c, signal) {
    // 畑の整地（設計書 75）: c.facility の四角と柵の輪を、畑の地面の高さにそろえる（c.where / c.radius は円）
    const farm = c.facility ? state.facilities?.[c.facility] : null
    if (c.facility && !farm?.layout?.rect) throw new Error(`there is no designed farm named ${c.facility} to level`)
    if (farm) {
      const { rect, y } = farm.layout
      noteLevelArea(state, { x: rect.x0 + Math.floor(rect.w / 2), y: y + 1, z: rect.z0 + Math.floor(rect.d / 2), radius: Math.ceil(Math.hypot(rect.w + 2 * FARM_MARGIN, rect.d + 2 * FARM_MARGIN) / 2) + 1 })
    } else {
      noteLevelZone(state, c.where, c.radius) // ここは土を取るために掘らない（leveling.mjs）
    }
    const statusNow = () => farm ? farmLevel(bot, state, farm) : levelStatus(bot, state, c.where, c.radius)
    const skipped = new Map()
    const tries = new Map() // 同じセルを何度も（掘ったのに残る、置いたのに空く）: 3 回で飛ばす
    let cut = 0
    let filled = 0
    let since = 0 // 落とし物を拾ってからの掘った数
    for (;;) {
      signal.throwIfAborted()
      const s = statusNow()
      const me = bot.entity.position
      const near = (p) => p.distanceTo(me)
      const open = (p) => !skipped.has(`${p.x},${p.z}`)
      const filler = farm ? soilItem(bot) : fillItem(bot) // 畑の地面は耕せる土だけで埋める
      // 列ごとに上から掘る・下から埋める: 近い列のいちばん上 / いちばん下
      const cuts = s.cuts.filter(open).sort((a, b) => near(a) - near(b) || b.y - a.y)
      const fills = filler ? s.fills.filter(open).sort((a, b) => near(a) - near(b) || a.y - b.y) : []
      const next = cuts[0] ? { p: topOf(cuts, cuts[0]), cut: true } : fills[0] ? { p: bottomOf(fills, fills[0]), cut: false } : null
      if (!next) {
        if (since) await collectNearbyDrops(bot, state, signal)
        const left = s.off ? `; ${s.off} columns still off${skipped.size ? ` (could not reach or work ${skipped.size}: ${[...new Set(skipped.values())].slice(0, 2).join('; ')})` : ''}${!filler && s.fills.length ? ` (no dirt to fill ${s.fills.length} blocks)` : ''}` : ''
        if (!cut && !filled) throw new Error(`could not level any of the ground${left}`)
        return `leveled the ground: dug ${cut} blocks, filled ${filled}${left}`
      }
      const { p } = next
      const k = `${p.x},${p.y},${p.z}`
      tries.set(k, (tries.get(k) ?? 0) + 1)
      bot.actionPhase = `${next.cut ? 'digging' : 'filling'} ${k} to level the ground`
      try {
        if (tries.get(k) > 3) throw new Error('tried 3 times and it did not change')
        if (next.cut) {
          const block = bot.blockAt(p)
          // 畑の整地は、低い耕地の作物（上に生えているもの）も掘る
          if (!TERRAIN.test(block?.name ?? '') && !(farm && (block?.boundingBox === 'empty' || farmRebuildable(block?.name)))) throw new Error(`${block?.name} is not natural ground`)
          const wet = liquidReason(bot, p, block)
          if (wet) throw new Error(wet)
          if (!withinReach(bot, p)) await goto(bot, new goals.GoalLookAtBlock(p, bot.world, { reach: REACH }), signal)
          for (let i = 0; i < 40 && !bot.entity.onGround; i++) await bot.waitForTicks(1)
          await equipToolFor(bot, state, block)
          await bot.dig(bot.blockAt(p), true)
          cut++
          if (++since >= 8) { await collectNearbyDrops(bot, state, signal); since = 0 }
        } else {
          const below = bot.blockAt(p.offset(0, -1, 0))
          if (below?.boundingBox !== 'block') throw new Error(`nothing to place on below ${p.x},${p.y},${p.z}`)
          if (bot.entity.position.floored().equals(p) && inventoryCounts(bot).dirt > 0) {
            // 穴の中に立っている: 1 つ上に立つ所へ行かせると、pathfinder が足元に土を積んで登る
            // （1x1 の塔。足場は土）。深い穴も下から 1 段ずつ埋まる（2026-09-27: 穴の中から
            // 埋められず、その列を飛ばして整地が失敗した）
            await goto(bot, new goals.GoalBlock(p.x, p.y + 1, p.z), signal)
            if (bot.blockAt(p)?.boundingBox === 'block') {
              filled++
              continue
            }
            // 浅い穴は、足元に積まなくても縁へ出られる: 外から埋める（下へ続く）
          }
          // 丸石しかなければ積めない（足場は土だけ）: 縁に出てから置く（置く所には立たない）
          if (!withinReach(bot, p) || bot.entity.position.floored().equals(p)) await goto(bot, new goals.GoalPlaceBlock(p, bot.world, { range: REACH }), signal)
          if (bot.entity.position.floored().equals(p)) throw new Error('standing in the hole')
          // 出る途中で足元に積んで、土を使い切ることがある: 次の周で埋める物がなくなって終わる
          const fill = farm ? soilItem(bot) : fillItem(bot)
          if (!fill) continue
          // 花や草が咲いていると置けない（「the block is still red_tulip」）: 先にどける
          const here = bot.blockAt(p)
          if (here && here.boundingBox === 'empty' && !/^(air|cave_air|void_air|water|lava)$/.test(here.name)) await bot.dig(here, true)
          await bot.equip(fill, 'hand')
          await bot.placeBlock(below, new Vec3(0, 1, 0)) // 断られたら place-report.mjs の installPlaceReport が記録する
          filled++
        }
      } catch (e) {
        if (signal.aborted) throw e
        skipped.set(`${p.x},${p.z}`, `${p.x},${p.z}: ${e.message.slice(0, 80)}`)
      } finally {
        bot.actionPhase = ''
      }
    }
  },
  // 畑のまわりの柵（設計書 75）: 輪の足りない所に、近い順に柵を立てる。持っている柵がなくなるか、立てられる所が
  // なくなるまで。1 つも立てられなければ失敗
  async fence (bot, state, c, signal) {
    const fac = state.facilities?.[c.facility]
    if (!fac?.layout?.fence) throw new Error(`there is no designed farm named ${c.facility} with a fence`)
    const skipped = new Map()
    let placed = 0
    for (;;) {
      signal.throwIfAborted()
      const todo = layoutStatus(bot, fac).fence.filter((p) => !skipped.has(`${p.x},${p.z}`))
      const item = bot.inventory.items().find((i) => FENCE_ITEM.test(i.name))
      if (!todo.length || !item) break
      const me = bot.entity.position
      const p = todo.sort((a, b) => a.distanceTo(me) - b.distanceTo(me))[0]
      bot.actionPhase = `placing a fence at ${p.x},${p.y},${p.z}`
      try {
        await PRIMITIVES.place_at(bot, state, { item: item.name, pos: p, against: p.offset(0, -1, 0) }, signal)
        placed++
      } catch (e) {
        if (signal.aborted) throw e
        skipped.set(`${p.x},${p.z}`, `${p.x},${p.z}: ${e.message.slice(0, 80)}`)
      } finally {
        bot.actionPhase = ''
      }
    }
    const left = layoutStatus(bot, fac).fence.length
    if (!placed) throw new Error(`could not place any fence around ${fac.name}${skipped.size ? ` (${[...skipped.values()].slice(0, 2).join('; ')})` : ' (no fence carried)'}`)
    return `placed ${placed} fences around the farm ${fac.name}${left ? `, ${left} still to place` : ''}`
  },
  // 畑の柵のゲート: 輪の外に立ち、輪に向かって置く（ゲートの向きは置くときに向いている向きで決まる。向きが輪と
  // 合わないと、通れない）
  async fence_gate (bot, state, c, signal) {
    const fac = state.facilities?.[c.facility]
    const gate = fac?.layout?.gate
    if (!gate) throw new Error(`there is no designed farm named ${c.facility} with a gate`)
    const item = bot.inventory.items().find((i) => FENCE_GATE_ITEM.test(i.name))
    if (!item) throw new Error('no fence gate')
    const pos = new Vec3(gate.x, gate.y, gate.z)
    const stand = pos.offset(gate.out[0], 0, gate.out[1])
    await goto(bot, new goals.GoalBlock(stand.x, stand.y, stand.z), signal)
    signal.throwIfAborted()
    const against = pos.offset(0, -1, 0)
    if (bot.blockAt(against)?.boundingBox !== 'block') throw new Error(`nothing to place the gate on at ${against.x},${against.y},${against.z}`)
    await bot.lookAt(pos.offset(0.5, 0.5, 0.5), true)
    return await PRIMITIVES.place_at(bot, state, { item: item.name, pos, against }, signal)
  },
  // 植林と畑（設計書 33）。c.pos: 苗木を置く空いたセル / 耕す土 / 種をまく耕地
  async plant (bot, state, c, signal) {
    const item = bot.inventory.items().find((i) => i.name === c.item)
    if (!item) throw new Error(`no ${c.item}`)
    await goNear(bot, c.pos, 3, signal)
    const ground = bot.blockAt(c.pos.offset(0, -1, 0))
    if (ground?.boundingBox !== 'block' || bot.blockAt(c.pos)?.name !== 'air') throw new Error(`no free ground for a sapling at ${c.pos.x},${c.pos.y},${c.pos.z} any more`)
    await bot.equip(item, 'hand')
    await bot.placeBlock(ground, { x: 0, y: 1, z: 0 })
    const placed = bot.blockAt(c.pos)
    if (!isSaplingItem(placed?.name)) throw new Error(`the ${c.item} was not planted (${placed?.name ?? 'nothing'} there)`)
    recordPlanting(state, c.pos, placed.name)
    return `planted ${placed.name} at ${c.pos.x},${c.pos.y},${c.pos.z}`
  },
  async till (bot, state, c, signal) {
    const hoe = bot.inventory.items().find((i) => i.name.endsWith('_hoe'))
    if (!hoe) throw new Error('no hoe')
    await goNear(bot, c.pos, 3, signal)
    // 上の草花は先にどける（耕すには上が空いている必要がある）
    const plant = bot.blockAt(c.pos.offset(0, 1, 0))
    if (plant && CLEARABLE.test(plant.name)) await bot.dig(plant)
    const block = bot.blockAt(c.pos)
    const above = bot.blockAt(c.pos.offset(0, 1, 0))
    if (!/^(dirt|grass_block|dirt_path)$/.test(block?.name ?? '') || above?.name !== 'air') {
      // 何が邪魔かを言う（土なのに耕せない理由は上のブロック。「土」とだけ言うと、なぜか分からない）
      const why = !/^(dirt|grass_block|dirt_path)$/.test(block?.name ?? '') ? `the ground is ${block?.name ?? 'unloaded'}` : `${above?.name ?? 'unloaded'} is on top of the ${block.name}`
      throw new Error(`nothing to till at ${c.pos.x},${c.pos.y},${c.pos.z} any more (${why})`)
    }
    await bot.equip(hoe, 'hand')
    await bot.activateBlock(block, { x: 0, y: 1, z: 0 })
    await bot.waitForTicks(4)
    if (bot.blockAt(c.pos)?.name !== 'farmland') throw new Error(`the ground at ${c.pos.x},${c.pos.y},${c.pos.z} did not turn into farmland`)
    return `tilled farmland at ${c.pos.x},${c.pos.y},${c.pos.z}`
  },
  async sow (bot, state, c, signal) {
    const seed = bot.inventory.items().find((i) => i.name === c.item)
    if (!seed) throw new Error(`no ${c.item}`)
    await goNear(bot, c.pos, 3, signal)
    const farmland = bot.blockAt(c.pos)
    if (farmland?.name !== 'farmland' || bot.blockAt(c.pos.offset(0, 1, 0))?.name !== 'air') throw new Error(`no free farmland at ${c.pos.x},${c.pos.y},${c.pos.z} any more`)
    await bot.equip(seed, 'hand')
    await bot.activateBlock(farmland, { x: 0, y: 1, z: 0 })
    await bot.waitForTicks(4)
    const crop = bot.blockAt(c.pos.offset(0, 1, 0))
    if (crop?.name === 'air') throw new Error(`the ${c.item} was not sown`)
    return `sowed ${c.item} (${crop.name}) at ${c.pos.x},${c.pos.y + 1},${c.pos.z}`
  },
  // 畑の水のマス（設計書 47 §17）。c.pos: 水を置く地面のブロック。それを掘って穴にし、隣のマスに立って、穴の底に
  // 水のバケツを使う。遠くから穴の底を狙うと、サーバーの視線は隣の地面の上面に当たり、水が畑の表面に流れる
  // （隣に立てば、視線は縁を越えて穴の底に届く）。穴の横の地面が水の高さ以上でないと水が流れ出すので、先に確かめる
  async place_water (bot, state, c, signal) {
    const bucket = bot.inventory.items().find((i) => i.name === 'water_bucket')
    if (!bucket) throw new Error('no water_bucket')
    const at = `${c.pos.x},${c.pos.y},${c.pos.z}`
    const solid = (p) => bot.blockAt(p)?.boundingBox === 'block'
    const around = [[1, 0], [-1, 0], [0, 1], [0, -1]].map(([dx, dz]) => c.pos.offset(dx, 0, dz))
    // 穴の横の地面が水の高さ以上か（先に確かめる。ここが欠けていると水は畑に流れ出す）
    for (const p of around) {
      if (!solid(p)) throw new Error(`the ground beside the hole at ${at} (${p.x},${p.y},${p.z}) is lower: the water would run out of the hole`)
    }
    // 立つ隣のマス: 上が空いている（作物は通れる）、近い順。穴そのものには立たない
    const me = bot.entity.position
    const stand = around
      .filter((p) => bot.blockAt(p.offset(0, 1, 0))?.boundingBox === 'empty' && bot.blockAt(p.offset(0, 2, 0))?.boundingBox === 'empty')
      .sort((a, b) => a.offset(0.5, 1, 0.5).distanceTo(me) - b.offset(0.5, 1, 0.5).distanceTo(me))[0]
    if (!stand) throw new Error(`no free cell to stand on beside the water cell ${at}`)
    await goto(bot, new goals.GoalBlock(stand.x, stand.y + 1, stand.z), signal)
    signal.throwIfAborted()
    const ground = bot.blockAt(c.pos)
    if (!ground) throw new Error(`${at} is not loaded`)
    if (ground.name === 'water') return `there is already water at ${at}`
    const plant = bot.blockAt(c.pos.offset(0, 1, 0))
    if (plant && CLEARABLE.test(plant.name)) await bot.dig(plant)
    if (ground.boundingBox === 'block') await bot.dig(ground)
    if (bot.blockAt(c.pos)?.boundingBox === 'block') throw new Error(`could not dig the hole at ${at}`)
    if (!solid(c.pos.offset(0, -1, 0))) throw new Error(`no solid floor under the hole at ${at}: the water would drain away`)
    await bot.equip(bucket, 'hand')
    await bot.lookAt(c.pos.offset(0.5, 0.05, 0.5), true)
    const cursor = bot.blockAtCursor?.(5)
    console.log(`[water] ${at} に水を置く: 立つ位置 ${bot.entity.position.x.toFixed(1)},${bot.entity.position.y.toFixed(1)},${bot.entity.position.z.toFixed(1)}、視線の先 ${cursor ? `${cursor.name} ${cursor.position.x},${cursor.position.y},${cursor.position.z}` : 'なし'}`)
    bot.activateItem()
    await bot.waitForTicks(8)
    const now = bot.blockAt(c.pos)?.name
    // 水が畑の表面に出ていないか（出ていたら、バケツは空で、水が流れている）。穴の外の水源は、汲み戻して流れを止める
    const spilled = around.map((p) => p.offset(0, 1, 0)).filter((p) => bot.blockAt(p)?.name === 'water')
    const strays = straySources(bot, c.pos)
    if (spilled.length || strays.length) {
      let back = ''
      try {
        const { scooped, left } = await scoopBack(bot, c.pos, (p) => goNear(bot, p, 3, signal))
        back = scooped ? `; scooped ${scooped} stray source back${left ? `, ${left} more left running` : ''}` : `; could not scoop it back${left ? ` (${left} stray source${left === 1 ? '' : 's'} left running)` : ''}`
      } catch (e) {
        if (signal.aborted) throw e
        back = `; could not scoop it back (${e.message})`
      }
      const where = [...spilled, ...strays].map((p) => `${p.x},${p.y},${p.z}`)
      const emptyNow = bot.inventory.items().some((i) => i.name === 'bucket')
      throw new Error(`the water ran onto the farm at ${[...new Set(where)].join(' ')} instead of staying in the hole at ${at} (${emptyNow ? 'the bucket is empty now' : 'the water_bucket is full again'}${back})`)
    }
    const empty = bot.inventory.items().some((i) => i.name === 'bucket')
    if (now !== 'water') throw new Error(`the water was not placed at ${at} (${now ?? 'unloaded'} there; ${empty ? 'the bucket is empty now' : 'the water_bucket is still full'})`)
    return `placed water at ${at} (the water_bucket is empty again: bucket)`
  },
  // 道具 ride_boat（設計書 37）: c.pos に向かって水の上をボートで進む（短い水でも）。岸に着いたら降りて拾う
  async ride_boat (bot, state, c, signal) {
    if (!riding(bot) && !boatItem(bot)) throw new Error('no boat (craft one from 5 planks of one wood at a crafting table)')
    const crossed = await crossWater(bot, state, c.pos, c.target, signal, RIDE_MIN_WATER)
    if (!crossed) throw new Error(`no water to cross toward ${c.target} within ${BOARD_WITHIN_M}m (the way there is land)`)
    return crossed
  },
  // 道具 use_item（設計書 37）: 手に持って使う（右クリック）。c.entityId のモブに、c.pos のブロックに
  // （固いブロックはその面に、水や空気はそこを見て）、どちらもなければ今向いている方へ。c.hold 秒押し続ける
  async use_item (bot, state, c, signal) {
    const bare = !c.item // 素手で押す（行き詰まったときの実験。設計書 85）
    const item = bare ? null : bot.inventory.items().find((i) => i.name === c.item)
    if (!bare && !item) throw new Error(`no ${c.item}`)
    const what = bare ? 'an empty hand' : c.item
    const before = { items: inventoryCounts(bot), damage: item?.durabilityUsed ?? 0 }
    const drops = new Set(nearbyEntities(bot).filter(({ e }) => e.name === 'item').map(({ e }) => e.id))
    const hand = async () => {
      if (!bare) await bot.equip(item, 'hand')
      else if (bot.heldItem) await bot.unequip('hand')
    }
    let on = 'in the air'
    let blockBefore = null
    let propsBefore = null
    // 押し方を指定したときだけスニークを触る（指定がなければ、そのまま）
    const touchSneak = c.sneak !== undefined
    if (touchSneak) bot.setControlState('sneak', !!c.sneak)
    try {
      if (c.entityId != null) {
        const e = bot.entities[c.entityId]
        if (!e) throw new Error(`the ${c.target} is gone`)
        if (e.position.distanceTo(bot.entity.position) > 3) await goNear(bot, e.position, 2, signal)
        await hand()
        await bot.lookAt(e.position.offset(0, (e.height ?? 1) / 2, 0), true)
        bot.useOn(e)
        on = `on the ${e.name}`
      } else if (c.pos) {
        if (!withinReach(bot, c.pos)) await goNear(bot, c.pos, 3, signal)
        signal.throwIfAborted()
        const block = bot.blockAt(c.pos)
        if (!block) throw new Error(`${c.pos.x},${c.pos.y},${c.pos.z} is not loaded`)
        blockBefore = block.name
        propsBefore = block.getProperties?.() ?? null
        await hand()
        if (block.boundingBox === 'block' || bare) {
          const face = c.face ? new Vec3(...FACE_VECTORS[c.face]) : faceToward(bot, c.pos)
          // 押す点は、その面の真ん中
          const cursor = new Vec3(0.5 + face.x * 0.5, 0.5 + face.y * 0.5, 0.5 + face.z * 0.5)
          await bot.lookAt(c.pos.offset(cursor.x, cursor.y, cursor.z), true)
          await bot.activateBlock(block, face, cursor)
        } else {
          await bot.lookAt(c.pos.offset(0.5, 0.5, 0.5), true)
          bot.activateItem()
        }
        on = `at ${block.name} ${c.pos.x},${c.pos.y},${c.pos.z}`
      } else {
        await hand()
        bot.activateItem()
      }
      if (c.hold) {
        await bot.waitForTicks(Math.round(c.hold * 20))
        bot.deactivateItem()
      }
      await bot.waitForTicks(6)
    } finally {
      if (touchSneak) bot.setControlState('sneak', false)
    }
    const changes = []
    const after = inventoryCounts(bot)
    for (const name of new Set([...Object.keys(before.items), ...Object.keys(after)])) {
      const d = (after[name] ?? 0) - (before.items[name] ?? 0)
      if (d) changes.push(`${d > 0 ? '+' : ''}${d} ${name}`)
    }
    const held = bare ? null : bot.inventory.items().find((i) => i.name === c.item)
    if (held && (held.durabilityUsed ?? 0) !== before.damage) changes.push(`the ${c.item} wore a little`)
    if (c.pos) {
      const nowBlock = bot.blockAt(c.pos)
      if (nowBlock && nowBlock.name !== blockBefore) changes.push(`${blockBefore} became ${nowBlock.name}`)
      // ドアの開閉などブロックの状態の変わりも変化として数える
      const propsNow = nowBlock?.getProperties?.() ?? null
      if (propsBefore && propsNow && nowBlock.name === blockBefore) {
        for (const k of Object.keys(propsNow)) if (String(propsNow[k]) !== String(propsBefore[k])) changes.push(`${k}: ${propsBefore[k]} -> ${propsNow[k]}`)
      }
    }
    const dropped = nearbyEntities(bot).filter(({ e }) => e.name === 'item' && !drops.has(e.id))
    if (dropped.length) changes.push(`${dropped.length} item(s) dropped nearby (pickup)`)
    if (!changes.length) throw new Error(`nothing changed after using ${what} ${on}`)
    // 空のバケツは、水（溶岩・牛乳）が入ったバケツが増えて初めて成功: 無関係な持ち物の変化（積んだ土を拾い直した、落とし物）で
    // 成功にしない（2026-10-07: 水に届かないまま「+6 dirt」で成功と返り、同じ手を 3 周した）
    if (c.item === 'bucket' && !Object.keys(after).some((n) => n !== 'bucket' && n.endsWith('_bucket') && (after[n] ?? 0) > (before.items[n] ?? 0))) {
      throw new Error(`the bucket was not filled (${changes.join(', ')}): stand within reach of the water source and aim at it`)
    }
    return `used ${what} ${on}: ${changes.join(', ')}`
  },
  async explore (bot, state, c, signal) {
    const start = bot.entity.position.clone()
    const go = c.go ?? EXPLORE_DISTANCE
    let target = start.offset(c.dx * go, 0, c.dz * go)
    // まだ見ていない土地へ、着くまで区間（LEG m）ごとに進む。長い水はボートで（近くても）
    await legToward(bot, state, target, `the unexplored land ${c.target}`, signal)
    // 目標が水の上で、ボートが岸まで先へ漕いだなら、最後に戻って歩かない（岸が目標）
    const gone = ((bot.entity.position.x - start.x) * c.dx + (bot.entity.position.z - start.z) * c.dz) / (c.dx * c.dx + c.dz * c.dz)
    if (gone > go) target = start.offset(c.dx * gone, 0, c.dz * gone)
    let error = ''
    try {
      await goto(bot, new goals.GoalNearXZ(target.x, target.z, 3), signal)
    } catch (e) {
      error = e.message
    }
    const moved = bot.entity.position.xzDistanceTo(start)
    if (moved < EXPLORE_MIN_PROGRESS) throw new Error(`could not walk ${c.target} from ${round(start.x)},${round(start.z)}${error ? `: ${error}` : ''}`)
    return `walked ${round(moved)}m ${c.target}`
  }
}

// c.pos のチェストを開けてそのウィンドウで `use` を実行し、そのあとの中身を記録する
async function withOpenChest (bot, state, c, signal, use) {
  await legToward(bot, state, c.pos, 'the chest', signal)
  if (state.home && isInside({ entity: { position: c.pos } }, state.home)) await enterHome(bot, state.home, signal)
  await goNear(bot, c.pos, 2, signal)
  const block = bot.blockAt(c.pos)
  if (block?.name !== 'chest') {
    forgetChest(state.memory, c.pos)
    throw new Error(`no chest at ${c.pos.x},${c.pos.y},${c.pos.z} any more`)
  }
  const window = await bot.openContainer(block)
  try {
    return await use(window)
  } finally {
    const contents = {}
    const items = window.containerItems()
    for (const i of items) contents[i.name] = (contents[i.name] ?? 0) + i.count
    // ラージチェスト（隣り合う 2 つ）は 1 つの窓で 54 スロット: 左の半分に覚え、右の半分の記録は消す
    // （両方に覚えると、中身が 2 倍に数えられる）
    const at = chestRecordPos(bot, block)
    if (!at.same) forgetChest(state.memory, c.pos)
    rememberChest(state.memory, at.pos, contents, Number(bot.time.age), { used: items.length, size: window.inventoryStart ?? SINGLE_CHEST })
    window.close()
  }
}

// items（{ item, count }）を、c.pos のチェストから順に、家のチェストへ入れる。いっぱいなら次のチェストへ。
// 入れた分の文（"3 dirt"）の配列を返す
async function putInChests (bot, state, c, signal, items) {
  const todo = new Map(items.map(({ item, count }) => [item, count]))
  const first = c.pos
  const others = homeChests(state.memory, state.home).map((r) => new Vec3(r.x, r.y, r.z))
    .filter((p) => !p.equals(first)).sort((a, b) => a.distanceTo(first) - b.distanceTo(first))
  const put = []
  for (const pos of [first, ...others]) {
    if (![...todo.values()].some((n) => n > 0)) break
    signal.throwIfAborted()
    await withOpenChest(bot, state, { pos }, signal, async (window) => {
      for (const [item, want] of todo) {
        const n = Math.min(want, inventoryCounts(bot)[item] ?? 0)
        if (n <= 0) { todo.set(item, 0); continue }
        const before = inventoryCounts(bot)[item] ?? 0
        try {
          await window.deposit(bot.registry.itemsByName[item].id, null, n)
        } catch {
          // このチェストには入らない（いっぱい）: 入った分だけ数えて、次のチェストへ
        }
        const moved = before - (inventoryCounts(bot)[item] ?? 0)
        if (moved > 0) { put.push(`${moved} ${item}`); todo.set(item, want - moved) }
      }
    })
  }
  return put
}

// 記録するチェストの位置: ラージチェストなら左の半分（もう片方の位置の記録は消す）、そうでなければそのチェスト
function chestRecordPos (bot, block) {
  const type = block.getProperties?.().type
  if (type !== 'left' && type !== 'right') return { pos: block.position, same: true }
  const facing = block.getProperties().facing
  const other = [[1, 0], [-1, 0], [0, 1], [0, -1]].map(([dx, dz]) => bot.blockAt(block.position.offset(dx, 0, dz)))
    .find((b) => b?.name === 'chest' && b.getProperties?.().facing === facing && b.getProperties().type !== type)
  if (!other) return { pos: block.position, same: true }
  const left = type === 'left' ? block : other
  return { pos: left.position, same: left === block }
}

// 目の高さからブロックの中心までが届く距離か
const withinReach = (bot, p) => bot.entity.position.offset(0, 1.62, 0).distanceTo(p.offset(0.5, 0.5, 0.5)) <= REACH

const SWEEP_MAX = 12 // 1 回の行動で続けて刈る数
const SWEEP_RADIUS = 5
const SWEEP_KINDS = (name) => /grass|fern/.test(name) ? ['short_grass', 'tall_grass', 'fern', 'large_fern'] : [name]

// まわりの同じ種類（草なら草とシダ）を、何か落ちるまで（か SWEEP_MAX まで）刈る
async function sweepNearby (bot, state, name, before, signal) {
  const ids = SWEEP_KINDS(name).map((n) => bot.registry.blocksByName[n]?.id).filter((id) => id != null)
  for (let i = 0; i < SWEEP_MAX; i++) {
    signal.throwIfAborted()
    if (gainedSince(bot, before) > 0 || nearbyDrops(bot, state, SWEEP_RADIUS + 2).length) return
    const next = bot.findBlock({ matching: ids, maxDistance: SWEEP_RADIUS, useExtraInfo: (b) => !inHouse(state, b.position) })
    if (!next) return
    try {
      if (!withinReach(bot, next.position)) await goto(bot, new goals.GoalNear(next.position.x, next.position.y, next.position.z, 2), signal)
      await bot.dig(bot.blockAt(next.position), true)
    } catch (e) {
      signal.throwIfAborted()
      return
    }
  }
}

// まわり（SWEEP_RADIUS）の熟した同じ作物を収穫する。収穫した場所を返す（植え直す所）
async function harvestNearby (bot, state, name, signal) {
  const id = bot.registry.blocksByName[name]?.id
  const done = []
  for (let i = 0; i < SWEEP_MAX; i++) {
    signal.throwIfAborted()
    const next = bot.findBlock({ matching: id, maxDistance: SWEEP_RADIUS, useExtraInfo: (b) => isRipe(b) && !inHouse(state, b.position) })
    if (!next) break
    try {
      if (!withinReach(bot, next.position)) await goto(bot, new goals.GoalNear(next.position.x, next.position.y, next.position.z, 2), signal)
      await bot.dig(bot.blockAt(next.position), true)
      done.push(next.position)
    } catch (e) {
      signal.throwIfAborted()
      break
    }
  }
  return done
}

// 収穫した耕地に種を植え直す。植えた数
async function replant (bot, positions, block) {
  const seed = CROPS[cropByBlock(block)].seed
  let n = 0
  for (const p of positions) {
    const item = bot.inventory.items().find((i) => i.name === seed)
    if (!item) break
    const farmland = bot.blockAt(p.offset(0, -1, 0))
    if (farmland?.name !== 'farmland' || bot.blockAt(p)?.name !== 'air') continue
    try {
      if (!withinReach(bot, p)) await goto(bot, new goals.GoalNear(p.x, p.y, p.z, 2), new AbortController().signal)
      await bot.equip(item, 'hand')
      await bot.placeBlock(farmland, new Vec3(0, 1, 0))
      if (bot.blockAt(p)?.name === block) n++
    } catch {}
  }
  return n
}

// ベッドに寝る。誰かが寝ていて断られたら、誰か・どうすればいいかを添える（「the bed is occupied」だけでは、
// 同じ手を 4 回繰り返した。2026-09-30）
async function sleepOn (bot, bed) {
  try {
    await bot.sleep(bed)
  } catch (e) {
    if (!/occupied/.test(e.message)) throw e
    const s = bed?.position && sleeperOf(bot, bed.position)
    throw new Error(s
      ? `the bed is occupied: a ${s.e.name} #${s.e.id} is sleeping in it. Hit it once to wake it (the option "wake up the sleeping ${s.e.name}") or sleep somewhere else`
      : 'the bed is occupied (someone is in it, out of sight): sleep somewhere else')
  }
}

// 家のでないベッドで眠り、夜が明けるのを待つ
async function sleepIn (bot, bed, signal) {
  await sleepOn(bot, bed)
  while (bot.isSleeping && !signal.aborted) await bot.waitForTicks(10)
  if (bot.isSleeping) await bot.wake()
  await once(bot, 'time', { signal: AbortSignal.timeout(TIME_UPDATE_TIMEOUT_MS) })
  const tod = bot.time.timeOfDay
  if (tod >= SLEEP_FROM && tod <= SLEEP_UNTIL) throw new Error(`woke up but the night was not skipped (time ${tod})`)
  return `slept through the night in the bed at ${bed.position.x},${bed.position.y},${bed.position.z}; time ${tod}`
}

// 持っているベッドを置ける、足元のすぐ横の 2 マス（床があり、空いていて、家・建物でない）
function bedSpotHere (bot, state) {
  const me = bot.entity.position.floored()
  for (const [dx, dz] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
    const foot = me.offset(dx, 0, dz)
    const head = foot.offset(dx, 0, dz)
    const free = (p) => bot.blockAt(p)?.name === 'air' && bot.blockAt(p.offset(0, -1, 0))?.boundingBox === 'block' && !inHouse(state, p, 1)
    if (free(foot) && free(head)) return { foot, head }
  }
  return null
}

// 自分でダメージに対処するプリミティブ: ダメージを受けても中断しない
export const DAMAGE_TOLERANT = new Set(['attack', 'flee', 'reach_land'])

// 名前付きの建物の次のブロック（空けるマスなら掘る）。家の増築ができたら、家の室内を広げる
async function placeBuildBlock (bot, state, name, signal) {
  const plan = state.builds?.[name]
  if (!plan) throw new Error(`there is no build named ${name}`)
  const b = plan.pending(bot)[0]
  if (!b) return `the build ${name} is complete`
  await placeOne(bot, plan, b, signal, (pos, block) => buildAllowsDig(state, pos, block))
  const s = plan.status(bot)
  if (s.complete && plan.design?.anchor?.startsWith('home:') && state.home && growHome(bot, state.home)) {
    return `${b.block === 'air' ? 'cleared' : `placed ${b.block}`}; the build ${name} is complete and the home now includes it`
  }
  return `${b.block === 'air' ? 'cleared a block' : `placed ${b.block}`} (${s.placed}/${s.total}) for ${name}`
}
