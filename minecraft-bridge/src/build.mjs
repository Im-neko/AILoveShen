// 建築計画: 平らな敷地を選び、ブロックを順に1つずつ置き、ワールドで確かめる。
//
// 計画は敷地の原点（地面の高さ、つまり最初の空気の層での、敷地の最小の角）からの相対位置の
// ブロックの一覧。形（壁、屋根、ドア）は Python 側で決める。このモジュールは渡されたものを
// 渡された順に置くだけ。

import pathfinderPkg from 'mineflayer-pathfinder'
import vec3Pkg from 'vec3'
import { isLog, isPlanks } from './observe.mjs'

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
  door: (name) => name.endsWith('_door')
}

// 敷地にあってよいブロック: 空気、植物、葉（置く前に掘る）
const isClearable = (block) => (block.boundingBox === 'empty' && !['water', 'lava'].includes(block.name)) || block.name.endsWith('_leaves')

export class BuildPlan {
  // design: モデルが設計したもの（名前、コンセプト、寸法、ドア）。できた家のために取っておく
  constructor ({ blocks, width, depth, height, design = null }) {
    for (const b of blocks) {
      if (!KINDS[b.block]) throw new Error(`unknown block kind: ${b.block}`)
    }
    this.blocks = blocks
    this.size = { width, depth, height }
    this.design = design
    this.origin = null
    this.siteSearchFailedAt = null
  }

  // 敷地の探索に失敗した場所の近くにまだいる間は false
  canSearchSite (bot) {
    return !this.siteSearchFailedAt || this.siteSearchFailedAt.distanceTo(bot.entity.position) >= SITE_RETRY_DISTANCE
  }

  toJSON () {
    return { blocks: this.blocks, ...this.size, design: this.design, origin: this.origin && { x: this.origin.x, y: this.origin.y, z: this.origin.z } }
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
    return !!block && KINDS[b.block](block.name)
  }

  pending (bot) {
    if (!this.origin) return this.blocks
    return this.blocks.filter((b) => !this.isPlaced(bot, b))
  }

  status (bot) {
    const pending = this.pending(bot)
    return {
      origin: this.origin && { x: this.origin.x, y: this.origin.y, z: this.origin.z },
      total: this.blocks.length,
      placed: this.blocks.length - pending.length,
      complete: !!this.origin && pending.length === 0,
      missing_blocks: pending.slice(0, 10).map((b) => ({ ...b, ...(this.origin ? { world: this.worldPos(b) } : {}) }))
    }
  }

  materialsNeeded (bot) {
    const need = {}
    for (const b of this.pending(bot)) need[b.block] = (need[b.block] ?? 0) + 1
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
        if (!b || !isClearable(b)) return false
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

export async function placeOne (bot, plan, b) {
  const pos = plan.worldPos(b)
  const current = bot.blockAt(pos)
  if (current && current.name !== 'air' && current.name !== 'cave_air') {
    if (!isClearable(current)) throw new Error(`site blocked by ${current.name} at ${pos}`)
    await bot.dig(current, true)
  }
  // サーバーが確かめるのは届く距離とカーソルで、視線は確かめない。なので、例えば屋根の最初の
  // ブロックを家の中から壁の上に置ける。
  const goal = new goals.GoalPlaceBlock(pos, bot.world, { range: PLACE_RANGE, LOS: false })
  if (!goal.isEnd(bot.entity.position.floored())) {
    const t = Date.now()
    await bot.pathfinder.goto(goal)
    if (Date.now() - t > 3000) console.log(`[build] ${pos} に置くための移動が遅い: ${Date.now() - t}ms、出発点 ${bot.entity.position}`)
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
