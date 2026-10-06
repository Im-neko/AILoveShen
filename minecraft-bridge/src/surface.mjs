// 地下から地上に出る道。洞窟の中では松明の材料（石炭と棒）が集められないことが多いので、松明が足りなければ
// 地上に出て、家で作る（2026-10-03、user: 「洞窟のなかでは松明の材料が集められないことの方が多い。足りない時は
// 地上に出るようにしてほしい」。深い地下で持ち物が空のまま、松明 128 本を作ろうとしていた）
import { isUnderground, UNDERGROUND_BELOW } from './observe.mjs'

// 地上に立っていた最後の場所を覚える（下りた所 = 戻れる道）。観察のたびに呼ぶ
export function trackSurface (bot, state, underground) {
  if (underground || !bot.entity.onGround || bot.entity.position.y < UNDERGROUND_BELOW) return
  const p = bot.entity.position
  state.lastSurface = { x: Math.floor(p.x), y: Math.floor(p.y), z: Math.floor(p.z) }
}

// 地上に出る道（行き先の候補）: 最後に地上にいた場所、設計した鉱山の入口、家の玄関の外。今より高い所だけ。
// 最後の場所が先で、あとは近い順
export function surfaceExits (bot, state) {
  const me = bot.entity.position
  const flat = (p) => Math.hypot(p.x - me.x, p.z - me.z)
  const others = []
  for (const fac of Object.values(state.facilities ?? {})) if (fac.kind === 'mine' && fac.entrance) others.push(fac.entrance)
  if (state.home?.outside) others.push(state.home.outside)
  const higher = (p) => p.y > me.y + 3
  const seen = new Set()
  return [...(state.lastSurface ? [state.lastSurface] : []), ...others.sort((a, b) => flat(a) - flat(b))]
    .filter(higher)
    .filter((p) => { const k = `${Math.floor(p.x)},${Math.floor(p.y)},${Math.floor(p.z)}`; return !seen.has(k) && seen.add(k) })
    .map((p) => ({ x: Math.floor(p.x), y: Math.floor(p.y), z: Math.floor(p.z) }))
}

export { isUnderground }
