// 反射: ボットの近くの敵対モブには、判断を待たずにすぐ対処する。また、水中では頭を水面の上に保つ。
//
// 数物理ティックごとに、行動の実行中もステップの間も動く（ステップの間に立ち止まっていて倒された
// ことがある）。実行中の行動は中断し、止まるのを待ってから、戦う（武器があり、相手が1体で、
// クリーパーでないとき）か逃げる。結果は行動の履歴に入れ、判断する側が何が起きたかを見られるようにする。

import { round } from './observe.mjs'
import { reachableThreats, bestWeapon, fight, flee } from './primitives.mjs'

const REFLEX_RADIUS = 5
const CREEPER_RADIUS = 7 // 膨らみ始める前に逃げる
const CHECK_EVERY_TICKS = 4

function dangers (bot, state) {
  return reachableThreats(bot, state).filter(({ e, dist }) => dist <= (e.name === 'creeper' ? CREEPER_RADIUS : REFLEX_RADIUS))
}

// hooks.preempt(reason): 実行中の行動があれば止め、止まったら解決する。
// 実行中の行動を中断してはいけないときは false で解決する。
// hooks.record(entry): 行動の履歴に追加する。
export function startReflex (bot, state, hooks) {
  let ticks = 0
  let floating = false
  bot.on('physicsTick', () => {
    // Mineflayer は自分では泳がない: 水中で何もしないでいると（ステップの間や、エージェントが
    // 止まっているとき）沈んで溺れた。経路移動中でなければジャンプを押し続けて水面にとどまる。
    const float = !!bot.entity?.isInWater && !bot.pathfinder.isMoving()
    if (float !== floating) {
      bot.setControlState('jump', float)
      floating = float
    }
    if (++ticks % CHECK_EVERY_TICKS || state.reflex || !bot.entity || bot.health <= 0) return
    const near = dangers(bot, state)
    if (near.length) react(near).catch((e) => console.log(`[reflex] エラー: ${e.message}`))
  })

  async function react (near) {
    state.reflex = true
    const t0 = Date.now()
    const { e, dist } = near[0]
    try {
      if (!(await hooks.preempt(`reflex: ${e.name} ${round(dist)}m away`))) return
      const weapon = bestWeapon(bot)
      const fightIt = weapon && e.name !== 'creeper' && near.length === 1
      const signal = new AbortController().signal
      let ok = true
      let result
      try {
        if (fightIt) {
          await bot.equip(weapon, 'hand')
          result = await fight(bot, e, signal)
        } else {
          result = await flee(bot, e, signal)
        }
      } catch (err) {
        ok = false
        result = `failed: ${err.message}`
      } finally {
        bot.clearControlStates()
      }
      const id = fightIt ? 'reflex_fight' : 'reflex_flee'
      console.log(`[reflex] ${id} ${e.name}（${round(dist)}m、近くに ${near.length} 体）: ${result}`)
      hooks.record({ action: id, ok, result: `${e.name}: ${result}`, seconds: round((Date.now() - t0) / 1000) })
    } finally {
      state.reflex = false
    }
  }
}
