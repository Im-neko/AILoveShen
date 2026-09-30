// 原木を掘ったあとのドロップの記録（docs/design/59_world_map.md 「別件」、原因を推測でなく見て決めるため）。
//
// 幹の上のほうを掘ると「掘ったのに拾えなかった」失敗が続いた（2026-09-30 のログ: 27 回のうち 9 回、どれも
// 下から 3〜5 本目）。アイテムは消えておらず、次の行動で拾えている。待ち時間が足りないのか、足場の上に
// 立っていて高さの差で対象から外れるのか、葉に乗って届かないのかを決めるため、掘った直後から
// 0.5 / 1 / 2 秒後のまわりのアイテムの位置（ボットからの差）と、ボットの足元を logs/drop-watch.jsonl に残す。
// 行動を遅くしない（タイマーで見るだけ）。

import { appendFileSync, mkdirSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const DEFAULT_FILE = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..', 'logs', 'drop-watch.jsonl')
export const LOOKS_MS = [500, 1000, 2000]
const GIVE_UP_MS = 15000 // 行動が終わらなくても、これで書く
const RADIUS = 10
const SHOWN = 6

const one = (n) => Math.round(n * 10) / 10

export function look (bot) {
  const me = bot.entity.position
  const items = Object.values(bot.entities)
    .filter((e) => e !== bot.entity && e.name === 'item' && e.position && e.position.distanceTo(me) <= RADIUS)
    .map((e) => ({ dx: one(e.position.x - me.x), dy: one(e.position.y - me.y), dz: one(e.position.z - me.z), d: one(e.position.distanceTo(me)), ground: !!e.onGround }))
    .sort((a, b) => a.d - b.d)
    .slice(0, SHOWN)
  return { items }
}

// 掘った直後に呼ぶ。返す finish(outcome) を、行動が終わったときに呼ぶ（成功・失敗の文）。
// schedule / write は、テストで差し替える
const later = (fn, ms) => setTimeout(fn, ms).unref?.()

export function watchDrop (bot, { block, pos }, { schedule = later, write = record } = {}) {
  const at = bot.entity.position
  const me = { x: at.x, y: at.y, z: at.z } // 位置は動く物なので写しを持つ
  const scaffold = bot.blockAt?.(at.offset(0, -1, 0))?.name ?? null
  const rec = {
    block,
    pos: [pos.x, pos.y, pos.z],
    bot: [one(me.x), one(me.y), one(me.z)],
    log_above_bot: one(pos.y - me.y),
    on_ground: !!bot.entity.onGround,
    below: scaffold,
    building: !!bot.pathfinder?.isBuilding?.(),
    looks: []
  }
  let outcome = null
  let looked = 0
  let written = false
  const flush = () => {
    if (written) return
    written = true
    write({ ...rec, outcome: outcome ?? 'unknown' })
  }
  const maybeFlush = () => { if (looked === LOOKS_MS.length && outcome !== null) flush() }
  for (const ms of LOOKS_MS) {
    schedule(() => {
      // block_after: 掘ったマスが本当に空いたか（サーバーが断って、手元だけ空気になったのではないか）
      rec.looks.push({ after_ms: ms, ...look(bot), bot_dy: one(bot.entity.position.y - me.y), block_after: bot.blockAt?.(pos)?.name ?? null })
      looked++
      maybeFlush()
    }, ms)
  }
  schedule(flush, GIVE_UP_MS)
  return {
    finish (result) {
      outcome = result
      maybeFlush()
    }
  }
}

export function record (rec, now = new Date(), file = process.env.DROP_WATCH_FILE ?? DEFAULT_FILE) {
  try {
    mkdirSync(path.dirname(file), { recursive: true })
    appendFileSync(file, JSON.stringify({ time: now.toISOString(), ...rec }) + '\n')
  } catch (e) {
    console.log(`[dropwatch] 記録を書けない: ${e.message}`)
  }
}
