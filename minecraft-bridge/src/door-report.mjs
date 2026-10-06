// 家のドアを開け閉めしたときの記録（ドアが開かない原因を、推測でなく見て決めるため。2026-10-04: 玄関の前に
// 立って右クリックしても 2 秒たってもドアが動かず、`door did not open` が 28 回続いた）。
//
// 試した手（既定の右クリック、スニークを外して素手で面を指定、上の段を押す）ごとの結果、ドアのブロックの状態
// （開閉）、サーバーから届いたドアのマスの更新、ボットの位置・向き・スニーク・持ち物、視線の先のブロックを、
// ブリッジのログと logs/door-toggles.jsonl に残す。DOOR_TOGGLES_FILE で場所を変えられる。

import { appendFileSync, mkdirSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const DEFAULT_FILE = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..', 'logs', 'door-toggles.jsonl')

const one = (n) => Math.round(n * 100) / 100

// ドアのマス（下の段と上の段）に届く更新を集める。stop() で止める
export function watchDoor (bot, door) {
  const updates = []
  const onUpdate = (oldBlock, newBlock) => {
    const p = newBlock?.position
    if (!p || p.x !== door.x || p.z !== door.z || (p.y !== door.y && p.y !== door.y + 1)) return
    updates.push({ y: p.y, from: oldBlock?.name ?? null, to: newBlock.name, open: newBlock.getProperties?.().open ?? null })
  }
  bot.on('blockUpdate', onUpdate)
  return { updates, stop: () => bot.removeListener('blockUpdate', onUpdate) }
}

export function snapshot (bot, door, wanted, tries, updates, opened) {
  const me = bot.entity.position
  const eye = me.offset(0, bot.entity.eyeHeight ?? 1.62, 0)
  const block = bot.blockAt(door)
  const upper = bot.blockAt(door.offset(0, 1, 0))
  const ray = bot.blockAtCursor?.(6)
  return {
    at: new Date().toISOString(),
    wanted: wanted ? 'open' : 'close',
    opened,
    tries,
    door: { name: block?.name ?? null, props: block?.getProperties?.() ?? null, upper: upper?.name ?? null, upper_props: upper?.getProperties?.() ?? null },
    updates,
    bot: {
      x: one(me.x), y: one(me.y), z: one(me.z), yaw: one(bot.entity.yaw), pitch: one(bot.entity.pitch),
      on_ground: bot.entity.onGround, sneak: !!bot.controlState?.sneak, sprint: !!bot.controlState?.sprint,
      vehicle: !!bot.vehicle, gamemode: bot.game?.gameMode ?? null
    },
    eye_to_door: one(eye.distanceTo(door.offset(0.5, 0.5, 0.5))),
    held: bot.heldItem?.name ?? null,
    looking_at: ray ? { name: ray.name, x: ray.position.x, y: ray.position.y, z: ray.position.z } : null
  }
}

export function reportDoor (bot, door, wanted, tries, updates, opened) {
  const record = snapshot(bot, door, wanted, tries, updates, opened)
  console.log(`[door] ${opened ? 'ok' : 'FAILED'} ${JSON.stringify(record)}`)
  try {
    const file = process.env.DOOR_TOGGLES_FILE || DEFAULT_FILE
    mkdirSync(path.dirname(file), { recursive: true })
    appendFileSync(file, JSON.stringify(record) + '\n')
  } catch {
    // 記録できなくても、ドアの開け閉めは続ける
  }
  return record
}
