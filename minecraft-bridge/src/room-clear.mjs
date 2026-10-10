// 家の中にあるいらないブロック（置いた丸石、土、余った板材）を片付ける（2026-10-11: 閉じ込められたとき足場に積んだ
// 丸石や土、家の計画にない板材が部屋に残り、ベッドの隣をふさいだ。壁や家具は残す）。設計書 165。
import vec3Pkg from 'vec3'
import { TERRAIN } from './leveling.mjs'

const { Vec3 } = vec3Pkg

// 片付けてよい物: 自然の地形（土・草ブロック・石…）、丸石、板材・原木。家具（ベッド、チェスト、作業台、かまど、松明、ドア…）
// や階段・ガラスなどの造作は残す
export const STRAY = new RegExp(`${TERRAIN.source}|^(cobblestone|mossy_cobblestone|cobbled_deepslate)$|_planks$|_log$`)
const DEFAULT_WALL_HEIGHT = 3

// 家の部屋（床の高さから壁の高さまで）にある、片付けるブロック。計画が置くと決めたブロック（正しく置かれた壁や柱）は残す。
// ボットに近い順
export function strayBlocks (bot, state) {
  const h = state.home
  if (!h?.min || !h?.max) return []
  const height = Math.max(1, Math.floor(Number(h.design?.wall_height ?? DEFAULT_WALL_HEIGHT)))
  const plan = state.plan?.origin ? state.plan : null
  const planned = new Map()
  if (plan) for (const b of plan.blocks) { const p = plan.worldPos(b); planned.set(`${p.x},${p.y},${p.z}`, b) }
  const out = []
  for (let y = h.min.y; y < h.min.y + height; y++) {
    for (let x = h.min.x; x <= h.max.x; x++) {
      for (let z = h.min.z; z <= h.max.z; z++) {
        const pos = new Vec3(x, y, z)
        const block = bot.blockAt(pos)
        if (!block || block.boundingBox !== 'block' || !STRAY.test(block.name)) continue
        const want = planned.get(`${x},${y},${z}`)
        if (want && plan.looksPlaced(want, block)) continue
        out.push(pos)
      }
    }
  }
  const me = bot.entity.position
  return out.sort((a, b) => a.distanceTo(me) - b.distanceTo(me))
}

// 「cobblestone x6, oak_planks x2」
export function describeStrays (bot, list) {
  const count = new Map()
  for (const p of list) { const n = bot.blockAt(p)?.name ?? '?'; count.set(n, (count.get(n) ?? 0) + 1) }
  return [...count].map(([n, k]) => `${n} x${k}`).join(', ')
}
