// 歩いていて動けなくなった所の記録（水の中で岸に上がれず詰まる原因を、推測でなく見て決めるため）。
//
// 詰まりを見つけた瞬間の位置、水の中か、周りのブロック（足元の高さの前後を上から 4 層、5x5）、
// 経路の次の数手（掘る・置くがあるか）を、ブリッジのログと logs/stuck-walks.jsonl に残す。
// 失敗の文にも位置と水の中かどうかを添える（logs/steps の result に出る）。

import { appendFileSync, mkdirSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const DEFAULT_FILE = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..', 'logs', 'stuck-walks.jsonl')
const LAYERS = [2, 1, 0, -1] // 足元のセルから見た高さ（上から）
const RADIUS = 2
const NEXT_MOVES = 3

const one = (n) => Math.round(n * 10) / 10

// 1 セルを 1 文字に: ~ 水、L 溶岩、# 固いブロック、o それ以外の当たり判定（柵・扉など）、. 空気、? 読み込まれていない
export function cellChar (block) {
  if (!block) return '?'
  if (block.name === 'water') return '~'
  if (block.name === 'lava') return 'L'
  if (block.boundingBox === 'block') return '#'
  return block.boundingBox === 'empty' ? '.' : 'o'
}

function grid (bot, cell) {
  return LAYERS.map((dy) => {
    const rows = []
    for (let dz = -RADIUS; dz <= RADIUS; dz++) {
      let row = ''
      for (let dx = -RADIUS; dx <= RADIUS; dx++) {
        const at = cell.offset(dx, dy, dz)
        row += dx === 0 && dz === 0 && dy === 0 ? '@' : cellChar(bot.blockAt?.(at))
      }
      rows.push(row)
    }
    return { dy, rows }
  })
}

const goalOf = (goal) => (goal && typeof goal === 'object'
  ? { kind: goal.constructor?.name, ...(goal.x !== undefined ? { x: goal.x, z: goal.z } : {}), ...(goal.y !== undefined ? { y: goal.y } : {}) }
  : String(goal))

// path_update の最後の結果（status と、次の数手）
export function pathOf (update) {
  if (!update) return null
  const path = update.path ?? []
  return {
    status: update.status,
    length: path.length,
    next: path.slice(0, NEXT_MOVES).map((n) => ({ x: n.x, y: n.y, z: n.z, break: n.toBreak?.length ?? 0, place: n.toPlace?.length ?? 0 }))
  }
}

export function snapshot (bot, goal, update) {
  const p = bot.entity.position
  const cell = p.floored()
  return {
    at: [one(p.x), one(p.y), one(p.z)],
    cell: [cell.x, cell.y, cell.z],
    in_water: !!bot.entity.isInWater,
    on_ground: !!bot.entity.onGround,
    feet: bot.blockAt?.(cell)?.name ?? null,
    below: bot.blockAt?.(cell.offset(0, -1, 0))?.name ?? null,
    goal: goalOf(goal),
    path: pathOf(update),
    grid: grid(bot, cell)
  }
}

// 失敗の文に添える短い位置（例: `-1.5,64.2,3.2 in water`）
export const where = (snap) => `${snap.at.join(',')}${snap.in_water ? ' in water' : ''}`

export function record (snap, now = new Date(), file = process.env.STUCK_WALKS_FILE ?? DEFAULT_FILE) {
  try {
    mkdirSync(path.dirname(file), { recursive: true })
    appendFileSync(file, JSON.stringify({ time: now.toISOString(), ...snap }) + '\n')
  } catch (e) {
    console.log(`[move] 詰まりの記録を書けない: ${e.message}`)
  }
}
