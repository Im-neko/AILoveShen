// 坑道の洞窟を塞ぐ丸石が足りないとき、掘ってきた坑道の横の壁から石を掘って丸石にする（2026-10-10: 丸石 0 で
// 「塞げない」と採掘場の設計をそこで切り捨て、掘り尽くした扱いになって、坑道と地上を往復した）。
// 掘ってよいのは、採掘場の設計の外にある石（stone / deepslate: 丸石を落とす）で、掘っても穴が洞窟や水・溶岩・砂利に
// つながらない所だけ。
import vec3Pkg from 'vec3'
import { onMineCell, inMineBody } from './facilities.mjs'

const { Vec3 } = vec3Pkg

export const QUARRY_ROCK = /^(stone|deepslate)$/
const FALLS = /^(sand|red_sand|gravel|suspicious_)/
const SIDES = [[1, 0], [-1, 0], [0, 1], [0, -1]]
const SIX = [[0, -1, 0], [1, 0, 0], [-1, 0, 0], [0, 0, 1], [0, 0, -1], [0, 1, 0]]

// 掘ってきた坑道の列（columns: セルの並び。ボットに近い順）の横の、丸石にできる石。avoid: 掘らない所（'x,y,z' の集合:
// これから塞ぐ面など）。各列の足元の高さと、その 1 つ上（歩く 2 マスの高さ）の、東西南北の壁だけ。最大 need 個
export function quarryTargets (bot, state, columns, need, avoid = new Set()) {
  const out = []
  const seen = new Set()
  for (const at of columns) {
    for (const dy of [0, 1]) {
      for (const [dx, dz] of SIDES) {
        const p = new Vec3(at.x + dx, at.y + dy, at.z + dz)
        const k = `${p.x},${p.y},${p.z}`
        if (seen.has(k)) continue
        seen.add(k)
        const b = bot.blockAt(p)
        if (!b || !QUARRY_ROCK.test(b.name) || avoid.has(k) || onMineCell(state, p) || inMineBody(state, p)) continue
        // 穴のまわりが、固い岩か自分の坑道のどちらかか: 空気・水・溶岩・落ちる砂利に面した所は掘らない
        const sealed = SIX.every(([x, y, z]) => {
          const n = p.offset(x, y, z)
          if (onMineCell(state, n)) return true
          const nb = bot.blockAt(n)
          return !!nb && nb.boundingBox === 'block' && !FALLS.test(nb.name)
        })
        if (sealed) out.push(p)
      }
    }
  }
  return out.slice(0, Math.max(0, need))
}
