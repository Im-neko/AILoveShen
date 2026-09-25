// プリミティブの行動と、それらが共有する移動の規則。
//
// 各プリミティブは具体的な対象（ブロック、エンティティ、アイテム）に対して有界な1ステップを実行し、
// ワールドで確かめた結果を返す。失敗は投げ、成功として返すことはない。
// 実行関数は AbortSignal（タイムアウト、またはダメージを受けた）を受け取り、発火したらすぐ止まる:
// ループがそれを確かめ、呼び出し側が経路移動と採掘を取り消す。

import { once } from 'node:events'
import pathfinderPkg from 'mineflayer-pathfinder'
import { THREAT_RADIUS, round, inventoryCounts, nearbyEntities, threats, isHostile } from './observe.mjs'
import { findSite, placeOne } from './build.mjs'
import { craftWithRecipeBook } from './craft.mjs'
import { shelteredFrom, enterHome, isDoorOpen, bedSpot, chestSpot, inHouse, isInside, digExit, stepOut, repairWall } from './home.mjs'
import { rememberChest, forgetChest, rememberFurnace, forgetFurnace, rememberSite } from './memory.mjs'
import { smeltingProduct } from './knowledge.mjs'
import { surveySite, SURVEY_REACH } from './survey.mjs'
import { walkTo as goto } from './move.mjs'
import { digTargets } from './world.mjs'

const { Movements, goals } = pathfinderPkg
export const REACH = 4.5 // サバイバルで目からブロックに届く距離
// 体力がここまで下がったら危険: 欲求として示し、戦いや逃走はここで止める
export const HEALTH_CRITICAL = 8
// 7 未満でダッシュできなくなる。ここから先は飢えているので、少し害のあるものでも食べる価値がある
export const HUNGER_URGENT = 6
export const TABLE_SEARCH_RADIUS = 32
const HOSTILE_AVOID_RADIUS = 5
const HOSTILE_STEP_COST = 20
const HOSTILE_CACHE_MS = 1000
const LEAVES_STEP_COST = 10
const FLEE_DISTANCE = 16
const FIGHT_TIMEOUT_MS = 10000
const FLEE_TIMEOUT_MS = 8000
const PICKUP_WAIT_MS = 1500
const DROP_COLLECT_RADIUS = 8
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
export function configureMovements (bot, state) {
  const m = new Movements(bot)
  // まだ読み込まれていないチャンクでは、pathfinder は位置のない代わりのブロックを渡す（歩けることは
  // ない）。その位置を読むコスト関数は物理ティックの中で例外を投げてしまう。
  const step = (cost) => m.exclusionAreasStep.push((block) => block.position ? cost(block) : 0)
  // 建築中の建物の上は歩かない: 途中の壁が段差になってボットが登り、壁の上から屋根を置いたり、
  // 上で動けなくなったりする。
  step((block) => {
    const o = state.plan?.origin
    if (!o) return 0
    const p = block.position
    const inside = p.x >= o.x && p.x < o.x + state.plan.size.width && p.z >= o.z && p.z < o.z + state.plan.size.depth
    return inside && p.y > o.y + 1 ? 101 : 0
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
  // 歩きながら壊してよいのは葉だけ（プレイヤーが樹冠をかき分けるように）。家や地形は壊さない。
  m.exclusionAreasBreak.push((block) => isLeaves(block.name) ? 0 : 100)
  m.scafoldingBlocks = [] // 足場を積むのに建材を使わない
  m.allow1by1towers = false
  bot.pathfinder.setMovements(m)
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

// 作業台やかまどを置く場所: 完全なブロックの上の空気で、ボットから 2〜3 ブロック（立っている場所では
// ない）、上下1ブロックまで（自分の高さの決まった輪だけでは、坂や洞窟で何も見つからなかった）。
// 家の中や計画中の敷地には置かない。近い順
export function stationSpot (bot, state) {
  const me = bot.entity.position.floored()
  const spots = []
  for (let dx = -3; dx <= 3; dx++) {
    for (let dz = -3; dz <= 3; dz++) {
      if (Math.max(Math.abs(dx), Math.abs(dz)) < 2) continue
      for (const dy of [0, -1, 1]) {
        const pos = me.offset(dx, dy, dz)
        const spot = bot.blockAt(pos)
        const ground = bot.blockAt(pos.offset(0, -1, 0))
        if (spot?.name !== 'air' || ground?.boundingBox !== 'block' || inHouse(state, pos, 1)) continue
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

// 長い道のりは、探索と同じく1ステップに1区間ずつ歩く: 各行動を短くして、タイムアウトに十分
// 収まるように終え、次のステップでワールドを見直す（270m 先の家まで 45 秒以上かかった）
export const LEG = 48
const SMELT_WAIT_MS = 20000 // 1個 10 秒かかる: スタック全部を待つとタイムアウトを過ぎる
async function legToward (bot, target, what, signal) {
  const me = bot.entity.position
  const far = Math.hypot(target.x - me.x, target.z - me.z)
  if (far <= LEG) return null
  const k = LEG / far
  await goto(bot, new goals.GoalNearXZ(me.x + (target.x - me.x) * k, me.z + (target.z - me.z) * k, 3), signal)
  const left = Math.hypot(target.x - bot.entity.position.x, target.z - bot.entity.position.z)
  return `walked ${round(far - left)}m toward ${what} (${round(left)}m left)`
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

export async function fight (bot, target, signal) {
  const start = Date.now()
  try {
    while (bot.entities[target.id] && Date.now() - start < FIGHT_TIMEOUT_MS && !signal.aborted) {
      if (target.position.distanceTo(bot.entity.position) > 3) {
        bot.pathfinder.setGoal(new goals.GoalFollow(target, 2), true)
      } else {
        await bot.lookAt(target.position.offset(0, target.height * 0.8, 0), true)
        bot.attack(target)
      }
      await bot.waitForTicks(12)
    }
  } finally {
    bot.pathfinder.setGoal(null)
  }
  return bot.entities[target.id] ? `${target.name} still alive` : `${target.name} gone (killed or despawned)`
}

export async function flee (bot, h, signal) {
  bot.pathfinder.setGoal(new GoalAwayFrom(h, FLEE_DISTANCE + REPLAN_MOVE), true)
  const start = Date.now()
  try {
    while (Date.now() - start < FLEE_TIMEOUT_MS && !signal.aborted && bot.entities[h.id] && h.position.distanceTo(bot.entity.position) < FLEE_DISTANCE) {
      await bot.waitForTicks(5)
    }
  } finally {
    bot.pathfinder.setGoal(null)
  }
  return `now ${round(h.position.distanceTo(bot.entity.position))}m from ${h.name}`
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

export function nearbyDrops (bot, state, radius) {
  return nearbyEntities(bot).filter(({ e, dist }) => e.name === 'item' && dist <= radius &&
    Math.abs(e.position.y - bot.entity.position.y) < 4 && !state.unreachableDrops.has(e.id))
}

// ドロップは少し待たないと拾えない。すでにその上に立っていると、歩くのはすぐ終わる。
// 近くに落ちたドロップを集め、それぞれ拾うのを待つ。
// まわりのドロップを拾う。最後のドロップに届かなかったら、その理由を返す
async function collectNearbyDrops (bot, state, signal) {
  let why = ''
  for (let i = 0; i < 3; i++) {
    await bot.waitForTicks(10)
    signal.throwIfAborted()
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

export function findTable (bot) {
  return bot.findBlock({ matching: bot.registry.blocksByName.crafting_table.id, maxDistance: TABLE_SEARCH_RADIUS })
}

export function findFurnace (bot) {
  return bot.findBlock({ matching: bot.registry.blocksByName.furnace.id, maxDistance: TABLE_SEARCH_RADIUS })
}

// 経路が届かなかったブロックは、このセッションでは再び出さない（town2: 何度も選ばれた）
const blockKey = (p) => `${p.x},${p.y},${p.z}`
const markUnreachable = (state, pos) => state.unreachableBlocks?.add(blockKey(pos))
export const isUnreachable = (state, pos) => !!state.unreachableBlocks?.has(blockKey(pos))

// そのブロックに一番速い道具を持っていれば装備する
async function equipToolFor (bot, block) {
  const tool = bot.pathfinder.bestHarvestTool(block)
  if (tool) await bot.equip(tool, 'hand')
}

// 階段掘り（docs/design/17_dig_down.md）: 1 マス幅・2 マスの高さで、前に 1、下に 1 ずつ下りる。
// 真下には掘らない（溶岩に落ちる、出られない穴になる）。階段なら歩いて戻れる
const STAIR_STEPS = 6 // 1 回の行動で下りる段数（タイムアウトの 45 秒に収まる）
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

// 実行関数: (bot, state, candidate, signal) -> 結果の文字列。対象は候補が持っている
export const PRIMITIVES = {
  async dig (bot, state, c, signal) {
    const block = bot.blockAt(c.pos)
    if (!block || block.name !== c.block) throw new Error(`${c.block} is gone from ${c.pos}`)
    const before = totalItems(bot)
    // 近くではなく、ブロックに届く場所に立つ: そうしないと、頭より高い原木には木の葉の上からしか
    // 「近く」にならない。
    try {
      await goto(bot, new goals.GoalLookAtBlock(c.pos, bot.world, { reach: REACH }), signal)
    } catch (e) {
      if (!signal.aborted) markUnreachable(state, c.pos)
      throw e
    }
    signal.throwIfAborted()
    // 空中で掘ると5倍遅い（例えば、立っていたブロックを切った直後）
    for (let i = 0; i < 40 && !bot.entity.onGround; i++) await bot.waitForTicks(1)
    await equipToolFor(bot, block)
    await bot.dig(bot.blockAt(c.pos), true)
    signal.throwIfAborted()
    const why = await collectNearbyDrops(bot, state, signal)
    const gained = totalItems(bot) - before
    if (gained <= 0) {
      const full = bot.inventory.emptySlotCount() === 0 ? '; the inventory is full' : ''
      throw new Error(`dug ${c.block} but picked nothing up (${dropsAround(bot)}${why ? `; path: ${why}` : ''}${full})`)
    }
    return `dug ${c.block}, picked up ${gained} items`
  },
  async pickup (bot, state, c, signal) {
    const drop = bot.entities[c.entityId]
    if (!drop) throw new Error('the item is gone')
    const before = totalItems(bot)
    const collected = waitForCollect(bot, PICKUP_WAIT_MS)
    try {
      await goNear(bot, drop.position, 0.5, signal)
    } catch (e) {
      state.unreachableDrops.add(c.entityId)
      throw e
    }
    await collected
    const gained = totalItems(bot) - before
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
  async flee (bot, state, c, signal) {
    const h = bot.entities[c.entityId]
    if (!h) return `${c.target} is gone`
    return await flee(bot, h, signal)
  },
  async eat (bot, state, c, signal) {
    const food = bot.inventory.items().find((i) => i.name === c.item)
    if (!food) throw new Error(`no ${c.item}`)
    const before = bot.food
    await bot.equip(food, 'hand')
    await bot.consume()
    return `ate ${c.item}, hunger ${before} -> ${bot.food}`
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
      table = findTable(bot)
      if (!table) throw new Error('no crafting table nearby')
      await goNear(bot, table.position, 3, signal)
    }
    const before = itemCount(bot, c.item)
    for (let i = 0; i < c.times; i++) {
      signal.throwIfAborted()
      await craftWithRecipeBook(bot, state.recipeBook, c.item, { table })
    }
    return `crafted ${itemCount(bot, c.item) - before} ${c.item}`
  },
  // ボットのそばに作業台かかまどを置く
  async place_station (bot, state, c, signal) {
    const item = bot.inventory.items().find((i) => i.name === c.item)
    if (!item) throw new Error(`no ${c.item}`)
    const pos = stationSpot(bot, state)
    if (!pos) throw new Error(`no free spot for the ${c.item}`)
    await bot.equip(item, 'hand')
    await bot.placeBlock(bot.blockAt(pos.offset(0, -1, 0)), { x: 0, y: 1, z: 0 })
    return `placed ${c.item} at ${pos}`
  },
  async place_plan (bot, state, c, signal) {
    const plan = state.plan
    if (!plan.origin) {
      // 選んだ場所に建てる計画なら、まずそこまで歩く
      if (plan.site) {
        const leg = await legToward(bot, plan.site, 'the site of the house', signal)
        if (leg) return leg
        await goto(bot, new goals.GoalNearXZ(plan.site.x, plan.site.z, 4), signal)
      }
      const origin = findSite(bot, plan.size)
      if (!origin) {
        plan.siteSearchFailedAt = bot.entity.position.clone()
        throw new Error(`no flat ${plan.size.width}x${plan.size.depth} site nearby; explore elsewhere`)
      }
      plan.origin = origin
    }
    const b = plan.pending(bot)[0]
    if (!b) return 'the plan is complete'
    await placeOne(bot, plan, b, signal)
    const s = plan.status(bot)
    return `placed ${b.block} (${s.placed}/${s.total})`
  },
  async place_bed (bot, state, c, signal) {
    await enterHome(bot, state.home, signal)
    const spot = bedSpot(bot, state.home)
    if (!spot) throw new Error('no free spot for the bed in the house')
    const { inside } = state.home
    await goto(bot, new goals.GoalBlock(inside.x, inside.y, inside.z), signal)
    // ベッドの頭側は、プレイヤーが向いている方向に1ブロック先になる。向きは次の移動パケットで
    // やっとサーバーに届く: すぐに置くと、サーバーはドアを閉めたときの向きを使い、ベッドの頭側が
    // ドアの内側のセルをふさいだ。
    const bed = bot.inventory.items().find((i) => i.name.endsWith('_bed'))
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
    await bot.sleep(bot.blockAt(state.home.bed))
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
    const leg = await legToward(bot, state.home.outside, 'home', signal)
    if (leg) return leg
    await enterHome(bot, state.home, signal)
    return 'inside the house with the door closed'
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
    return useChest(bot, state, c, signal, async (window) => {
      const n = Math.min(c.count, inventoryCounts(bot)[c.item] ?? 0)
      if (!n) throw new Error(`no ${c.item} to put in`)
      await window.deposit(bot.registry.itemsByName[c.item].id, null, n)
      return `put ${n} ${c.item} in the chest`
    })
  },
  async withdraw (bot, state, c, signal) {
    return useChest(bot, state, c, signal, async (window) => {
      const inside = window.containerItems().filter((i) => i.name === c.item).reduce((s, i) => s + i.count, 0)
      const n = Math.min(c.count, inside)
      if (!n) throw new Error(`no ${c.item} in the chest (the record was wrong)`)
      await window.withdraw(bot.registry.itemsByName[c.item].id, null, n)
      return `took ${n} ${c.item} from the chest`
    })
  },
  // できたものを取り出し、材料と燃料を入れ、しばらく待ってできたものを取る。
  // 残りは後のステップで取り出す（その間もかまどは動く）
  async smelt (bot, state, c, signal) {
    const leg = await legToward(bot, c.pos, 'the furnace', signal)
    if (leg) return leg
    await goNear(bot, c.pos, 2, signal) // 遠いブロックは null になる: 着いてから判定する
    const block = bot.blockAt(c.pos)
    if (block?.name !== 'furnace') {
      forgetFurnace(state.memory, c.pos)
      throw new Error(`no furnace at ${c.pos.x},${c.pos.y},${c.pos.z} any more`)
    }
    const furnace = await bot.openFurnace(block)
    const got = {}
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
      const until = Date.now() + SMELT_WAIT_MS
      while (Date.now() < until && furnace.inputItem()) {
        await bot.waitForTicks(20)
        await take()
      }
      await take()
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
    return `${took ? `took ${took}` : 'nothing done yet'}${left ? `; ${left} still smelting` : ''}`
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
    const leg = await legToward(bot, c.pos, `where ${c.target} was seen`, signal)
    if (leg) return leg
    await goto(bot, new goals.GoalNearXZ(c.pos.x, c.pos.z, 3), signal)
    return `arrived where ${c.target} was seen (${c.pos.x},${c.pos.z})`
  },
  async survey (bot, state, c, signal) {
    const leg = await legToward(bot, c.pos, `the ${c.target} site`, signal)
    if (leg) return leg
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
    const surfaceY = state.goal?.surfaceY ?? Math.floor(bot.entity.position.y)
    const maxDepth = state.goal?.spec?.dig_depth ?? 0
    let steps = 0
    let stop = ''
    for (; steps < STAIR_STEPS; steps++) {
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
        await equipToolFor(bot, block)
        await bot.dig(block, true)
        signal.throwIfAborted()
      }
      const [feet] = next.step.cells.slice(-1)
      await goto(bot, new goals.GoalBlock(feet.x, feet.y, feet.z), signal)
    }
    if (!steps) {
      markUnreachable(state, c.pos)
      throw new Error(`could not dig stairs down toward ${c.target}: ${stop}`)
    }
    await collectNearbyDrops(bot, state, signal)
    const depth = surfaceY - Math.floor(bot.entity.position.y)
    return `dug ${steps} steps down toward ${c.target} (${depth} blocks below where the goal started)${stop ? `; stopped: ${stop}` : ''}`
  },
  async explore (bot, state, c, signal) {
    const start = bot.entity.position.clone()
    const target = start.offset(c.dx * EXPLORE_DISTANCE, 0, c.dz * EXPLORE_DISTANCE)
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
async function useChest (bot, state, c, signal, use) {
  const leg = await legToward(bot, c.pos, 'the chest', signal)
  if (leg) return leg
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
    for (const i of window.containerItems()) contents[i.name] = (contents[i.name] ?? 0) + i.count
    rememberChest(state.memory, c.pos, contents, Number(bot.time.age))
    window.close()
  }
}

// 自分でダメージに対処するプリミティブ: ダメージを受けても中断しない
export const DAMAGE_TOLERANT = new Set(['attack', 'flee'])

// 最長は 45 秒: Python クライアントの要求のタイムアウト（minecraft.bridge.timeout_seconds）はこれより長くする
export const TIMEOUTS_MS = { smelt: 45000, place_chest: 45000, deposit: 45000, withdraw: 45000, goto_memory: 45000, survey: 45000, dig_down: 45000, go_home: 45000, place_bed: 45000, sleep: 45000, explore: 30000, exit_wall: 30000 }
export const DEFAULT_TIMEOUT_MS = 20000

