// Build plan execution: pick a flat site, place a block list in order, verify it in the world.
//
// The plan is a list of blocks relative to the site origin (min corner of the footprint at
// ground level, i.e. the first air layer). Geometry (walls, roof, door) is decided on the
// Python side; this module only places what it is given, in the given order.

import pathfinderPkg from 'mineflayer-pathfinder'
import { isLog, isPlanks } from './observe.mjs'

const { goals } = pathfinderPkg

const SITE_SEARCH_RADIUS = 24
const SITE_SEARCH_DY = 4
const SITE_RETRY_DISTANCE = 16 // after a failed search, search again only this far from there
const PLACE_RANGE = 4
const BLOCKS_PER_STEP = 8
const PLACE_INTERVAL_MS = 200
const UNSUITABLE_GROUND = /(_leaves|_log|_planks|_door|water|lava|ice|snow$|sand$|gravel$)/

// A block kind in the plan -> predicate over inventory item / world block names
const KINDS = {
  planks: isPlanks,
  log: isLog,
  door: (name) => name.endsWith('_door')
}

// Blocks the site may contain: air, plants, and leaves (dug before placing)
const isClearable = (block) => (block.boundingBox === 'empty' && !['water', 'lava'].includes(block.name)) || block.name.endsWith('_leaves')

export class BuildPlan {
  constructor ({ blocks, width, depth, height }) {
    for (const b of blocks) {
      if (!KINDS[b.block]) throw new Error(`unknown block kind: ${b.block}`)
    }
    this.blocks = blocks
    this.size = { width, depth, height }
    this.origin = null
    this.siteSearchFailedAt = null
  }

  // False while the bot is still near where a site search already failed
  canSearchSite (bot) {
    return !this.siteSearchFailedAt || this.siteSearchFailedAt.distanceTo(bot.entity.position) >= SITE_RETRY_DISTANCE
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

// Nearest flat, clear footprint around the bot.
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

async function placeOne (bot, plan, b) {
  const pos = plan.worldPos(b)
  const current = bot.blockAt(pos)
  if (current && current.name !== 'air' && current.name !== 'cave_air') {
    if (!isClearable(current)) throw new Error(`site blocked by ${current.name} at ${pos}`)
    await bot.dig(current, true)
  }
  // The server validates reach and the cursor, not line of sight, so e.g. the first roof block
  // can be put on a wall top from inside the house.
  const goal = new goals.GoalPlaceBlock(pos, bot.world, { range: PLACE_RANGE, LOS: false })
  if (!goal.isEnd(bot.entity.position.floored())) {
    const t = Date.now()
    await bot.pathfinder.goto(goal)
    if (Date.now() - t > 3000) console.log(`[build] slow path to place ${pos}: ${Date.now() - t}ms, from ${bot.entity.position}`)
  }
  const eye = bot.entity.position.floored().offset(0.5, 1.6, 0.5)
  const fr = goal.getFaceAndRef(eye)
  if (!fr) throw new Error(`no reference face for ${b.block} at ${pos}`)
  const item = findItem(bot, b.block)
  await bot.equip(item, 'hand')
  // Paper drops use-item packets beyond 8 per 300ms without any reply; pace like the vanilla
  // client's right-click repeat (4 ticks) instead.
  const wait = PLACE_INTERVAL_MS - (Date.now() - lastPlaceAt)
  if (wait > 0) await new Promise((resolve) => setTimeout(resolve, wait))
  await bot.lookAt(fr.to, true)
  // The server, not Mineflayer, decrements the held stack. Wait for that slot update so the next
  // placement does not pick a stack that is already used up.
  const slot = bot.quickBarSlot + bot.inventory.hotbarStart
  const slotUpdated = new Promise((resolve) => {
    const timer = setTimeout(resolve, 1000)
    bot.inventory.once(`updateSlot:${slot}`, () => { clearTimeout(timer); resolve() })
  })
  try {
    await bot.placeBlock(bot.blockAt(fr.ref), fr.face.scaled(-1))
    await slotUpdated
  } catch (e) {
    console.log(`[build] place failed at ${pos} from ${bot.entity.position}: ${e.message}`)
    throw e
  } finally {
    lastPlaceAt = Date.now()
  }
}

// Places up to BLOCKS_PER_STEP pending blocks. Returns a short human-readable result.
export async function buildStep (bot, plan, signal) {
  if (!plan.origin) {
    const origin = findSite(bot, plan.size)
    if (!origin) {
      plan.siteSearchFailedAt = bot.entity.position.clone()
      throw new Error(`no flat ${plan.size.width}x${plan.size.depth} site within ${SITE_SEARCH_RADIUS}m; explore elsewhere`)
    }
    plan.origin = origin
  }
  let placed = 0
  for (const b of plan.pending(bot)) {
    if (placed >= BLOCKS_PER_STEP) break
    signal.throwIfAborted()
    if (!findItem(bot, b.block)) {
      if (placed === 0) throw new Error(`out of ${b.block}`)
      break
    }
    await placeOne(bot, plan, b)
    placed++
  }
  const s = plan.status(bot)
  return `placed ${placed} blocks (${s.placed}/${s.total})`
}

// The next block can be placed now: material in hand, and a site chosen or worth searching for here
export function canBuildNow (bot, plan) {
  if (!plan.origin && !plan.canSearchSite(bot)) return false
  const next = plan.pending(bot)[0]
  return !!next && !!findItem(bot, next.block)
}
