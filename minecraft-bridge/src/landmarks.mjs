// まわりの目印（ベッド、ドア、作業台、かまど、チェスト、松明）と、見つけた拠点。
//
// これまでの観測は、モブ・落とし物・持ち物・覚えた資源と、ボットが自分で建てた家だけだった。
// ボットが建てていないベッドや家（視聴者が建てたもの、村の家、覚えていることを消した後の前の家）は
// 目の前にあっても分からなかった。ここでそれを観測に出し、家がないときは、ドアのある屋根つきの
// 閉じた部屋を家として使う（adoptFoundBase）。
import vec3Pkg from 'vec3'
import { round, bearing } from './observe.mjs'
import { isHomeCell } from './builds.mjs'

const { Vec3 } = vec3Pkg

export const LANDMARK_RADIUS = 24
const MAX_ROOM_CELLS = 100 // これより広い「部屋」は家とみなさない（屋根の下の広場、洞窟）
const ROOM_REACH = 8 // ドアから部屋の端まで（閉じていなければ、ここで外とみなす）
const ROOF_HEIGHT = 6

// 目印の種類: 名前 -> 表示の名前（Python が日本語にする）
const KINDS = [
  ['bed', /_bed$/],
  ['door', /_door$/],
  ['crafting_table', /^crafting_table$/],
  ['furnace', /^(furnace|blast_furnace|smoker)$/],
  ['chest', /^(chest|trapped_chest|barrel)$/],
  ['torch', /^(torch|wall_torch|lantern|soul_torch|soul_wall_torch|soul_lantern)$/]
]

function idsMatching (bot, pattern) {
  return Object.values(bot.registry.blocksByName).filter((b) => pattern.test(b.name)).map((b) => b.id)
}

// その位置が誰のものか: 今の家、前の家、名前付きの建物（なければ null: ボットが建てていないもの）
function ownerOf (state, p) {
  const inBox = (h) => h && (isHomeCell(h, p) || (p.x >= h.min.x - 1 && p.x <= h.max.x + 1 && p.z >= h.min.z - 1 && p.z <= h.max.z + 1)) &&
    p.y >= h.min.y - 1 && p.y <= h.min.y + 4
  if (inBox(state.home)) return 'home'
  if ((state.formerHomes ?? []).some(inBox)) return 'former_home'
  return null
}

// まわりの目印: 種類ごとに数と一番近いもの（距離、方角、座標、誰のものか）
export function landmarks (bot, state, radius = LANDMARK_RADIUS) {
  const me = bot.entity.position
  const out = []
  for (const [kind, pattern] of KINDS) {
    let found = bot.findBlocks({ matching: idsMatching(bot, pattern), maxDistance: radius, count: 64 })
    // ドアとベッドは 2 ブロックで 1 つ: 下半分（足）だけ数える
    if (kind === 'door') found = found.filter((p) => bot.blockAt(p)?.getProperties?.().half !== 'upper')
    if (kind === 'bed') found = found.filter((p) => bot.blockAt(p)?.getProperties?.().part !== 'head')
    if (!found.length) continue
    found.sort((a, b) => a.distanceTo(me) - b.distanceTo(me))
    const p = found[0]
    out.push({
      kind,
      count: found.length,
      nearest: { x: p.x, y: p.y, z: p.z, distance_m: round(p.distanceTo(me)), direction: bearing(me, p) },
      owner: ownerOf(state, p)
    })
  }
  return out
}

// ドアの片側から床の高さで塗りつぶし、屋根つきで閉じた部屋ならそのセル。開いていれば null
export function roomFrom (bot, start) {
  const y = start.y
  const seen = new Set()
  const cells = []
  const queue = [start]
  const key = (p) => `${p.x},${p.z}`
  const passable = (p) => {
    const b = bot.blockAt(p)
    const above = bot.blockAt(p.offset(0, 1, 0))
    const below = bot.blockAt(p.offset(0, -1, 0))
    if (!b || !above || !below) return null // 読み込まれていない
    if (b.name.endsWith('_door')) return false
    const walkable = b.boundingBox === 'empty' || /(_bed|chest|barrel|furnace|smoker|crafting_table|_carpet)$/.test(b.name)
    return walkable && above.boundingBox !== 'block' && below.boundingBox === 'block'
  }
  const roofed = (p) => {
    for (let dy = 2; dy <= ROOF_HEIGHT; dy++) if (bot.blockAt(p.offset(0, dy, 0))?.boundingBox === 'block') return true
    return false
  }
  while (queue.length) {
    const p = queue.shift()
    if (seen.has(key(p))) continue
    seen.add(key(p))
    if (Math.abs(p.x - start.x) > ROOM_REACH || Math.abs(p.z - start.z) > ROOM_REACH) return null
    const ok = passable(p)
    if (ok === null) return null
    if (!ok) continue
    if (!roofed(p)) return null
    cells.push(p)
    if (cells.length > MAX_ROOM_CELLS) return null
    for (const [dx, dz] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) queue.push(new Vec3(p.x + dx, y, p.z + dz))
  }
  return cells.length >= 2 ? cells : null
}

// 近くのドアのうち、片側が閉じた部屋になっているもの（一番近いもの）→ 家の形 { door, inside, outside, min, max, cells }
export function findBase (bot, radius = LANDMARK_RADIUS) {
  const me = bot.entity.position
  const doors = bot.findBlocks({ matching: idsMatching(bot, /_door$/), maxDistance: radius, count: 32 })
    .filter((p) => bot.blockAt(p)?.getProperties?.().half !== 'upper')
    .sort((a, b) => a.distanceTo(me) - b.distanceTo(me))
  for (const door of doors) {
    for (const [dx, dz] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
      const inside = door.offset(dx, 0, dz)
      const outside = door.offset(-dx, 0, -dz)
      // 外側は歩ける所（壁の中のドアで、反対側が外）
      if (bot.blockAt(outside)?.boundingBox === 'block') continue
      const cells = roomFrom(bot, inside)
      if (!cells) continue
      const xs = cells.map((c) => c.x)
      const zs = cells.map((c) => c.z)
      return {
        name: null,
        design: null,
        door,
        inside,
        outside,
        min: new Vec3(Math.min(...xs), door.y, Math.min(...zs)),
        max: new Vec3(Math.max(...xs), door.y, Math.max(...zs)),
        cells: cells.map((c) => ({ x: c.x, z: c.z })),
        bed: null,
        breach: [],
        found: true
      }
    }
  }
  return null
}

// 家がまだないとき、近くで見つけた拠点を家にする（建てる前に使える家があれば使う）→ 家にしたら true
export function adoptFoundBase (bot, state) {
  if (state.home || !bot.entity) return false
  const base = findBase(bot)
  if (!base) return false
  base.name = '見つけた拠点'
  state.home = base
  return true
}
