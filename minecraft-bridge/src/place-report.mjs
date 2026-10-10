// サーバーにブロックの設置を断られた瞬間の記録（整地で同じ列だけ何度も断られる原因を、推測でなく見て決めるため。
// docs/design/70_leveling_dirt.md）。
//
// 置く先のセル、基準にしたブロック、ボットの位置と目の高さからの距離、そのセルの近くのブロックと生き物を、
// ブリッジのログと logs/place-refusals.jsonl に残す。置く動きは何も変えない。

import { appendFileSync, mkdirSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const DEFAULT_FILE = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..', 'logs', 'place-refusals.jsonl')
const NEAR_ENTITY = 3

const one = (n) => Math.round(n * 100) / 100
const xyz = (p) => ({ x: p.x, y: p.y, z: p.z })

// 呼び出し元の印: スタックから、経路探索の足場積みか、ブリッジの関数（jumpAndPlace など）かを読む
export function callerOf (stack) {
  const lines = String(stack ?? '').split('\n').slice(1)
  if (lines.some((l) => l.includes('mineflayer-pathfinder'))) return 'pathfinder'
  for (const l of lines) {
    if (l.includes('place-report')) continue
    const m = l.match(/at (?:async )?([\w$.]+) \(.*\/src\//)
    if (m) return m[1]
  }
  return null
}

export function snapshot (bot, dest, against, error, caller = null, timing = null) {
  const me = bot.entity.position
  const eye = me.offset(0, bot.entity.eyeHeight ?? 1.62, 0)
  const center = dest.offset(0.5, 0.5, 0.5)
  const around = {}
  for (const [dx, dy, dz] of [[0, 1, 0], [0, -1, 0], [1, 0, 0], [-1, 0, 0], [0, 0, 1], [0, 0, -1]]) {
    around[`${dx},${dy},${dz}`] = bot.blockAt(dest.offset(dx, dy, dz))?.name ?? null
  }
  const entities = Object.values(bot.entities ?? {})
    .filter((e) => e !== bot.entity && e.position && e.position.distanceTo(center) <= NEAR_ENTITY)
    .map((e) => ({ name: e.name ?? e.type, id: e.id, at: { x: one(e.position.x), y: one(e.position.y), z: one(e.position.z) } }))
  return {
    at: new Date().toISOString(),
    error: String(error?.message ?? error).slice(0, 160),
    dest: xyz(dest),
    dest_block: bot.blockAt(dest)?.name ?? null,
    against: against ? { ...xyz(against.position), name: against.name } : null,
    around,
    bot: { x: one(me.x), y: one(me.y), z: one(me.z), on_ground: bot.entity.onGround, in_water: !!bot.entity.isInWater, standing_in_dest: me.floored().equals(dest) },
    eye_to_dest: one(eye.distanceTo(center)),
    held: bot.heldItem?.name ?? null,
    caller,
    timing,
    entities
  }
}

export function record (snap, file = process.env.PLACE_REFUSALS_FILE ?? DEFAULT_FILE) {
  console.log(`[place] 置けなかった: ${JSON.stringify(snap)}`)
  try {
    mkdirSync(path.dirname(file), { recursive: true })
    appendFileSync(file, `${JSON.stringify(snap)}\n`)
  } catch (e) {
    console.log(`[place] 記録できなかった: ${e.message}`)
  }
}

// bot.placeBlock を包み、サーバーに断られた（置けなかった・答えがなかった）ときに、どこから呼ばれても
// 記録する（記録は整地の 1 か所にしか付いておらず、家の戸口の穴埋めで断られた 2026-10-01 19:54 は残らなかった）。
// 置く動きは何も変えない: 記録して同じエラーを投げ直す
const REFUSED = /Server (refused|did not answer)/
const TRAIL_TICKS = 14

// 断られた瞬間の高さは、置こうとした時の高さではない（呼んでから packet を送るまで、向きを合わせる 1 tick 以上待つ）。
// 「呼んだ時」「packet を送った時」の足の高さと、直近の物理 tick の高さ（断られた時刻からの ms）を残す
export function timingOf (dest, call, sent, trail, now) {
  const rel = (y) => one(y - dest.y)
  return {
    call_y: rel(call.y),
    send_y: sent ? rel(sent.y) : null,
    ms_call_to_send: sent ? sent.t - call.t : null,
    ms_send_to_refusal: sent ? now - sent.t : null,
    trail: trail.map((p) => [p.t - now, rel(p.y)])
  }
}

export function installPlaceReport (bot, file) {
  const placeBlock = bot.placeBlock.bind(bot)
  const trail = []
  let lastSend = null
  bot.on?.('physicsTick', () => {
    trail.push({ t: Date.now(), y: bot.entity.position.y })
    if (trail.length > TRAIL_TICKS) trail.shift()
  })
  if (bot._client?.write) {
    const write = bot._client.write.bind(bot._client)
    bot._client.write = (name, ...args) => {
      if (name === 'block_place') lastSend = { t: Date.now(), y: bot.entity.position.y }
      return write(name, ...args)
    }
  }
  bot.placeBlock = async (referenceBlock, faceVector, ...rest) => {
    const caller = callerOf(new Error().stack)
    const call = { t: Date.now(), y: bot.entity.position.y }
    lastSend = null
    try {
      return await placeBlock(referenceBlock, faceVector, ...rest)
    } catch (e) {
      if (REFUSED.test(String(e?.message))) {
        try {
          const dest = referenceBlock.position.plus(faceVector)
          record(snapshot(bot, dest, referenceBlock, e, caller, timingOf(dest, call, lastSend, trail, Date.now())), file)
        } catch (reportError) {
          console.log(`[place] 記録を作れなかった: ${reportError.message}`)
        }
      }
      throw e
    }
  }
}
