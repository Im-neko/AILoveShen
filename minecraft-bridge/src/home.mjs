// The home (a finished house) and persistence of the build plan and the home.
//
// Both live in memory otherwise, and the bridge restarts often: data/state.json keeps them so a
// restart neither forgets the house nor the build in progress.
//
// Mineflayer-pathfinder does not handle doors (canOpenDoors is off: it misbehaves), so entering
// and leaving are done explicitly: walk to the cell in front of the door, open it, walk through,
// close it behind.

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import vec3Pkg from 'vec3'
import pathfinderPkg from 'mineflayer-pathfinder'
import { BuildPlan } from './build.mjs'
import { nearbyEntities, isHostile } from './observe.mjs'

const { Vec3 } = vec3Pkg
const { goals } = pathfinderPkg
const DATA_FILE = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'data', 'state.json')
const DOOR_TOGGLE_TIMEOUT_MS = 2000
const WALK_THROUGH_TIMEOUT_MS = 3000
// A hostile this close to the door keeps the bot inside: a creeper followed it home, waited at the
// door and blew up the doorway (and the house corner) as the bot stepped out in the morning.
const DOOR_DANGER_RADIUS = 8

const toVec = (p) => new Vec3(p.x, p.y, p.z)
const plain = (v) => ({ x: v.x, y: v.y, z: v.z })

export function saveState (state) {
  const data = {
    plan: state.plan && { ...state.plan.toJSON() },
    home: state.home && {
      door: plain(state.home.door),
      inside: plain(state.home.inside),
      outside: plain(state.home.outside),
      min: plain(state.home.min),
      max: plain(state.home.max),
      bed: state.home.bed ? plain(state.home.bed) : null
    }
  }
  fs.mkdirSync(path.dirname(DATA_FILE), { recursive: true })
  fs.writeFileSync(DATA_FILE, JSON.stringify(data, null, 1))
}

export function loadState (state) {
  if (!fs.existsSync(DATA_FILE)) return
  const data = JSON.parse(fs.readFileSync(DATA_FILE, 'utf8'))
  if (data.plan) state.plan = BuildPlan.fromJSON(data.plan)
  if (data.home) {
    const h = data.home
    state.home = { door: toVec(h.door), inside: toVec(h.inside), outside: toVec(h.outside), min: toVec(h.min), max: toVec(h.max), bed: h.bed ? toVec(h.bed) : null }
  }
}

// The finished plan becomes the home: the door's lower half, the cells just inside and outside it,
// and the interior (the footprint without its walls).
export function homeFromPlan (plan) {
  const doors = plan.blocks.filter((b) => b.block === 'door').sort((a, b) => a.y - b.y)
  if (!doors.length) return null
  const d = doors[0]
  const { width, depth } = plan.size
  const inward = d.x === 0 ? [1, 0] : d.x === width - 1 ? [-1, 0] : d.z === 0 ? [0, 1] : [0, -1]
  const door = plan.worldPos(d)
  return {
    door,
    inside: door.offset(inward[0], 0, inward[1]),
    outside: door.offset(-inward[0], 0, -inward[1]),
    min: plan.origin.offset(1, 0, 1),
    max: plan.origin.offset(width - 2, 0, depth - 2),
    bed: null
  }
}

export function isInside (bot, home) {
  if (!home) return false
  const p = bot.entity.position.floored()
  return p.x >= home.min.x && p.x <= home.max.x && p.z >= home.min.z && p.z <= home.max.z &&
    p.y >= home.min.y && p.y <= home.min.y + 1
}

// A mob outside the walls cannot reach a bot inside with the door closed.
export function shelteredFrom (bot, home, entity) {
  if (!isInside(bot, home) || isDoorOpen(bot, home)) return false
  const p = entity.position.floored()
  return !(p.x >= home.min.x && p.x <= home.max.x && p.z >= home.min.z && p.z <= home.max.z)
}

const isTrue = (v) => v === true || v === 'true'

export function isDoorOpen (bot, home) {
  const block = bot.blockAt(home.door)
  return !!block && isTrue(block.getProperties().open)
}

async function setDoor (bot, home, open) {
  const block = bot.blockAt(home.door)
  if (!block || !block.name.endsWith('_door')) throw new Error(`no door at ${home.door}`)
  if (isDoorOpen(bot, home) === open) return
  await bot.lookAt(home.door.offset(0.5, 0.5, 0.5), true)
  await bot.activateBlock(block)
  const start = Date.now()
  while (isDoorOpen(bot, home) !== open) {
    if (Date.now() - start > DOOR_TOGGLE_TIMEOUT_MS) throw new Error(`door did not ${open ? 'open' : 'close'}`)
    await bot.waitForTicks(1)
  }
}

// Walk straight to the center of `cell` (one block away, through the open door).
async function walkInto (bot, cell) {
  const target = cell.offset(0.5, 0, 0.5)
  await bot.lookAt(target.offset(0, bot.entity.eyeHeight, 0), true)
  bot.setControlState('forward', true)
  const start = Date.now()
  try {
    while (bot.entity.position.xzDistanceTo(target) > 0.35) {
      if (Date.now() - start > WALK_THROUGH_TIMEOUT_MS) throw new Error(`could not walk to ${cell}`)
      await bot.lookAt(target.offset(0, bot.entity.eyeHeight, 0), true)
      await bot.waitForTicks(1)
    }
  } finally {
    bot.clearControlStates()
  }
}

export async function enterHome (bot, home) {
  if (!isInside(bot, home)) {
    try {
      await bot.pathfinder.goto(new goals.GoalBlock(home.outside.x, home.outside.y, home.outside.z))
    } finally {
      bot.pathfinder.setGoal(null)
    }
    await setDoor(bot, home, true)
    await walkInto(bot, home.door)
    await walkInto(bot, home.inside)
  }
  await setDoor(bot, home, false)
}

// Hostile mobs waiting outside near the door
export function dangerOutside (bot, home) {
  if (!home) return []
  return nearbyEntities(bot).filter(({ e }) => isHostile(bot, e) && e.position.distanceTo(home.outside) <= DOOR_DANGER_RADIUS &&
    !(e.position.x >= home.min.x && e.position.x < home.max.x + 1 && e.position.z >= home.min.z && e.position.z < home.max.z + 1))
}

const inHome = (home, p) => p.x >= home.min.x && p.x <= home.max.x && p.z >= home.min.z && p.z <= home.max.z

// A bed goes straight in from the door: foot one cell past the inside cell, head one further
export function bedSpot (bot, home) {
  const inward = home.inside.minus(home.door)
  const foot = home.inside.plus(inward)
  const head = foot.plus(inward)
  const free = (p) => inHome(home, p) && bot.blockAt(p)?.name === 'air' && bot.blockAt(p.offset(0, -1, 0))?.boundingBox === 'block'
  return free(foot) && free(head) ? { foot, inward } : null
}

export const hasBed = (bot, home) => !!home?.bed && !!bot.blockAt(home.bed)?.name.endsWith('_bed')

export async function leaveHome (bot, home) {
  if (!isInside(bot, home)) return
  const danger = dangerOutside(bot, home)
  if (danger.length) throw new Error(`staying inside: ${danger.map(({ e }) => e.name).join(', ')} waiting outside the door`)
  if (bot.entity.position.floored().xzDistanceTo(home.inside) > 0) {
    // Pathing inside the closed house is fine: the interior is free of obstacles
    try {
      await bot.pathfinder.goto(new goals.GoalBlock(home.inside.x, home.inside.y, home.inside.z))
    } finally {
      bot.pathfinder.setGoal(null)
    }
  }
  await setDoor(bot, home, true)
  await walkInto(bot, home.door)
  await walkInto(bot, home.outside)
  await setDoor(bot, home, false)
}
