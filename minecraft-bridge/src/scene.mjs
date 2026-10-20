// 失敗した生の bot の技に添える、そのときの現場（設計書 193）。ブリッジの判断は入れず、世界の事実だけを書く:
// 立っている所、向き、手の物、足元・頭・まわりのブロック、視線の先、近くのもの。
// 置けなかった・掘れなかったときの原因（自分の足元のセルに置こうとした、向きが違う、手に持っていない）が、記録から読める。

import { lookingAt } from './sightline.mjs'

const round = (n, k = 10) => Math.round(n * k) / k
const nameOf = (b) => b?.name ?? 'unloaded'

export function sceneOf (bot) {
  const p = bot.entity.position
  const f = p.floored()
  const at = (dx, dy, dz) => nameOf(bot.blockAt(f.offset(dx, dy, dz)))
  const ray = lookingAt(bot, 5)
  const near = Object.values(bot.entities ?? {})
    .filter((e) => e !== bot.entity && e.position && e.position.distanceTo(p) <= 3)
    .map((e) => e.name ?? e.type)
    .slice(0, 5)
  return {
    position: { x: round(p.x), y: round(p.y), z: round(p.z) },
    yaw: round(bot.entity.yaw ?? 0, 100),
    pitch: round(bot.entity.pitch ?? 0, 100),
    on_ground: Boolean(bot.entity.onGround),
    sneaking: Boolean(bot.controlState?.sneak),
    held: bot.heldItem?.name ?? null,
    below: at(0, -1, 0),
    feet: at(0, 0, 0),
    head: at(0, 1, 0),
    above: at(0, 2, 0),
    around_feet: { north: at(0, 0, -1), south: at(0, 0, 1), east: at(1, 0, 0), west: at(-1, 0, 0) },
    looking_at: ray ? `${ray.name} at ${ray.x},${ray.y},${ray.z}` : null,
    within_3m: near
  }
}
