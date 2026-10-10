// 採掘場の出入りは、らせん階段を通る（docs/design/145_mine_by_the_stairs.md）。
//
// 経路探しは最も安い道を選ぶので、自然の洞窟や谷が採掘場の底へ近道になっていると、階段でなく洞窟を通る:
// 水の中を落ち、7 のダメージを受けて底に着いた。帰りは、区間の目標が横の位置だけ（GoalNearXZ）なので、地下のまま
// 横へ進む洞窟が「着いた」ことになり、そのまま深層まで潜った（2026-10-10）。採掘場の中と外を行き来するときは、
// 階段の途中のセルを順に目標にして、階段を歩かせる。

import pkg from 'mineflayer-pathfinder'
import vec3Pkg from 'vec3'
import { onMineCell } from './facilities.mjs'

const { goals } = pkg
const { Vec3 } = vec3Pkg

const STEP = 5 // 階段のセルを何個おきに目標にするか
const NEAR_H = 1.5 // セルの中心からこの内（水平）にいれば、採掘場の中
const SURFACE_BAND = 6 // 入口からこれより浅い所は、地上と同じ（階段を通らず、ふつうに歩く）

const mines = (state) => Object.values(state.facilities ?? {}).filter((f) => f.kind === 'mine' && f.layout?.cells?.length)
const stairsOf = (fac) => fac.layout.cells.filter((c) => c.part === 'stairs')
const entranceOf = (fac) => fac.layout.cells[0]

// p のいる採掘場（セルの上下の範囲にいるもの）。なければ null
export function mineAround (state, p) {
  for (const fac of mines(state)) {
    if (fac.layout.cells.some((c) => Math.abs(p.x - (c.x + 0.5)) <= NEAR_H && Math.abs(p.z - (c.z + 0.5)) <= NEAR_H && p.y >= c.y - 1 && p.y <= c.y + c.h)) return fac
  }
  return null
}

// 入口から SURFACE_BAND より深いか（階段を通る深さか）
const deep = (fac, p) => entranceOf(fac).y - p.y > SURFACE_BAND

// 階段のセルを、入口から底へ STEP 個おきに並べたもの（底を含む）
export function stairWaypoints (fac) {
  const stairs = stairsOf(fac)
  const out = []
  for (let i = STEP; i < stairs.length; i += STEP) out.push(stairs[i])
  if (stairs.length && out.at(-1) !== stairs.at(-1)) out.push(stairs.at(-1))
  return out
}

const block = (c) => new goals.GoalBlock(c.x, c.y, c.z)

// 外から採掘場の深い所へ: 入口まで歩き（walkTo）、階段を底まで下りる
export async function enterMine (fac, walkTo, go, signal) {
  const entrance = entranceOf(fac)
  await walkTo(new Vec3(entrance.x + 0.5, entrance.y, entrance.z + 0.5))
  signal.throwIfAborted()
  await go(block(entrance))
  for (const c of stairWaypoints(fac)) {
    signal.throwIfAborted()
    await go(block(c))
  }
}

// 採掘場の中から外へ: 階段の底まで行き（坑道や枝にいるとき）、階段を入口まで上る
export async function leaveMine (fac, me, go, signal) {
  const stairs = stairsOf(fac)
  if (!stairs.length) return
  let nearest = 0
  let best = Infinity
  stairs.forEach((c, i) => {
    const d = Math.hypot(me.x - (c.x + 0.5), me.y - c.y, me.z - (c.z + 0.5))
    if (d < best) { best = d; nearest = i }
  })
  if (best > 3) nearest = stairs.length - 1 // 階段にいない: 坑道・部屋・枝。底の階段へ出てから上る
  if (best > 3) await go(block(stairs.at(-1)))
  for (let i = nearest - STEP; i > -STEP; i -= STEP) {
    signal.throwIfAborted()
    await go(block(stairs[Math.max(i, 0)]))
    if (i <= 0) break
  }
}

// target へ向かう前に、採掘場を出入りするなら階段を通る。何もしなければ false
export async function routeThroughMine (bot, state, target, walkTo, go, signal) {
  const here = mineAround(state, bot.entity.position)
  const there = onMineCell(state, { x: Math.floor(target.x), y: Math.floor(target.y), z: Math.floor(target.z) }) ? mineAround(state, target) : null
  if (here && there && here === there) return false
  if (here && deep(here, bot.entity.position)) {
    await leaveMine(here, bot.entity.position, go, signal)
    return true
  }
  if (there && deep(there, target) && !here) {
    await enterMine(there, walkTo, go, signal)
    return true
  }
  return false
}

// 地下から地上へ向かうか（目標がこの高さより上）。区間（横の位置だけの目標）に分けると、地下のまま横へ進む洞窟が
// 「着いた」ことになる。高さも含めて 1 つの目標で歩く
export const RISE_DIRECT_M = 14
export const climbsDirect = (me, target) => Number.isFinite(target.y) && target.y - me.y > RISE_DIRECT_M
