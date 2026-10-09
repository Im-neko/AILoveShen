// 鉱石を取ったあとの穴を、その場で埋め直す（docs/design/134_backfill_and_stair_walls.md）。
// 穴が残ると、坑道の壁や地表の凹みで通路がわかりにくくなる。取った鉱石の穴だけを、手元の土か丸石で埋める
// （土や丸石を掘った跡は埋めない）。通路のセル（採掘場・家・建物の計画）は、通れなくなるので埋めない。

import pathfinderPkg from 'mineflayer-pathfinder'
import vec3Pkg from 'vec3'
import { inventoryCounts, nearbyEntities, isHostile } from './observe.mjs'
import { onMineCell } from './facilities.mjs'
import { onBuildCell } from './builds.mjs'
import { placeSeal } from './sealing.mjs'
import { walkTo as goto } from './move.mjs'

const { goals } = pathfinderPkg
const { Vec3 } = vec3Pkg

export const FILL_ITEMS = ['dirt', 'cobblestone', 'cobbled_deepslate'] // 手元から使う順（丸石は坑道を塞ぐ分に残す）
export const isOreBlock = (name) => /_ore$/.test(name ?? '')
const DANGER_RADIUS = 10 // 敵がこの内にいる間は埋めない
const MIN_HEALTH = 10
const REACH = 4

const fmt = (p) => `${p.x},${p.y},${p.z}`

// 埋めない理由（埋めてよければ ''）。通路のセルは理由なしで断る（'passage'）
export function backfillRefusal (bot, state, pos) {
  const here = bot.blockAt(pos)
  if (!here) return 'not loaded'
  if (here.boundingBox !== 'empty') return 'already filled'
  if (/water|lava/.test(here.name)) return `${here.name} is in it` // 水の穴は plug_hole の番（設計書 55）
  const home = state.home
  if (onMineCell(state, pos) || onBuildCell(state, pos) ||
    (home && pos.x >= home.min.x - 1 && pos.x <= home.max.x + 1 && pos.z >= home.min.z - 1 && pos.z <= home.max.z + 1 &&
      pos.y >= home.min.y && pos.y <= home.min.y + 3)) return 'passage'
  if (!FILL_ITEMS.some((n) => (inventoryCounts(bot)[n] ?? 0) > 0)) return 'no dirt or cobblestone to fill it with'
  if (bot.health < MIN_HEALTH) return 'low health'
  if (nearbyEntities(bot).some(({ e, dist }) => dist <= DANGER_RADIUS && isHostile(bot, e))) return 'a monster is near'
  return ''
}

// 鉱石を取った pos を埋める。stood: 掘る前に立っていたセル（鉱石が足元で、穴に落ちたとき、そこへ戻ってから置く）。
// 埋め直しは付け足しなので、失敗しても掘ったことは失敗にしない: 結果に足す文（'' なら何も足さない）を返す
export async function backfillHole (bot, state, pos, stood, signal) {
  const why = backfillRefusal(bot, state, pos)
  if (why === 'passage' || why === 'already filled') return ''
  if (why) return `; left the hole at ${fmt(pos)} open: ${why}`
  try {
    const feet = bot.entity.position.floored()
    const inHole = feet.x === pos.x && feet.z === pos.z && Math.abs(feet.y - pos.y) <= 1
    if (inHole) await goto(bot, new goals.GoalBlock(stood.x, stood.y, stood.z), signal)
    else if (bot.entity.position.offset(0, bot.entity.height * 0.9, 0).distanceTo(new Vec3(pos.x + 0.5, pos.y + 0.5, pos.z + 0.5)) > REACH) {
      await goto(bot, new goals.GoalNear(pos.x, pos.y, pos.z, 3), signal)
    }
    const name = FILL_ITEMS.find((n) => (inventoryCounts(bot)[n] ?? 0) > 0)
    if (!name) return `; left the hole at ${fmt(pos)} open: no dirt or cobblestone to fill it with`
    if (await placeSeal(bot, pos, signal, [name])) {
      const record = (state.dug ?? []).find((d) => d.x === pos.x && d.y === pos.y && d.z === pos.z && !d.plugged)
      if (record) record.plugged = true
      return `; filled the hole at ${fmt(pos)} with ${name}`
    }
    return `; could not fill the hole at ${fmt(pos)} (nothing solid next to it to place against)`
  } catch (e) {
    if (signal.aborted) throw e
    return `; could not fill the hole at ${fmt(pos)}: ${e.message}`
  }
}
