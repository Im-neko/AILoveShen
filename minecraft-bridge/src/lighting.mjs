// 家のまわりの地面を照らす（docs/design/15_town.md §4 B）: lit(radius)。
//
// 光のデータではなくブロックから判定する（mineflayer の 1.21.4 の光はサーバーと食い違った）。
// たいまつはそのブロックで明るさ 14、歩いて1ブロック離れるごとに 1 下がる（マンハッタン距離）。
// 1.18 以降、敵対モブはブロック光 0 にしか湧かないので、どの光源からも LIT_REACH より遠い地面は暗い。
// 遮蔽は無視する（家のまわりの開けた地面で、間に壁があることはまれ）。

import vec3Pkg from 'vec3'
import { LIGHT_SOURCES } from './observe.mjs'
import { inHouse } from './home.mjs'

const { Vec3 } = vec3Pkg
export const LIT_REACH = 12 // 光が届く最後のブロック 13 の1つ手前
const STEP = 2 // 地面は1ブロックおきに調べる
const DY = 6 // 家の床からこの高さだけ上下の地面まで見る

const lightIds = (bot) => LIGHT_SOURCES.map((n) => bot.registry.blocksByName[n]?.id).filter((id) => id !== undefined)

// 家の床の高さに近い x,z の地面のセル（完全なブロックの上の空気）。ない場所（水、木の上など）は
// null、チャンクが見えていない場所は UNLOADED
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

// 家から radius 以内の暗い地面（家に近い順）と、調べた列のうち見えていない数（そのときは何も
// 判定しない: 見えていない場所を明るいとみなすことはない）
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
      if (inHouse(state, new Vec3(x, center.y, z), 1)) continue // 家の中は内側から照らす
      const cell = groundAt(bot, x, z, center.y)
      if (cell === UNLOADED) unloaded++
      else if (cell && !sources.some((s) => manhattan(s, cell) <= LIT_REACH)) dark.push(cell)
    }
  }
  return { dark: dark.sort((a, b) => manhattan(a, center) - manhattan(b, center)), unloaded }
}
