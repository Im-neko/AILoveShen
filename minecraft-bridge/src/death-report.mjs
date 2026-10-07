// 死んだときの様子（docs/design/105）。死んだあとの目標の決定が、何で・どんな状況で死んだかを知って教訓にできるように、
// 受けたダメージを直近だけ覚えておき、死んだ瞬間に 1 件にまとめる。復活すると位置も時間帯も家の中に変わる
// （2026-10-07: 夜の外で死んで、復活後の状況は「家の中、体力 20」だった）ので、死ぬ前の様子はここでしか取れない。
import { dayPhase, isUnderground } from './observe.mjs'
import { isInside } from './home.mjs'

const WINDOW_MS = 90000 // 死ぬ前のこれだけの間のダメージを数える
const KEPT = 40

// 受けたダメージを 1 件覚える（state.recentDamage）。古いものは捨てる
export function noteDamage (state, entry) {
  const list = (state.recentDamage ??= [])
  list.push(entry)
  const cut = entry.at - WINDOW_MS
  while (list.length > KEPT || (list.length && list[0].at < cut)) list.shift()
}

// 原因の種類（"zombie 2.1m" → zombie、"unknown (no hostile ...)" → unknown、"fall (3 blocks per second)" → fall）
export const kindOf = (cause) => String(cause).replace(/ \(.*$/, '').replace(/ [\d.]+m$/, '')

// 死んだ瞬間の様子: { phase, place, from_home_m, seconds, health_before, damage: {種類: 合計}, hits, doing: [行動] }
export function deathReport (bot, state, now = Date.now()) {
  const recent = (state.recentDamage ?? []).filter((d) => now - d.at <= WINDOW_MS)
  const damage = {}
  for (const d of recent) damage[kindOf(d.cause)] = Math.round(((damage[kindOf(d.cause)] ?? 0) + d.lost) * 10) / 10
  const doing = []
  for (const d of recent) if (d.what && !doing.includes(d.what)) doing.push(d.what)
  const home = state.home
  return {
    phase: dayPhase(bot.time.timeOfDay),
    place: home && isInside(bot, home) ? 'home' : isUnderground(bot) ? 'underground' : 'outside',
    from_home_m: home?.door ? Math.round(bot.entity.position.distanceTo(home.door)) : null,
    seconds: recent.length ? Math.round((now - recent[0].at) / 1000) : 0,
    health_before: recent.length ? Math.round((recent[0].health + recent[0].lost) * 10) / 10 : null,
    damage,
    hits: recent.length,
    doing
  }
}
