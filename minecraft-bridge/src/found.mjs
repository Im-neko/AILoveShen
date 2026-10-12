// found(target, distance): 探しているもの（モブ、群系）が見つかった（設計書 176）。
// target が動物・モブの名前（sheep）なら、そのモブが SIGHT ブロック以内に読み込まれている。群系の名前（plains）なら、いま
// その群系に立っている（sunflower_plains、snowy_plains も plains）。ブリッジが世界から判定する。
import { biomeName } from './observe.mjs'

export const SIGHT = 48

// 群系名が target か、target で終わる（_ 区切り）か
export const biomeMatches = (name, target) => !!name && (name === target || name.endsWith(`_${target}`))

// target が何か: 'mob'、'biome'、見つからなければ null
export function foundKind (bot, target) {
  const r = bot.registry
  if (r?.entitiesByName?.[target]) return 'mob'
  const biomes = r?.biomesArray ?? Object.values(r?.biomes ?? {})
  if (biomes.some((b) => biomeMatches(b.name, target))) return 'biome'
  return null
}

// いま見つかっているか: { where } か null
export function foundNow (bot, target) {
  const kind = foundKind(bot, target)
  if (kind === 'biome') {
    const here = biomeName(bot)
    return biomeMatches(here, target) ? { where: `standing in ${here}` } : null
  }
  if (kind !== 'mob') return null
  const me = bot.entity.position
  let best = null
  for (const e of Object.values(bot.entities ?? {})) {
    if (e === bot.entity || e.name !== target || !e.position) continue
    const d = me.distanceTo(e.position)
    if (d <= SIGHT && (!best || d < best.d)) best = { d, e }
  }
  return best ? { where: `${target} ${Math.round(best.d)}m away at ${Math.round(best.e.position.x)},${Math.round(best.e.position.y)},${Math.round(best.e.position.z)}` } : null
}

// 探索の向きの並び（まだ見ていない土地の手前の群系 biome_ahead で）: 水（海・川・浜）の手前になる向きは後ろへ、探しているものが
// その群系の名前なら、その手前になる向きを前へ。0 なら同順位（あとは近い順）
const WATER_BIOME = /(^|_)(ocean|river|beach)$/
const waterAhead = (d) => (WATER_BIOME.test(d.biome_ahead ?? '') ? 1 : 0)
const targetAhead = (target, d) => (target && biomeMatches(d.biome_ahead, target) ? 1 : 0)
export const aheadOrder = (target, a, b) => (waterAhead(a) - waterAhead(b)) || (targetAhead(target, b) - targetAhead(target, a))
