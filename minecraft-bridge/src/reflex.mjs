// 反射: 敵に襲われたら、判断を待たずにすぐ動く。また、水中では頭を水面の上に保つ（設計書 28）。
//
// 数物理ティックごとに、行動の実行中もステップの間も動く（ステップの間に立ち止まっていて倒された
// ことがある）。きっかけはコード: 近くの敵（5 m、クリーパー 7 m）、ダメージを受けた（見えない相手、
// 遠くからの矢も）、16 m 以内の敵がこちらに向かってくる。まずコードの規則（戦うか逃げる）で動き始め、
// state.danger に選択肢を出す。Python の見張り（Jev）が別の選択肢を POST /reflex で選べば、そちらに
// 切り替える。結果は行動の履歴に入れ、判断する側が何が起きたかを見られるようにする。
import { round, bearing, nearbyEntities, isHostile, canSee } from './observe.mjs'
import { reachableThreats, bestWeapon, fight, flee } from './primitives.mjs'
import { enterHome, isInside } from './home.mjs'

const REFLEX_RADIUS = 5
const CREEPER_RADIUS = 7 // 膨らみ始める前に逃げる
const ATTACKER_RADIUS = 16 // ダメージを受けたとき、相手を探す範囲（見えなくても、高さが違っても）
const APPROACH_RADIUS = 16
const APPROACH_METERS = 3 // APPROACH_WINDOW_MS の間にこれだけ近づいたら「向かってくる」
const APPROACH_WINDOW_MS = 2000
const KEEP_DISTANCE = 10 // 距離を取る: この距離まで離れて様子を見る
const HOME_REACH = 48 // 家に入る: この距離までなら選べる
const CHECK_EVERY_TICKS = 4

export const CHOICES = ['fight', 'flee', 'go_home', 'keep_distance', 'ignore']

// モブ自身がこちらへ動いた距離（ボットが近づいた分は数えない）
export function movedToward (from, to, me) {
  const toward = me.minus(from)
  const len = Math.hypot(toward.x, toward.z)
  if (!len) return 0
  const moved = to.minus(from)
  return (moved.x * toward.x + moved.z * toward.z) / len
}

function dangers (bot, state) {
  return reachableThreats(bot, state).filter(({ e, dist }) => dist <= (e.name === 'creeper' ? CREEPER_RADIUS : REFLEX_RADIUS))
}

// ダメージの相手: 近くの敵対モブで一番近いもの（見えなくても、高さが違っても）
function attackerOf (bot) {
  return nearbyEntities(bot).filter(({ e, dist }) => dist <= ATTACKER_RADIUS && isHostile(bot, e))[0] ?? null
}

// 選べるもの（ブリッジが今できるものだけ）。家の中にいるなら外に出ない（夜の決まり）
export function optionsFor (bot, state, target) {
  const home = state.home
  const inside = isInside(bot, home)
  if (inside) return ['fight', 'ignore']
  const homeNear = home && bot.entity.position.distanceTo(home.door) <= HOME_REACH
  return [
    ...(target?.name === 'creeper' ? [] : ['fight']),
    'flee',
    ...(homeNear ? ['go_home'] : []),
    'keep_distance',
    'ignore'
  ]
}

// コードの規則（Jev の答えを待つ間と、Jev がないとき）: 武器があり相手が 1 体でクリーパーでなければ戦う
export function ruleChoice (bot, target, count, options) {
  const fightIt = bestWeapon(bot) && target.name !== 'creeper' && count === 1
  const choice = fightIt ? 'fight' : 'flee'
  return options.includes(choice) ? choice : options[0]
}

// hooks.preempt(reason): 実行中の行動があれば止め、止まったら解決する。
// 実行中の行動を中断してはいけないときは false で解決する。
// hooks.record(entry): 行動の履歴に追加する。
export function startReflex (bot, state, hooks) {
  let ticks = 0
  let floating = false
  let ids = 0
  let hurtAt = 0
  let lastHealth = null
  const seen = new Map() // モブの id -> [{ t, pos }]（向かってくるかを見る）

  bot.on('entityHurt', (entity) => {
    if (entity !== bot.entity) return
    const damage = lastHealth != null ? round(lastHealth - bot.health) : null
    state.lastHurt = { at: Date.now(), damage }
    hurtAt = Date.now()
  })
  bot.on('health', () => { lastHealth = bot.health })

  bot.on('physicsTick', () => {
    // Mineflayer は自分では泳がない: 水中で何もしないでいると（ステップの間や、エージェントが
    // 止まっているとき）沈んで溺れた。経路移動中でなければジャンプを押し続けて水面にとどまる。
    const float = !!bot.entity?.isInWater && !bot.pathfinder.isMoving()
    if (float !== floating) {
      bot.setControlState('jump', float)
      floating = float
    }
    if (++ticks % CHECK_EVERY_TICKS || state.reflex || !bot.entity || bot.health <= 0) return
    const trigger = triggerNow()
    if (trigger) react(trigger).catch((e) => console.log(`[reflex] エラー: ${e.message}`))
  })

  // きっかけ → { why, target, count } か null
  function triggerNow () {
    const near = dangers(bot, state)
    if (near.length) return { why: 'close', target: near[0].e, count: near.length }
    if (Date.now() - hurtAt < 1000) {
      hurtAt = 0
      const a = attackerOf(bot)
      if (a) return { why: 'hurt', target: a.e, count: 1 }
    }
    const coming = approaching()
    if (coming) return { why: 'approaching', target: coming, count: 1 }
    return null
  }

  function approaching () {
    const now = Date.now()
    const inside = isInside(bot, state.home)
    const me = bot.entity.position
    for (const { e, dist } of nearbyEntities(bot)) {
      if (dist > APPROACH_RADIUS || !isHostile(bot, e)) continue
      const track = (seen.get(e.id) ?? []).filter((s) => now - s.t <= APPROACH_WINDOW_MS)
      track.push({ t: now, pos: e.position.clone() })
      seen.set(e.id, track)
      if (inside || !canSee(bot, e)) continue // 家の中は壁が守る
      if (movedToward(track[0].pos, e.position, me) >= APPROACH_METERS) {
        seen.delete(e.id)
        return e
      }
    }
    for (const id of seen.keys()) if (!bot.entities[id]) seen.delete(id)
    return null
  }

  async function perform (choice, target, signal) {
    if (choice === 'fight') {
      const weapon = bestWeapon(bot)
      if (weapon) await bot.equip(weapon, 'hand')
      return await fight(bot, target, signal)
    }
    if (choice === 'flee') return await flee(bot, target, signal)
    if (choice === 'keep_distance') return await flee(bot, target, signal, KEEP_DISTANCE)
    if (choice === 'go_home') {
      await enterHome(bot, state.home, signal)
      return 'inside the house with the door closed'
    }
    return 'ignored (went on)'
  }

  async function react ({ why, target, count }) {
    state.reflex = true
    const t0 = Date.now()
    const dist = round(target.position.distanceTo(bot.entity.position))
    const options = optionsFor(bot, state, target)
    const rule = ruleChoice(bot, target, count, options)
    const danger = {
      id: ++ids,
      trigger: why,
      target: { id: target.id, name: target.name, distance_m: dist, direction: bearing(bot.entity.position, target.position), visible: canSee(bot, target) },
      count,
      options,
      rule,
      choice: rule,
      judged: null,
      next: null,
      controller: null,
      started: t0,
      health: round(bot.health)
    }
    state.danger = danger
    const steps = []
    try {
      if (!(await hooks.preempt(`reflex: ${target.name} ${dist}m away (${why})`))) return
      let choice = rule
      while (choice) {
        danger.choice = choice
        danger.controller = new AbortController()
        let result
        let ok = true
        try {
          result = await perform(choice, target, danger.controller.signal)
        } catch (err) {
          ok = false
          result = `failed: ${err.message}`
        } finally {
          bot.clearControlStates()
          bot.pathfinder.setGoal(null)
        }
        steps.push({ choice, ok, result })
        // Jev が別のものを選んでいれば、そちらに切り替える
        if (danger.next && danger.next !== choice) {
          choice = danger.next
          danger.next = null
          continue
        }
        break
      }
      const last = steps.at(-1)
      const by = danger.judged ? `jev ${danger.judged.choice} (${danger.judged.confidence})` : 'rule'
      const summary = steps.map((s) => `${s.choice}: ${s.result}`).join(' → ')
      console.log(`[reflex] ${why} ${target.name}（${dist}m、近くに ${count} 体、${by}）: ${summary}`)
      hooks.record({ action: `reflex_${last.choice}`, ok: last.ok, result: `${target.name} (${why}, ${by}): ${summary}`, seconds: round((Date.now() - t0) / 1000) })
    } finally {
      state.danger = null
      state.reflex = false
    }
  }
}

// POST /reflex: 見張り（Jev）が選んだものに切り替える → { accepted, why? }
export function steerReflex (state, { id, choice, confidence }) {
  const danger = state.danger
  if (!danger || danger.id !== Number(id)) return { accepted: false, why: 'the reflex has ended' }
  if (!danger.options.includes(choice)) return { accepted: false, why: `${choice} is not one of ${danger.options.join(', ')}` }
  danger.judged = { choice, confidence: round(Number(confidence ?? 0), 2) }
  if (choice !== danger.choice) {
    danger.next = choice
    danger.controller?.abort(new Error(`switched to ${choice}`))
  }
  return { accepted: true }
}

// /state に出す形（中の制御用のものは出さない）
export function dangerView (state) {
  const d = state.danger
  if (!d) return null
  return { id: d.id, trigger: d.trigger, target: d.target, count: d.count, options: d.options, rule: d.rule, choice: d.choice, judged: d.judged, health_at_start: d.health, seconds: round((Date.now() - d.started) / 1000) }
}
