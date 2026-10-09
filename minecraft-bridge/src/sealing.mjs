// 採掘場の坑道・枝が空洞（洞窟）に出たら、掘ってきて手元にある丸石で、道（床）・壁・天井を作って塞ぐ
// （docs/design/86_safe_work_and_branch_mining.md §4）。坑道の外に空気が開いていると、洞窟のモブが通路に入ってくる。
//
// 坑道のセル（幅 1、高さ h）を囲む面のうち、岩でなく空いているもの（洞窟、空洞）に丸石を置く。置くときの「面を借りる
// 隣」は、置く場所の隣の固いブロック: 坑道を順に掘り進めるので、前のセルの同じ面はもう固く、どの面にも借りる隣がある
// （床は前のセルの床、壁と天井は前のセルの壁と天井）。ほかの採掘場のセルは、どれも坑道なので塞がない。

import vec3Pkg from 'vec3'
import { inventoryCounts } from './observe.mjs'
import { onMineCell } from './facilities.mjs'

const { Vec3 } = vec3Pkg

export const SEAL_ITEMS = ['cobblestone', 'cobbled_deepslate']
const FLUID = /water|lava/
const SIX = [[0, -1, 0], [1, 0, 0], [-1, 0, 0], [0, 0, 1], [0, 0, -1], [0, 1, 0]]
const SIDES = [[1, 0], [-1, 0], [0, 1], [0, -1]]
const PASSES = 3 // 置いた面が次の面の「借りる隣」になるので、数回まわす
// 階段は、入口からこの段数より深くなってから塞ぐ（浅い所は地上の空気に面していて、洞窟ではない。設計書 134）
export const SEAL_FROM_DEPTH = 4

// 手元の、塞ぐのに使える丸石の数
export const sealStock = (bot) => {
  const have = inventoryCounts(bot)
  return SEAL_ITEMS.reduce((sum, name) => sum + (have[name] ?? 0), 0)
}

// セルを囲む面: 床、天井、4 方の壁（高さの数だけ）。順は床 → 壁 → 天井（下から、置ける順）。
// 採掘場のセル（空ける予定のマス）と skip（'x,y,z' の集合: 階段で掘ってきたセルなど）は囲まれる側ではない
export function enclosure (state, cell, skip = new Set()) {
  const out = [{ pos: new Vec3(cell.x, cell.y - 1, cell.z), role: 'floor' }]
  for (let i = 0; i < cell.h; i++) {
    for (const [dx, dz] of SIDES) out.push({ pos: new Vec3(cell.x + dx, cell.y + i, cell.z + dz), role: 'wall' })
  }
  out.push({ pos: new Vec3(cell.x, cell.y + cell.h, cell.z), role: 'ceiling' })
  return out.filter((f) => !onMineCell(state, f.pos) && !skip.has(`${f.pos.x},${f.pos.y},${f.pos.z}`))
}

// 塞ぐ面のうち、今空いているもの（読み込まれていない面、水や溶岩の面は数えない: 水・溶岩は mineDanger が断る）
export function openings (bot, state, cell, skip) {
  return enclosure(state, cell, skip).filter(({ pos }) => {
    const b = bot.blockAt(pos)
    return !!b && b.boundingBox === 'empty' && !FLUID.test(b.name)
  })
}

// pos に names のうち手元にあるものを 1 個置く（既定は丸石）。借りる隣は、固いブロック（どれでもよい）
export async function placeSeal (bot, pos, signal, names = SEAL_ITEMS) {
  signal.throwIfAborted()
  const name = names.find((n) => (inventoryCounts(bot)[n] ?? 0) > 0)
  if (!name) return false
  for (const [dx, dy, dz] of SIX) {
    const ref = bot.blockAt(pos.offset(dx, dy, dz))
    if (ref?.boundingBox !== 'block') continue
    try {
      await bot.equip(bot.inventory.items().find((i) => i.name === name), 'hand')
      await bot.placeBlock(ref, new Vec3(-dx, -dy, -dz))
    } catch (e) {
      if (signal.aborted) throw e
      continue
    }
    if (bot.blockAt(pos)?.boundingBox === 'block') return true
  }
  return false
}

// セルの空いた面をすべて塞ぐ。床は今立っている前のセルから、壁と天井はセルの中へ入ってから（enter: 入る手）。
// { ok, placed, remaining（塞げなかった面） }
export async function sealCell (bot, state, cell, signal, enter, skip) {
  let placed = 0
  const floors = openings(bot, state, cell, skip).filter((f) => f.role === 'floor')
  for (const f of floors) if (await placeSeal(bot, f.pos, signal)) placed++
  await enter()
  for (let pass = 0; pass < PASSES; pass++) {
    const open = openings(bot, state, cell, skip)
    if (!open.length) break
    let progress = 0
    for (const f of open) if (await placeSeal(bot, f.pos, signal)) { placed++; progress++ }
    if (!progress) break
  }
  const remaining = openings(bot, state, cell, skip)
  return { ok: !remaining.length, placed, remaining }
}

// 塞げなかったセルを、丸石でふさぐ（通路に空洞がつながったままにしない）。ボットはセルの外（前のセル）から
export async function plugCell (bot, cell, signal) {
  let plugged = 0
  for (let i = 0; i < cell.h; i++) {
    const pos = new Vec3(cell.x, cell.y + i, cell.z)
    const b = bot.blockAt(pos)
    if (b?.boundingBox === 'empty' && !FLUID.test(b.name) && await placeSeal(bot, pos, signal)) plugged++
  }
  return plugged
}
