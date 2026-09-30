// ベッドで寝ているモブ（村人など）を見分ける（docs/design/56_wake_sleeper.md）。
//
// 村の家のベッドは村人が使っていて、寝ている間は mineflayer が「the bed is occupied」で寝かせない。
// 寝ている村人は殴れば起きてベッドが空く。ここで、ベッドに寝ている相手と、そのベッドの位置を返す。

import { nearbyEntities } from './observe.mjs'

const SLEEPING_POSE = 2 // エンティティのポーズ（寝ている）
export const WAKE_RADIUS = 8

// 寝ているか: ポーズが「寝ている」か、寝ているベッドの位置（村人）がある。メタデータの読み方が分からない
// エンティティは、寝ていないとする
export function isSleeping (bot, e) {
  const keys = bot.registry?.entitiesByName?.[e.name]?.metadataKeys
  if (!keys || !e.metadata) return false
  return e.metadata[keys.indexOf('pose')] === SLEEPING_POSE || !!e.metadata[keys.indexOf('sleeping_pos')]
}

const isBed = (b) => !!b && b.name.endsWith('_bed')

// ベッドの上で寝ているモブ（プレイヤーは除く）: [{ e, dist, bed }]。bed は寝ている位置のベッドのブロック位置
export function sleepersInBeds (bot, radius = WAKE_RADIUS) {
  const out = []
  for (const { e, dist } of nearbyEntities(bot)) {
    if (e.type === 'player' || e === bot.entity || e.name === 'item' || dist > radius || !isSleeping(bot, e)) continue
    const at = e.position.floored()
    const bed = [at, at.offset(0, -1, 0)].find((p) => isBed(bot.blockAt(p)))
    if (bed) out.push({ e, dist, bed })
  }
  return out.sort((a, b) => a.dist - b.dist)
}

// このベッド（2 マスのどちらか）に寝ているモブ。なければ null
export function sleeperOf (bot, bedPos) {
  return sleepersInBeds(bot, WAKE_RADIUS * 2).find((s) => Math.abs(s.bed.x - bedPos.x) + Math.abs(s.bed.z - bedPos.z) <= 1 && s.bed.y === bedPos.y) ?? null
}
