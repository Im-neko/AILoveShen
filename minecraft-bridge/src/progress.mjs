// 実行中の行動の進み具合: 見張り（Jev の質問）と Gemini が見る共通の状態の action の欄。
//
// 進んでいるかは、行動の種類ごとの規則では判定しない。どの行動にも同じ信号（動いた距離、目標までの
// 距離、経路が見つからない回数、サーバーに位置を戻された回数、掘りの進み、持ち物と体力の変化）を
// 並べ、判定は質問に答える側に任せる。
import { round } from './observe.mjs'

const SAMPLE_MS = 250
const WINDOW_MS = 5000

const totalItems = (bot) => bot.inventory.items().reduce((n, i) => n + i.count, 0)

// 行動が向かう位置（あれば）: ブロック、エンティティ、場所
function targetOf (bot, c) {
  if (c.pos) return c.pos
  if (c.entityId !== undefined) return bot.entities[c.entityId]?.position ?? null
  return null
}

export function startTracking (bot, c, limitMs) {
  const started = Date.now()
  const startPos = bot.entity.position.clone()
  const startItems = totalItems(bot)
  const startHealth = bot.health
  const startTarget = targetOf(bot, c)
  const startDistance = startTarget ? startPos.distanceTo(startTarget) : null
  const samples = [{ t: started, pos: startPos }]
  const counts = { positionResets: 0, noPath: 0, pathUpdates: 0 }
  const onForced = () => { counts.positionResets++ }
  const onPath = (r) => {
    counts.pathUpdates++
    if (r?.status === 'noPath' || r?.status === 'timeout') counts.noPath++
  }
  bot.on('forcedMove', onForced)
  bot.on('path_update', onPath)
  const timer = setInterval(() => {
    const now = Date.now()
    samples.push({ t: now, pos: bot.entity.position.clone() })
    while (samples.length > 1 && now - samples[0].t > WINDOW_MS + SAMPLE_MS) samples.shift()
  }, SAMPLE_MS)

  const movedSince = (ms) => {
    const now = Date.now()
    const past = samples.find((s) => now - s.t <= ms) ?? samples[0]
    return round(bot.entity.position.distanceTo(past.pos))
  }

  return {
    stop () {
      clearInterval(timer)
      bot.off('forcedMove', onForced)
      bot.off('path_update', onPath)
    },
    // 共通の状態の action の欄（要約）
    snapshot () {
      const target = targetOf(bot, c)
      const dig = bot.targetDigBlock
      return {
        verb: c.verb,
        target: c.target ?? null,
        elapsed_s: round((Date.now() - started) / 1000),
        limit_s: round(limitMs / 1000),
        moved_1s_m: movedSince(1000),
        moved_5s_m: movedSince(WINDOW_MS),
        moved_total_m: round(bot.entity.position.distanceTo(startPos)),
        target_distance_m: target ? { at_start: startDistance === null ? null : round(startDistance), now: round(bot.entity.position.distanceTo(target)) } : null,
        path: { moving: !!bot.pathfinder?.isMoving?.(), updates: counts.pathUpdates, no_path: counts.noPath },
        position_resets: counts.positionResets,
        digging: dig ? { block: dig.name, at: { x: dig.position.x, y: dig.position.y, z: dig.position.z } } : null,
        items_gained: totalItems(bot) - startItems,
        health_change: round(bot.health - startHealth)
      }
    }
  }
}
