// 建築計画: 平らな敷地を選び、ブロックを順に1つずつ置き、ワールドで確かめる。
//
// 計画は敷地の原点（地面の高さ、つまり最初の空気の層での、敷地の最小の角）からの相対位置の
// ブロックの一覧。形（壁、屋根、ドア）は Python 側で決める。このモジュールは渡されたものを
// 渡された順に置くだけ。

import pathfinderPkg from 'mineflayer-pathfinder'
import vec3Pkg from 'vec3'
import { isLog, isPlanks } from './observe.mjs'
import { walkTo } from './move.mjs'

const { goals } = pathfinderPkg
const { Vec3 } = vec3Pkg

const SITE_SEARCH_RADIUS = 24
const SITE_SEARCH_DY = 4
const SITE_RETRY_DISTANCE = 16 // 探索に失敗したら、そこからこの距離だけ離れてから探し直す
const PLACE_RANGE = 4
const PLACE_INTERVAL_MS = 200
const UNSUITABLE_GROUND = /(_leaves|_log|_planks|_door|water|lava|ice|snow$|sand$|gravel$)/

// 計画のブロックの種類 -> インベントリのアイテム名・ワールドのブロック名に対する判定
const KINDS = {
  planks: isPlanks,
  log: isLog,
  door: (name) => name.endsWith('_door'),
  cobblestone: (name) => name === 'cobblestone',
  dirt: (name) => name === 'dirt' || name === 'grass_block',
  // 空ける（名前付きの建物だけ。docs/design/25_builds.md）: 空気か、刈れる植物
  air: (name) => name === 'air' || name === 'cave_air'
}
// 名前付きの建物で、置く前に掘ってよい自然のブロック（守るものは別に確かめる）
const NATURAL = /^(dirt|grass_block|coarse_dirt|podzol|stone|granite|diorite|andesite|gravel|sand|clay|mud|tuff|deepslate|short_grass|tall_grass|fern|snow)$/

// 敷地にあってよいブロック: 空気、植物、葉（置く前に掘る）
// 仮設の作業台・かまど（建築予定地に置いてよい）は、建てる番が来たら壊して置く
const TEMPORARY_STATION = /^(crafting_table|furnace)$/
const isClearable = (block) => (block.boundingBox === 'empty' && !['water', 'lava'].includes(block.name)) || block.name.endsWith('_leaves')

export class BuildPlan {
  // design: モデルが設計したもの（名前、コンセプト、寸法、ドア）。できた家のために取っておく
  // site: 建てる場所 {x, z}（選んだ候補地）。なければボットのいる所のまわりに建てる
  // kind: 'house'（家の計画）か 'build'（名前付きの建物）
  constructor ({ blocks, width, depth, height, design = null, site = null, kind = 'house' }) {
    for (const b of blocks) {
      if (!KINDS[b.block]) throw new Error(`unknown block kind: ${b.block}`)
      if (b.block === 'air' && kind !== 'build') throw new Error('air is only for named builds')
    }
    this.kind = kind
    this.blocks = blocks
    this.size = { width, depth, height }
    this.design = design
    this.site = site && { x: Math.floor(site.x), z: Math.floor(site.z) }
    this.origin = null
    this.siteSearchFailedAt = null
  }

  // 敷地の探索に失敗した場所の近くにまだいる間は false
  canSearchSite (bot) {
    return !this.siteSearchFailedAt || this.siteSearchFailedAt.distanceTo(bot.entity.position) >= SITE_RETRY_DISTANCE
  }

  toJSON () {
    return { blocks: this.blocks, ...this.size, design: this.design, site: this.site, kind: this.kind, origin: this.origin && { x: this.origin.x, y: this.origin.y, z: this.origin.z } }
  }

  static fromJSON (data) {
    const plan = new BuildPlan(data)
    if (data.origin) plan.origin = new Vec3(data.origin.x, data.origin.y, data.origin.z)
    return plan
  }

  worldPos (b) {
    return this.origin.offset(b.x, b.y, b.z)
  }

  isPlaced (bot, b) {
    const block = bot.blockAt(this.worldPos(b))
    if (!block) return false
    if (b.block === 'air') return block.boundingBox === 'empty' && !/water|lava/.test(block.name)
    if (!KINDS[b.block](block.name)) return false
    // ドアは壁と直角に向いていないと、開けたときに板が通り道をふさぐ（town4: 西向きのドアで入れなかった）
    return b.block !== 'door' || this.doorOutward(b).facings.includes(block.getProperties().facing)
  }

  // ドアのある壁の外向き（{x, z} の単位ベクトル）と、正しい向き（壁と直角の 2 方向）
  doorOutward (b) {
    const { width, depth } = this.size
    if (this.kind === 'build') {
      // 建物のドアは、左右（x）の隣が壁なら南北を向く。外は建物の端に近い側
      const solid = (x, z) => this.blocks.some((o) => o.x === x && o.z === z && o.y === b.y && o.block !== 'air' && o.block !== 'door')
      if (solid(b.x - 1, b.z) || solid(b.x + 1, b.z)) return { x: 0, z: b.z < depth / 2 ? -1 : 1, facings: ['north', 'south'] }
      return { x: b.x < width / 2 ? -1 : 1, z: 0, facings: ['east', 'west'] }
    }
    if (b.z === 0 || b.z === depth - 1) return { x: 0, z: b.z === 0 ? -1 : 1, facings: ['north', 'south'] }
    return { x: b.x === 0 ? -1 : 1, z: 0, facings: ['east', 'west'] }
  }

  pending (bot) {
    if (!this.origin) return this.blocks
    return this.blocks.filter((b) => !this.isPlaced(bot, b))
  }

  status (bot) {
    const pending = this.pending(bot)
    return {
      origin: this.origin && { x: this.origin.x, y: this.origin.y, z: this.origin.z },
      site: this.site,
      design: this.design,
      total: this.blocks.length,
      placed: this.blocks.length - pending.length,
      complete: !!this.origin && pending.length === 0,
      missing_blocks: pending.slice(0, 10).map((b) => ({ ...b, ...(this.origin ? { world: this.worldPos(b) } : {}) }))
    }
  }

  materialsNeeded (bot) {
    const need = {}
    for (const b of this.pending(bot)) {
      if (b.block !== 'air') need[b.block] = (need[b.block] ?? 0) + 1
    }
    return need
  }
}

function siteFits (bot, origin, size) {
  for (let dx = 0; dx < size.width; dx++) {
    for (let dz = 0; dz < size.depth; dz++) {
      const ground = bot.blockAt(origin.offset(dx, -1, dz))
      if (!ground || ground.boundingBox !== 'block' || UNSUITABLE_GROUND.test(ground.name)) return false
      for (let dy = 0; dy < size.height; dy++) {
        const b = bot.blockAt(origin.offset(dx, dy, dz))
        if (!b || !(isClearable(b) || TEMPORARY_STATION.test(b.name))) return false
      }
    }
  }
  return true
}

// ボットのまわりで一番近い、平らで空いている敷地。
export function findSite (bot, size) {
  const me = bot.entity.position.floored()
  for (let r = 2; r <= SITE_SEARCH_RADIUS; r++) {
    const ring = []
    for (let dx = -r; dx <= r; dx++) {
      for (let dz = -r; dz <= r; dz++) {
        if (Math.max(Math.abs(dx), Math.abs(dz)) !== r) continue
        ring.push([dx, dz])
      }
    }
    for (const [dx, dz] of ring) {
      for (let dy = -SITE_SEARCH_DY; dy <= SITE_SEARCH_DY; dy++) {
        const origin = me.offset(dx, dy, dz)
        if (siteFits(bot, origin, size)) return origin
      }
    }
  }
  return null
}

function findItem (bot, kind) {
  return bot.inventory.items().find((i) => KINDS[kind](i.name))
}

let lastPlaceAt = 0

const occupied = (block) => block && block.name !== 'air' && block.name !== 'cave_air'

// 名前付きの建物で、置くマスにあってもよい（掘ってから置く）ブロック
export const removable = (plan, block, pos, allowDig) =>
  isClearable(block) || TEMPORARY_STATION.test(block.name) ||
  (plan.kind === 'build' && (NATURAL.test(block.name) || !!allowDig?.(pos, block)))

export async function placeOne (bot, plan, b, signal, allowDig = null) {
  if (b.block === 'door') return placeDoor(bot, plan, b, signal)
  if (b.block === 'air') return clearCell(bot, plan, b, signal, allowDig)
  const pos = plan.worldPos(b)
  const found = bot.blockAt(pos)
  if (occupied(found) && !removable(plan, found, pos, allowDig)) throw new Error(`site blocked by ${found.name} at ${pos}`)
  // サーバーが確かめるのは届く距離とカーソルで、視線は確かめない。なので、例えば屋根の最初の
  // ブロックを家の中から壁の上に置ける。
  const goal = new goals.GoalPlaceBlock(pos, bot.world, { range: PLACE_RANGE, LOS: false })
  if (!goal.isEnd(bot.entity.position.floored())) {
    const t = Date.now()
    await walkTo(bot, goal, signal)
    if (Date.now() - t > 3000) console.log(`[build] ${pos} に置くための移動が遅い: ${Date.now() - t}ms、出発点 ${bot.entity.position}`)
  }
  // 草などは届く所に来てから刈る（遠くから掘るとサーバーは断り、手元の世界だけが空気になる）
  const current = bot.blockAt(pos)
  if (occupied(current)) {
    if (!removable(plan, current, pos, allowDig)) throw new Error(`site blocked by ${current.name} at ${pos}`)
    await bot.dig(current, true)
  }
  const eye = bot.entity.position.floored().offset(0.5, 1.6, 0.5)
  const fr = goal.getFaceAndRef(eye)
  if (!fr) throw new Error(`no reference face for ${b.block} at ${pos}`)
  const item = findItem(bot, b.block)
  await bot.equip(item, 'hand')
  // Paper は 300ms に 8 個を超えるアイテム使用のパケットを、何も返さずに捨てる。代わりに、
  // バニラのクライアントの右クリックの連打（4ティック）と同じ間隔で置く。
  const wait = PLACE_INTERVAL_MS - (Date.now() - lastPlaceAt)
  if (wait > 0) await new Promise((resolve) => setTimeout(resolve, wait))
  await bot.lookAt(fr.to, true)
  // 持っているスタックを減らすのは Mineflayer ではなくサーバー。次に置くときに使い切ったスタックを
  // 選ばないよう、そのスロットの更新を待つ。
  const slot = bot.quickBarSlot + bot.inventory.hotbarStart
  const slotUpdated = new Promise((resolve) => {
    const timer = setTimeout(resolve, 1000)
    bot.inventory.once(`updateSlot:${slot}`, () => { clearTimeout(timer); resolve() })
  })
  try {
    await bot.placeBlock(bot.blockAt(fr.ref), fr.face.scaled(-1))
    await slotUpdated
  } catch (e) {
    console.log(`[build] ${pos} に置けなかった（位置 ${bot.entity.position}）: ${e.message}`)
    throw e
  } finally {
    lastPlaceAt = Date.now()
  }
}

// 空けるマス（名前付きの建物）: 届く所まで歩いて掘る。掘ってよいのは植物・自然のブロックと、
// allowDig が許すもの（建物が空けると書いた家の壁）
async function clearCell (bot, plan, b, signal, allowDig) {
  const pos = plan.worldPos(b)
  const found = bot.blockAt(pos)
  if (!found || !occupied(found)) return
  if (!removable(plan, found, pos, allowDig)) throw new Error(`cannot clear ${found.name} at ${pos}`)
  const goal = new goals.GoalNear(pos.x, pos.y, pos.z, PLACE_RANGE - 1)
  if (!goal.isEnd(bot.entity.position.floored())) await walkTo(bot, goal, signal)
  await bot.dig(bot.blockAt(pos), true)
}

const DOOR_WALK_MS = 6000
const DOOR_DIG_MS = 6000
// 約束が ms までに終わらなければ、理由をつけて失敗にする（stop で止める）
async function within (promise, ms, why, stop = () => {}) {
  let timer
  const late = new Promise((resolve, reject) => { timer = setTimeout(() => { stop(); reject(new Error(why)) }, ms) })
  try {
    return await Promise.race([promise, late])
  } finally {
    clearTimeout(timer)
  }
}
// 決めた時間までに着かなければ諦める（経路が見つからないときの長い探索を切る）
async function walkWithin (bot, goal, signal, ms) {
  let timer
  const late = new Promise((resolve, reject) => {
    timer = setTimeout(() => { bot.pathfinder.setGoal(null); reject(new Error(`could not get there in ${ms / 1000}s`)) }, ms)
  })
  try {
    await Promise.race([walkTo(bot, goal, signal), late])
  } finally {
    clearTimeout(timer)
  }
}

// ドアの向きは、置くときにプレイヤーが向いている方角になる。壁の外側のマス（だめなら内側）に立ち、
// ドアのマスの床を見て置く: 視線が壁と直角になる。向きの違うドアは壊して置き直す。ドアは下の
// マスに置けば上のマスもできる
async function placeDoor (bot, plan, b, signal) {
  const phase = (p) => { bot.actionPhase = p }
  const lower = plan.blocks.find((d) => d.block === 'door' && d.x === b.x && d.z === b.z && d.y === b.y - 1) ?? b
  const pos = plan.worldPos(lower)
  const found = bot.blockAt(pos)
  if (occupied(found) && !isClearable(found) && !TEMPORARY_STATION.test(found.name) && !KINDS.door(found.name)) throw new Error(`site blocked by ${found.name} at ${pos}`)
  const out = plan.doorOutward(lower)
  let error = null
  // ボットに近い側から（前は必ず外から: 中にいても外へ回ろうとした）
  const sides = [1, -1].sort((a, b) => bot.entity.position.distanceTo(pos.offset(out.x * a + 0.5, 0, out.z * a + 0.5)) -
    bot.entity.position.distanceTo(pos.offset(out.x * b + 0.5, 0, out.z * b + 0.5)))
  for (const side of sides) {
    phase(`walking to the ${side === 1 ? 'outside' : 'inside'} of the door`)
    try {
      // 片側に行けないと経路探しが長引いて全体が時間切れになった（外が行けなければ内側を試す）
      await walkWithin(bot, new goals.GoalBlock(pos.x + out.x * side, pos.y, pos.z + out.z * side), signal, DOOR_WALK_MS)
      error = null
      break
    } catch (e) {
      signal.throwIfAborted()
      error = e
    }
  }
  if (error) throw new Error(`no place to stand in front of the door at ${pos}: ${error.message}`)
  // 向きの違うドアや草は、ドアの前に立ってから壊す（遠くから掘るとサーバーは断る）
  const current = bot.blockAt(pos)
  if (occupied(current)) {
    if (!isClearable(current) && !TEMPORARY_STATION.test(current.name) && !KINDS.door(current.name)) throw new Error(`site blocked by ${current.name} at ${pos}`)
    phase(`breaking the old ${current.name}`)
    await within(bot.dig(current, true), DOOR_DIG_MS, `the ${current.name} did not break in ${DOOR_DIG_MS / 1000}s`, () => bot.stopDigging())
  }
  phase('picking up the door')
  // 壊したドアが落ちて拾われるのを待つ（隣のマスに立てば拾える）
  for (let i = 0; i < 40 && !findItem(bot, 'door'); i++) await bot.waitForTicks(1)
  const item = findItem(bot, 'door')
  if (!item) throw new Error('no door to place (the old door was not picked up)')
  phase('placing the door')
  await bot.equip(item, 'hand')
  await bot.lookAt(pos.offset(0.5, 0, 0.5), true)
  await bot.placeBlock(bot.blockAt(pos.offset(0, -1, 0)), new Vec3(0, 1, 0))
  lastPlaceAt = Date.now()
  phase('')
}
