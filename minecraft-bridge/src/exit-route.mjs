// 家から出る道（2026-10-11: 玄関のセルに草ブロックが残り、まぐさの板材で高さが 1 マスしかない家の中に閉じ込められ、
// 何を言われても出られなかった。設計書 163）。
//
// 出る道は 3 通り: 玄関を通る（door）、壁を掘る（wall）、行動の経路探しに任せる（walk: 歩いて出られるとき）。
// どれもなければ stuck で、何が玄関をふさいでいるかを返す。
import { doorRouteOk, exitSpots, EXIT_DROP } from './home.mjs'
import { closedSpace } from './reach.mjs'

// 玄関のセル（下と上の 2 マス）と外のセル（足と頭）が通れるか。ドアは開け閉めして通る
const WALKABLE = /_(door|fence_gate)$/
const passable = (bot, p) => {
  const b = bot.blockAt(p)
  return !!b && (b.boundingBox === 'empty' || WALKABLE.test(b.name))
}

// 玄関から外へ歩いて出るのをふさいでいるもの（なければ ''）
export function doorBlocker (bot, home) {
  for (const [what, base] of [['door', home.door], ['outside', home.outside]]) {
    if (!base) continue
    for (const dy of [0, 1]) {
      const p = base.offset(0, dy, 0)
      if (!passable(bot, p)) return `${what} cell ${p.x},${p.y},${p.z} holds ${bot.blockAt(p)?.name ?? 'an unloaded block'}`
    }
  }
  return ''
}

// 家の中から出る道: { route: 'door' | 'wall' | 'walk' | 'stuck', spot?, blocked? }
export function leaveRoute (bot, home) {
  const blocked = doorBlocker(bot, home)
  if (!blocked && doorRouteOk(bot, home)) return { route: 'door' }
  const spot = exitSpots(bot, home, [], { drop: EXIT_DROP })[0] ?? null
  if (spot) return { route: 'wall', spot }
  if (bot.entity && !closedSpace(bot)) return { route: 'walk' }
  return { route: 'stuck', blocked }
}
