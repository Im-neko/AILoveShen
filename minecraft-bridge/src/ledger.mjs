// ボットが置いた物と掘った物の記録（docs/design/55_liquid_and_trees.md）。
//
// placed: 自分で置いたブロック。木ではない原木（間違えて置いた物）を、集めてよいものとして見分ける。
// 見つけた拠点（村の小屋など）の家具は自分の物ではない: 家具（ベッド、作業台、かまど、チェスト）も
// 自分で置いた物だけを記録し、placed(bed, home) を見つけた家のベッドで満たさない（docs/design/62_found_base.md）。
// 置いた後に別のブロックに変わっていれば、その記録は使わない（名前が同じかを見る）
// dug: 最近掘ったブロック。掘った跡に水が流れ込んだら、同じ物で埋める（plug_hole）

import vec3Pkg from 'vec3'

const { Vec3 } = vec3Pkg

const MAX_PLACED = 400
// 置いてあるのを拾って置き直せる家具（furniture.mjs の MOVABLE）。記録もこの種類
export const FURNITURE_BLOCK = /(_bed|^crafting_table|^furnace|^blast_furnace|^smoker|^chest|^trapped_chest|^barrel)$/
const MAX_DUG = 200
export const DUG_TICKS = 10 * 60 * 20 // 掘った跡を覚えている時間（10 分）
export const PLUG_TRIES = 2
const HOLE_OK = new Set(['air', 'cave_air', 'water'])
const NEIGHBORS = [[0, 0, 0], [1, 0, 0], [-1, 0, 0], [0, 0, 1], [0, 0, -1], [0, 1, 0]]
const key = (p) => `${p.x},${p.y},${p.z}`

export function recordPlaced (state, pos, name, now) {
  state.placed ??= {}
  state.placed[key(pos)] = { name, at: now }
  const keys = Object.keys(state.placed)
  if (keys.length > MAX_PLACED) {
    keys.sort((a, b) => state.placed[a].at - state.placed[b].at).slice(0, keys.length - MAX_PLACED).forEach((k) => delete state.placed[k])
  }
}

// 自分で置いて、今もそのブロックがあるか
export function placedByMe (bot, state, pos) {
  const e = state.placed?.[key(pos)]
  return !!e && bot.blockAt(pos)?.name === e.name
}

// 自分で置いた家具か。ベッドは 2 マスで、置いた記録は足側だけ: 頭側なら横のマスの記録を見る
export function furniturePlacedByMe (bot, state, pos) {
  if (placedByMe(bot, state, pos)) return true
  if (!bot.blockAt(pos)?.name.endsWith('_bed')) return false
  return [[1, 0], [-1, 0], [0, 1], [0, -1]].some(([x, z]) => {
    const q = pos.offset(x, 0, z)
    return bot.blockAt(q)?.name.endsWith('_bed') && placedByMe(bot, state, q)
  })
}

export function recordDug (state, pos, name, now) {
  state.dug ??= []
  state.dug.push({ x: pos.x, y: pos.y, z: pos.z, name, at: now })
  if (state.dug.length > MAX_DUG) state.dug.splice(0, state.dug.length - MAX_DUG)
}

// 掘った跡のうち、水が入り込んでいる・入り込みそうなもの（横か上か中に水）。近い順ではなく記録の順
// skip(pos): 水を入れるための穴（畑の水のマス）は、水が入っていて当たり前なので埋めない
export function leakingHoles (bot, state, now, skip = () => false) {
  return (state.dug ?? [])
    .filter((d) => now - d.at <= DUG_TICKS && !d.plugged && (d.tries ?? 0) < PLUG_TRIES)
    .map((d) => ({ d, pos: new Vec3(d.x, d.y, d.z) }))
    .filter(({ pos }) => !skip(pos) && HOLE_OK.has(bot.blockAt(pos)?.name ?? '') &&
      NEIGHBORS.some(([x, y, z]) => bot.blockAt(pos.offset(x, y, z))?.name === 'water'))
}

// ボットの掘る・置くを記録する（bot.dig を包んで掘る前のブロックの名前を取る。置くのは mineflayer の
// blockPlaced）。パスファインダーも bot.dig / bot.placeBlock を通るので、歩きながら掘った・置いたものも入る
// onOwnChange(pos): 自分の行動で変わったセル（視界の地図がその場で読み直す。docs/design/59_world_map.md）
export function installLedger (bot, state, onOwnChange = () => {}) {
  const dig = bot.dig.bind(bot)
  bot.dig = async (block, ...rest) => {
    const at = block?.position && { pos: block.position.clone(), name: block.name, solid: block.boundingBox === 'block' }
    const result = await dig(block, ...rest)
    if (at?.solid) recordDug(state, at.pos, at.name, Number(bot.time.age))
    if (at) onOwnChange(at.pos)
    return result
  }
  // 原木と家具だけ記録する: 足場の土や建物の全ブロックを入れると、上限（400）で間違えて置いた原木の記録が先に消える
  bot.on('blockPlaced', (_old, block) => {
    if (block?.position && (block.name?.endsWith('_log') || FURNITURE_BLOCK.test(block.name ?? ''))) recordPlaced(state, block.position, block.name, Number(bot.time.age))
    if (block?.position) onOwnChange(block.position)
  })
}
