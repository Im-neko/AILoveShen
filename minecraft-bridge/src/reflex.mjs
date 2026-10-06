// 反射: 敵に襲われたら、判断を待たずにすぐ動く。また、水中では頭を水面の上に保つ（設計書 28）。
//
// 数物理ティックごとに、行動の実行中もステップの間も動く（ステップの間に立ち止まっていて倒された
// ことがある）。きっかけはコード: 近くの敵（5 m、クリーパー 7 m）、ダメージを受けた（見えない相手、
// 遠くからの矢も）、16 m 以内の敵がこちらに向かってくる。まずコードの規則（戦うか逃げる）で動き始め、
// state.danger に選択肢を出す。Python の見張り（Jev）が別の選択肢を POST /reflex で選べば、そちらに
// 切り替える。結果は行動の履歴に入れ、判断する側が何が起きたかを見られるようにする。
import { riding } from './boat.mjs'
import { round, bearing, nearbyEntities, isHostile, canSee } from './observe.mjs'
import { reachableThreats, bestWeapon, fight, flee } from './primitives.mjs'
import { enterHome, isInside } from './home.mjs'
import { inBurrow } from './burrow.mjs'
import { breathTrigger, surface } from './breath.mjs'
import { isSwimming } from './swim.mjs'
import { reachLand } from './land.mjs'

const REFLEX_RADIUS = 5
const CREEPER_RADIUS = 7 // 膨らみ始める前に逃げる
const ATTACKER_RADIUS = 16 // ダメージを受けたとき、相手を探す範囲（見えなくても、高さが違っても）
const APPROACH_RADIUS = 16
const APPROACH_METERS = 3 // APPROACH_WINDOW_MS の間にこれだけ近づいたら「向かってくる」
const APPROACH_WINDOW_MS = 2000
const KEEP_DISTANCE = 10 // 距離を取る: この距離まで離れて様子を見る
const HOME_REACH = 48 // 家に入る: この距離までなら選べる
const CHECK_EVERY_TICKS = 4
const BREATH_EVERY_MS = 500 // 息の道を調べる間隔（幅優先探索は軽くない）
// 同じ相手から逃げても離れられない・戦っても倒せないまま、また発火する繰り返し（2026-10-01: 壁際のスケルトンから
// 20 分、6 秒ごとに「逃げる」）。2 回続けたら逃げるのをやめて戦い、4 回続けたらその相手には 1 分反応しない
export const LOOP_FIGHT = 2
export const LOOP_GIVE_UP = 4
export const MUTE_MS = 60000
export const FIGHT_MS = 20000 // 反射の戦いはこれ以上続けない（また発火すれば、続きは数える）
// 武器がなくても殴り合ってよい相手と体力（docs/design/89: 夜に家も道具もないとき、逃げ続けるだけにしない）。クリーパーは近づかない。
// 体力が FIST_MIN_HEALTH を切ったら殴るのをやめて逃げる（素手は弱い）
export const FIST_MOBS = new Set(['zombie', 'husk', 'zombie_villager', 'drowned', 'spider', 'cave_spider', 'skeleton', 'stray'])
export const FIST_START_HEALTH = 12
export const FIST_MIN_HEALTH = 8

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

// 選べるもの（ブリッジが今できるものだけ）。家の中にいるなら外に出ない（夜の決まり）。クリーパーは
// 戦わず、家へも連れて帰らない
export function optionsFor (bot, state, target) {
  const home = state.home
  // 家の中と、塞いだ穴の中（docs/design/61）は、外に出ない
  const inside = isInside(bot, home) || inBurrow(bot)
  if (inside) return ['fight', 'ignore']
  const homeNear = home && bot.entity.position.distanceTo(home.door) <= HOME_REACH
  // クリーパーは追ってきて、壁のそばで膨らむ: 家へ連れて帰ると壁と床を吹き飛ばされる（docs/design/88）
  const creeper = target?.name === 'creeper'
  return [
    ...(creeper ? [] : ['fight']),
    'flee',
    ...(homeNear && !creeper ? ['go_home'] : []),
    'keep_distance',
    'ignore'
  ]
}

// コードの規則（Jev の答えを待つ間と、Jev がないとき）: 武器があり相手が 1 体でクリーパーでなければ戦う
export function ruleChoice (bot, target, count, options) {
  // 水の中では素手で戦わない（足場がなく、ドロウンドは水の中のほうが強い）: 先に陸へ（flee が陸へ泳ぐ）
  const fist = FIST_MOBS.has(target.name) && bot.health >= FIST_START_HEALTH && !isSwimming(bot)
  const fightIt = (bestWeapon(bot) || fist) && target.name !== 'creeper' && count === 1
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
  let lastBreath = 0
  const seen = new Map() // モブの id -> [{ t, pos }]（向かってくるかを見る）
  const stalls = new Map() // モブの id -> 続けて離れられなかった・倒せなかった回数
  const muted = new Map() // モブの id -> この時刻まで、近づいても反応しない（ダメージを受けたときは反応する）
  state.reflexMuteAll = 0 // この時刻まで、近づいた相手には反応しない（POST /reflex/release。ダメージは別）

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
    // ボートに乗っている間は物理が止まり、isInWater は乗る前のまま（設計書 37）
    const float = !!bot.entity?.isInWater && !bot.pathfinder.isMoving() && !riding(bot)
    if (float !== floating) {
      bot.setControlState('jump', float)
      floating = float
    }
    // ボートで水の上にいる間は反射で戦い・逃げない: 漕いで離れる方が速い（ボートは毎秒 7m ほど）
    if (++ticks % CHECK_EVERY_TICKS || state.reflex || !bot.entity || bot.health <= 0 || riding(bot)) return
    // 溺れる前に、息のできる所へ（敵より先。設計書 69）
    if (bot.entity.isInWater && Date.now() - lastBreath >= BREATH_EVERY_MS) {
      lastBreath = Date.now()
      const breath = breathTrigger(bot)
      if (breath) {
        gasp(breath).catch((e) => console.log(`[reflex] 息のエラー: ${e.message}`))
        return
      }
    }
    const trigger = triggerNow()
    if (trigger) react(trigger).catch((e) => console.log(`[reflex] エラー: ${e.message}`))
  })

  // 息が続かなくなりそう: 動いている行動を止めて、息のできる所へ泳ぐ。Jev には聞かない（相手がいない）
  async function gasp ({ path, air }) {
    state.reflex = true
    const t0 = Date.now()
    try {
      if (!(await hooks.preempt(`reflex: running out of breath (air ${air}/20)`, true))) return
      bot.pathfinder.setGoal(null)
      bot.clearControlStates()
      let result
      let ok = true
      try {
        result = await surface(bot, path)
      } catch (err) {
        ok = false
        result = `failed: ${err.message}`
      }
      console.log(`[reflex] 息 ${air}/20: ${result}`)
      hooks.record({ action: 'reflex_surface', ok, result: `air ${air}/20: ${result}`, seconds: round((Date.now() - t0) / 1000) })
    } finally {
      floating = false // 制御を離したので、水の中ならまたジャンプを押し直す
      state.reflex = false
    }
  }

  // きっかけ → { why, target, count } か null
  const isMuted = (e) => Date.now() < state.reflexMuteAll || Date.now() < (muted.get(e.id) ?? 0)

  function triggerNow () {
    const near = dangers(bot, state).filter(({ e }) => !isMuted(e))
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
      if (inside || !canSee(bot, e) || isMuted(e)) continue // 家の中は壁が守る
      if (movedToward(track[0].pos, e.position, me) >= APPROACH_METERS) {
        seen.delete(e.id)
        return e
      }
    }
    for (const id of seen.keys()) if (!bot.entities[id]) seen.delete(id)
    return null
  }

  // voluntary: 逃げる手が残っているうちに選んだ戦い。逃げても離れられなかった後の戦い（stalls）は、ほかに手がないので、
  // 体力が低くても殴る（下限を付けると、1 回目から殴らずに立ったまま叩かれ続ける）
  async function perform (choice, target, signal, voluntary = false) {
    if (choice === 'fight') {
      const weapon = bestWeapon(bot)
      if (weapon) await bot.equip(weapon, 'hand')
      return await fight(bot, target, signal, FIGHT_MS, weapon || !voluntary ? 0 : FIST_MIN_HEALTH)
    }
    // 水の中で逃げる: 水の中は離れても追われる（ドロウンドは泳いで戻ってくる）。陸へ泳いで上がる。陸が見つからなければ、これまでの逃げ方
    if (choice === 'flee' && isSwimming(bot)) {
      try {
        return await reachLand(bot, signal)
      } catch (e) {
        if (signal.aborted) throw e
      }
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
    // 逃げても離れられない繰り返し（stalls）: 1 回目は逃げる手を外して、家が近ければ帰る（逃げ続けるより安全。
    // 2026-10-01 user: 「やむを得ない場合は逃走よりも帰宅を優先してもいい」）、帰れなければ戦う。2 回続いたら
    // 帰る手も外して戦う。クリーパーは戦えないので、逃げる手を残す
    const n = stalls.get(target.id) ?? 0
    const all = optionsFor(bot, state, target)
    const strip = (drop) => (n >= 1 && target.name !== 'creeper' ? all.filter((o) => !drop.includes(o)) : all)
    const options = n >= LOOP_FIGHT ? strip(['flee', 'keep_distance', 'go_home']) : strip(['flee', 'keep_distance'])
    const rule = n >= 1 && target.name !== 'creeper'
      ? (options.includes('go_home') ? 'go_home' : options.includes('fight') ? 'fight' : ruleChoice(bot, target, count, options))
      : ruleChoice(bot, target, count, options)
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
          result = await perform(choice, target, danger.controller.signal, n === 0)
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
      // 終わっても相手が射程にいる（逃げても離れられない、戦っても倒せない）なら数える。続いたら反応をやめる
      const choiceDone = steps.at(-1).choice
      const radius = target.name === 'creeper' ? CREEPER_RADIUS : REFLEX_RADIUS
      const stalled = ['flee', 'keep_distance', 'fight', 'go_home'].includes(choiceDone) && bot.entities[target.id] &&
        target.position.distanceTo(bot.entity.position) < radius
      if (stalled) {
        const n = (stalls.get(target.id) ?? 0) + 1
        stalls.set(target.id, n)
        if (n >= LOOP_GIVE_UP) {
          muted.set(target.id, Date.now() + MUTE_MS)
          stalls.delete(target.id)
          steps.push({ choice: 'ignore', ok: true, result: `gave up on ${target.name} after ${n} rounds that changed nothing: no reaction to it for ${MUTE_MS / 1000}s` })
        }
      } else {
        stalls.delete(target.id)
      }
      for (const [id, until] of muted) if (Date.now() >= until) muted.delete(id)
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

// POST /reflex/release: Python が待ち続けた反射を手放す。動いている反射を止め、seconds の間は近づいた相手に
// 反応しない（ダメージを受けたときは反応する）→ { released }
export function releaseReflex (state, seconds) {
  const until = Date.now() + Math.max(1, Math.min(300, Number(seconds) || 30)) * 1000
  state.reflexMuteAll = until
  const d = state.danger
  d?.controller?.abort(new Error('released: the reflex ran too long'))
  if (d) d.next = null
  return { released: !!d, until }
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
