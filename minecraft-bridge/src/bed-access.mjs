// ベッドのすぐ隣に立てる所があるか（2026-10-11: 家の中に置いた丸石と家具でベッドの周りがふさがり、寝る手が
// 「No path to the goal!」だけで失敗し続けた。何がふさいでいるかを失敗の理由に書く）。
import vec3Pkg from 'vec3'

const { Vec3 } = vec3Pkg
const SIDES = [['east', 1, 0], ['west', -1, 0], ['south', 0, 1], ['north', 0, -1]]
const empty = (b) => !!b && b.boundingBox === 'empty'

// ベッドのマス（foot と、隣り合うもう半分）
function bedCells (bot, pos) {
  const out = [pos]
  for (const [, dx, dz] of SIDES) {
    const p = pos.offset(dx, 0, dz)
    if (bot.blockAt(p)?.name.endsWith('_bed')) out.push(p)
  }
  return out
}

// 立てる隣（足と頭が空き、足元が固い）があれば null、なければ、ふさいでいるものの説明
export function bedBlockers (bot, pos) {
  const cells = bedCells(bot, new Vec3(pos.x, pos.y, pos.z))
  const key = (p) => `${p.x},${p.y},${p.z}`
  const own = new Set(cells.map(key))
  const blocked = []
  for (const c of cells) {
    for (const [side, dx, dz] of SIDES) {
      const n = c.offset(dx, 0, dz)
      if (own.has(key(n))) continue
      const feet = bot.blockAt(n)
      const head = bot.blockAt(n.offset(0, 1, 0))
      const floor = bot.blockAt(n.offset(0, -1, 0))
      if (empty(feet) && empty(head) && floor?.boundingBox === 'block') return null
      const why = !feet ? 'unloaded' : !empty(feet) ? feet.name : !empty(head) ? `${head.name} above` : `no floor (${floor?.name ?? 'unloaded'})`
      blocked.push(`${side} of the bed at ${n.x},${n.y},${n.z}: ${why}`)
    }
  }
  return blocked.join('; ')
}
