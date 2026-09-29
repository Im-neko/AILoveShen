// 断られた・着けなかった手の台帳（docs/design/51）。
//
// 同じ場所への同じ手（動作の種類 + 座標）は、断られたり経路がなかったりしたら、次も同じ結果になる。
// それでも配信者（LLM）は、覚えた場所や検索の結果から同じ座標をまた選び、1 手（LLM の 7 秒前後）を
// 無駄にした（2026-09-29: 家の壁の原木を掘れと 2 回、同じ goto を 3 回）。ここに 1 回で載せ、期限
// （NO_PATH_MS と同じ 5 分）まで、候補・検索の結果・道具の呼び出しから外す。理由は配信者に見せる。
import { NO_PATH_MS } from './move.mjs'

const cellKey = (p) => `${Math.floor(p.x)},${Math.floor(p.y)},${Math.floor(p.z)}`
const MAX_SHOWN = 8

// 動作の種類: 掘れなかった場所は、そこへ歩くのを妨げない
const GROUPS = {
  goto: 'walk', goto_memory: 'walk', goto_pos: 'walk', sleep_at: 'walk', explore: 'walk',
  dig: 'dig', dig_down: 'dig',
  place: 'place', place_at: 'place'
}
export const groupOf = (verb) => GROUPS[verb] ?? verb

// 同じことをしても同じ結果になる理由だけ（一時的な理由 — 反射、別の行動、ダメージ、遠すぎる、
// 持っていない — は載せない）
const FUTILE = [
  /no path/i,
  /took to long to decide path/i,
  /could not walk/i,
  /will not (dig|place)/i,
  /nothing to dig/i,
  /is not ripe/i,
  /is taken by/i,
  /cannot be (dug|placed)/i
]

export function isFutile (reason) {
  return FUTILE.some((re) => re.test(String(reason ?? '')))
}

export function noteRefusal (state, verb, pos, reason, now = Date.now()) {
  if (!pos || !Number.isFinite(pos.x) || !isFutile(reason)) return false
  state.refusals ??= new Map()
  state.refusals.set(`${groupOf(verb)}:${cellKey(pos)}`, {
    verb: groupOf(verb), pos: { x: Math.floor(pos.x), y: Math.floor(pos.y), z: Math.floor(pos.z) },
    reason: String(reason).replace(/^(refused|failed): /, '').slice(0, 160), until: now + NO_PATH_MS, at: now
  })
  return true
}

export function refusalAt (state, verb, pos, now = Date.now()) {
  if (!pos) return null
  const key = `${groupOf(verb)}:${cellKey(pos)}`
  const r = state.refusals?.get(key)
  if (!r) return null
  if (r.until <= now) { state.refusals.delete(key); return null }
  return r
}

// /state と道具のプロンプトに出す: 新しい順、最大 MAX_SHOWN 件
export function listRefusals (state, now = Date.now()) {
  const out = []
  for (const [key, r] of state.refusals ?? []) {
    if (r.until <= now) { state.refusals.delete(key); continue }
    out.push({ tool: r.verb, ...r.pos, reason: r.reason, minutes_ago: Math.max(0, Math.round((now - r.at) / 60000)) })
  }
  return out.sort((a, b) => a.minutes_ago - b.minutes_ago).slice(0, MAX_SHOWN)
}
