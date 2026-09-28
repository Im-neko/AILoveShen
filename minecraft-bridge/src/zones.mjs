// 街の理想図の区画の地形（設計書 47 §6）: 街の中心から cells x cells の区画、1 区画 size ブロック四方。
// 区画 id は列（x、西→東）A.. と行（z、北→南）1..（地図の画像と同じ呼び方）。Python の理想図が区画の
// 用途を決める材料: 見えている割合、水・溶岩、平らさ、木、建てた物、重なる施設。
import vec3Pkg from 'vec3'
import { mapKind } from './map.mjs'

const { Vec3 } = vec3Pkg

export const TOWN_CELLS = 12
export const ZONE_SIZE = 8
const SCAN_UP = 16
const SCAN_DOWN = 16

export const zoneId = (col, row) => `${String.fromCharCode(65 + col)}${row + 1}`

// 区画の範囲（ワールドの座標、両端を含む）
export function zoneBoxes (center, cells = TOWN_CELLS, size = ZONE_SIZE) {
  const x0 = Math.floor(center.x) - (cells * size) / 2
  const z0 = Math.floor(center.z) - (cells * size) / 2
  const out = []
  for (let row = 0; row < cells; row++) {
    for (let col = 0; col < cells; col++) {
      const minX = x0 + col * size
      const minZ = z0 + row * size
      out.push({ id: zoneId(col, row), min_x: minX, min_z: minZ, max_x: minX + size - 1, max_z: minZ + size - 1 })
    }
  }
  return out
}

// 列の一番上（固いブロックか水・溶岩）: [種類, y] か null（読み込まれていない）
function top (bot, x, z, baseY) {
  for (let y = baseY + SCAN_UP; y >= baseY - SCAN_DOWN; y--) {
    const b = bot.blockAt(new Vec3(x, y, z))
    if (!b) return null
    if (b.boundingBox === 'block' || /water|lava/.test(b.name)) return [mapKind(b.name), y]
  }
  return ['other', baseY - SCAN_DOWN]
}

const overlaps = (box, f) => f.box.min.x <= box.max_x && f.box.max.x >= box.min_x && f.box.min.z <= box.max_z && f.box.max.z >= box.min_z

// GET /zones: 区画ごとの地形。baseY は地面の高さの目安（家の床の下、なければ足元の下）
export function zonesAround (bot, state, center, cells = TOWN_CELLS, size = ZONE_SIZE) {
  const home = state.home
  const baseY = home ? home.min.y - 1 : Math.floor(bot.entity.position.y) - 1
  const facilities = Object.values(state.facilities ?? {})
  return zoneBoxes(center, cells, size).map((box) => {
    const counts = {}
    const heights = []
    let seen = 0
    for (let x = box.min_x; x <= box.max_x; x++) {
      for (let z = box.min_z; z <= box.max_z; z++) {
        const t = top(bot, x, z, baseY)
        if (!t) continue
        seen++
        counts[t[0]] = (counts[t[0]] ?? 0) + 1
        if (t[0] !== 'water' && t[0] !== 'lava') heights.push(t[1])
      }
    }
    const share = (k) => (seen ? Math.round(((counts[k] ?? 0) / seen) * 100) / 100 : 0)
    const sorted = [...heights].sort((a, b) => a - b)
    const median = sorted.length ? sorted[Math.floor(sorted.length / 2)] : null
    return {
      ...box,
      seen: Math.round((seen / (size * size)) * 100) / 100,
      water: share('water'),
      lava: share('lava'),
      trees: share('tree') + share('leaves'),
      built: share('built'),
      stone: share('stone'),
      // 平らさ: 真ん中の高さから ±1 に入る列の割合。高さは地面の目安からの差
      flat: sorted.length ? Math.round((sorted.filter((y) => Math.abs(y - median) <= 1).length / sorted.length) * 100) / 100 : 0,
      height: median === null ? null : median - baseY,
      facilities: facilities.filter((f) => overlaps(box, f)).map((f) => ({ name: f.name, kind: f.kind }))
    }
  })
}
