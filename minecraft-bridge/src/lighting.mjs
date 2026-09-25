// Lighting the grounds around the home (docs/design/15_town.md §4 B): lit(radius).
//
// Judged from the blocks, not the light data (mineflayer's light for 1.21.4 disagreed with the
// server). A torch lights 14 at its block and one less per block walked (Manhattan distance), and
// since 1.18 hostile mobs spawn only at block light 0: ground farther than LIT_REACH from every light
// source is dark. Occlusion is ignored (a wall between is rare on open ground around a house).

import vec3Pkg from 'vec3'
import { LIGHT_SOURCES } from './observe.mjs'
import { inHouse } from './home.mjs'

const { Vec3 } = vec3Pkg
export const LIT_REACH = 12 // one short of 13, the last block the light reaches
const STEP = 2 // ground sampled every other block
const DY = 6 // ground this far above or below the home floor

const lightIds = (bot) => LIGHT_SOURCES.map((n) => bot.registry.blocksByName[n]?.id).filter((id) => id !== undefined)

// The ground cell (air on a full block) at x,z near the home's floor, null where there is none
// (water, tree tops, ...), or UNLOADED where the chunk is out of view
const UNLOADED = 'unloaded'
function groundAt (bot, x, z, y0) {
  for (let y = y0 + DY; y >= y0 - DY; y--) {
    const b = bot.blockAt(new Vec3(x, y, z))
    if (!b) return UNLOADED
    if (b.boundingBox === 'empty') continue
    if (b.name.endsWith('_leaves') || b.name.endsWith('_log') || /water|lava/.test(b.name)) return null
    const above = bot.blockAt(new Vec3(x, y + 1, z))
    return above?.boundingBox === 'empty' && !/water|lava/.test(above.name) ? new Vec3(x, y + 1, z) : null
  }
  return null
}

const manhattan = (a, b) => Math.abs(a.x - b.x) + Math.abs(a.y - b.y) + Math.abs(a.z - b.z)

// The dark ground within radius of the home, nearest to the home first, and how many sampled
// columns are out of view (then nothing is judged: an unseen spot is never taken as lit)
export function darkGround (bot, state, radius) {
  const home = state.home
  const center = home.inside
  const sources = bot.findBlocks({ point: center, matching: lightIds(bot), maxDistance: radius + LIT_REACH + DY, count: 512 })
  const dark = []
  let unloaded = 0
  for (let dx = -radius; dx <= radius; dx += STEP) {
    for (let dz = -radius; dz <= radius; dz += STEP) {
      if (dx * dx + dz * dz > radius * radius) continue
      const x = center.x + dx
      const z = center.z + dz
      if (inHouse(state, new Vec3(x, center.y, z), 1)) continue // the house is lit from inside
      const cell = groundAt(bot, x, z, center.y)
      if (cell === UNLOADED) unloaded++
      else if (cell && !sources.some((s) => manhattan(s, cell) <= LIT_REACH)) dark.push(cell)
    }
  }
  return { dark: dark.sort((a, b) => manhattan(a, center) - manhattan(b, center)), unloaded }
}
