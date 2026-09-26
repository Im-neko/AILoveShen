// 置いてある家具（ベッド、作業台、かまど、チェスト）を拾う・置き直す。
//
// ユーザー「アイテムの移動もできるようにならないか。ベッドの置き直しとか」。家具を壊すと、その
// アイテムが落ちる（ベッドもそのまま拾える）。拾って置き直すことで、家の外のベッドを家に運ぶ、
// 家の中の配置を変える、ができる。安全の決まり: 今の家の家具は家の外に出さない（家の中での置き直し
// だけ）、ベッドは家の中にだけ置く、中身の分かっているチェストは先に中身を出す。
import vec3Pkg from 'vec3'
import { isHomeCell } from './builds.mjs'

const { Vec3 } = vec3Pkg

export const MOVABLE = /(_bed|^crafting_table|^furnace|^blast_furnace|^smoker|^chest|^trapped_chest|^barrel)$/
const TAKE_RADIUS = 32

const key = (p) => `${p.x},${p.y},${p.z}`

// 家の中に置けるもの（placed(item, home) の item）→ 置いてあるかを見るブロックの名前
export const HOME_FURNITURE = {
  bed: /_bed$/,
  crafting_table: /^crafting_table$/,
  furnace: /^(furnace|blast_furnace|smoker)$/,
  chest: /^(chest|trapped_chest|barrel)$/
}

// 家の部屋（床の高さ）にその家具が置いてあれば、その位置
// 家の中に置けるものか: 種類（bed など）か、色つきのベッド（blue_bed など）
export const isHomeFurnitureItem = (item) => !!HOME_FURNITURE[item] || /^[a-z_]+_bed$/.test(item ?? '')

export function furnitureInHome (bot, home, item) {
  const pattern = HOME_FURNITURE[item] ?? (isHomeFurnitureItem(item) ? new RegExp(`^${item}$`) : null)
  if (!home || !pattern) return null
  const cells = home.cells ?? []
  const spots = cells.length
    ? cells
    : Array.from({ length: (home.max.x - home.min.x + 1) * (home.max.z - home.min.z + 1) }, (_, i) => ({
      x: home.min.x + (i % (home.max.x - home.min.x + 1)),
      z: home.min.z + Math.floor(i / (home.max.x - home.min.x + 1))
    }))
  for (const c of spots) {
    for (const dy of [0, 1]) {
      const p = new Vec3(c.x, home.min.y + dy, c.z)
      if (pattern.test(bot.blockAt(p)?.name ?? '')) return p
    }
  }
  return null
}

// 今の家の家具か（家の部屋のセルで、床の高さ）
export function isHomeFurniture (state, p) {
  const home = state.home
  return !!home && isHomeCell(home, p) && p.y >= home.min.y && p.y <= home.min.y + 1
}

// そのチェストに、覚えている中身があるか（壊すと中身が散らばる）
export function knownContents (state, p) {
  const c = state.memory?.chests?.[key(p)]
  const items = c ? Object.entries(c.contents ?? {}).filter(([, n]) => n > 0) : []
  return items.length ? items.map(([n, k]) => `${n} ${k}`).join(', ') : null
}

// 置いてあるベッドのうち、今の家のものでない一番近いもの（家に運ぶ候補）
export function placedBedToTake (bot, state, radius = TAKE_RADIUS) {
  const ids = Object.values(bot.registry.blocksByName).filter((b) => b.name.endsWith('_bed')).map((b) => b.id)
  const me = bot.entity.position
  return bot.findBlocks({ matching: ids, maxDistance: radius, count: 16 })
    .filter((p) => !isHomeFurniture(state, p))
    .sort((a, b) => a.distanceTo(me) - b.distanceTo(me))
    .map((p) => ({ pos: p, block: bot.blockAt(p)?.name }))
    .find((b) => b.block) ?? null
}

// 動かしてよいか（道具の move_furniture）: 理由の文か null
export function moveRefusal (bot, state, pos, block, to) {
  if (!block || !MOVABLE.test(block.name)) return `there is no bed, crafting table, furnace or chest at ${key(pos)}`
  const isBed = block.name.endsWith('_bed')
  if (isBed && !state.home) return 'there is no home to put the bed in'
  if (isBed && to) return 'a bed is always put in the home: leave out the target'
  if (isHomeFurniture(state, pos) && to && !isHomeFurniture(state, to)) return 'the furniture of the home stays in the home (choose a target inside it)'
  const contents = /chest|barrel/.test(block.name) ? knownContents(state, pos) : null
  if (contents) return `the chest still holds ${contents}: take them out first (withdraw)`
  return null
}
