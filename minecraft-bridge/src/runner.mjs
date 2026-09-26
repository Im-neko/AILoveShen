// 行動を 1 つ実行する唯一の経路。候補（POST /act）も道具（POST /tool）もここを通る。
//
// タイムアウトか、ボットがダメージを受けたら中断する（反射: 判断する側はそのあと攻撃してきた相手を
// 見て、戦うか逃げるかを選べる）。中断すると経路移動と採掘を取り消し、実行関数が本当に止まるまで
// 待つので、行動が重なることはない。実行中は state.current を持ち（反射の割り込みと POST /abort が
// 使う）、進み具合（state.mjs の共通の状態）を記録する。
import { threats, round } from './observe.mjs'
import { PRIMITIVES, DAMAGE_TOLERANT, TIMEOUTS_MS, DEFAULT_TIMEOUT_MS, HEALTH_CRITICAL } from './primitives.mjs'
import { needsOutside } from './candidates.mjs'
import { leaveHome, houseAround } from './home.mjs'
import { startTracking } from './progress.mjs'

const WALK_PROGRESS_M = 8 // これだけ歩いていれば、時間切れでも進んだとみなす

// deps: { primitives, afterRun(): 実行の後（見えたものの記録と保存）, log(line) }
export function createRunner (bot, state, deps = {}) {
  const primitives = deps.primitives ?? PRIMITIVES
  const afterRun = deps.afterRun ?? (() => {})
  const log = deps.log ?? ((line) => console.log(line))

  // c: 実行するもの（候補か、道具が作ったもの）。c.verb が PRIMITIVES の名前。
  // label: 履歴に残す名前（候補の id か、道具の呼び出し）
  return async function run (c, label) {
    if (state.reflex) return { ok: false, interrupted: true, result: 'not started: the reflex is handling a nearby threat', seconds: 0 }
    if (state.busy) return { ok: false, result: 'not started: another action is running', seconds: 0 }
    state.busy = true
    const t = Date.now()
    const controller = new AbortController()
    const abort = (reason) => {
      if (controller.signal.aborted) return false
      controller.abort(new Error(reason))
      if (bot.targetDigBlock) bot.stopDigging()
      bot.pathfinder.setGoal(null)
      bot.clearControlStates()
      return true
    }
    const limit = TIMEOUTS_MS[c.verb] ?? DEFAULT_TIMEOUT_MS
    // 時間切れの理由に、そのとき何をしていたか（bot.actionPhase。プリミティブが書く）を添える
    bot.actionPhase = ''
    const startPos = bot.entity.position.clone()
    let walkedOnTimeout = null // 歩いている間の時間切れ: どれだけ進んだか
    const timer = setTimeout(() => {
      const moved = round(bot.entity.position.distanceTo(startPos))
      if (/^walking/.test(bot.actionPhase) && moved >= WALK_PROGRESS_M) walkedOnTimeout = `${bot.actionPhase.replace(/^walking/, 'walked')}: ${moved}m this time, not there yet`
      abort(bot.actionPhase ? `timeout (while ${bot.actionPhase}; moved ${moved}m)` : `timeout (moved ${moved}m)`)
    }, limit)
    const onHurt = (entity) => {
      if (entity !== bot.entity) return
      if (DAMAGE_TOLERANT.has(c.verb)) {
        // 逃走は体力にかかわらず続ける。戦いはやめる
        if (c.verb === 'attack' && bot.health <= HEALTH_CRITICAL) abort(`stopped: health critical (${round(bot.health)}/20)`)
        return
      }
      const attacker = threats(bot)[0]?.e.name
      abort(`interrupted: took damage${attacker ? ` (${attacker} nearby)` : ''}`)
    }
    bot.on('entityHurt', onHurt)
    let ok = true
    let result
    let finished
    const progress = startTracking(bot, c, limit)
    state.current = { id: label, verb: c.verb, abort, progress, done: new Promise((resolve) => { finished = resolve }) }
    try {
      const house = houseAround(bot, state)
      if (house && needsOutside(c, house)) {
        bot.actionPhase = 'leaving the house'
        await leaveHome(bot, house, controller.signal, { confront: !!c.confront })
        bot.actionPhase = ''
      }
      controller.signal.throwIfAborted()
      result = await primitives[c.verb](bot, state, c, controller.signal)
      if (controller.signal.aborted) throw controller.signal.reason
    } catch (e) {
      const reason = controller.signal.aborted ? controller.signal.reason : e
      abort(reason.message)
      if (walkedOnTimeout) {
        // 遠い所へ歩いている途中の時間切れは、進んでいれば失敗ではない（次のステップで続きを歩く）。
        // 死んで遠くに戻されたあと、家のドアやチェストへの行動が全部「失敗」になって止まった
        result = walkedOnTimeout
      } else {
        ok = false
        result = `failed: ${reason.message}`
      }
    } finally {
      clearTimeout(timer)
      bot.off('entityHurt', onHurt)
      progress.stop()
      state.busy = false
      state.current = null
      finished()
      afterRun()
    }
    const seconds = round((Date.now() - t) / 1000)
    state.history.push({ action: label, ok, result, seconds })
    log(`[act] ${label}: ${ok ? '成功' : '失敗'} ${result}（${seconds}秒）`)
    // 襲われて止まった（ダメージ、反射）: 小目標の失敗に数えない（設計書 28 §3）
    const interrupted = !ok && /^failed: (interrupted: took damage|reflex: )/.test(result)
    return { ok, result, seconds, ...(interrupted ? { interrupted: true } : {}) }
  }
}

// 実行中の行動を理由をつけて止める（POST /abort）。行動がないか、もう止まっていれば何もしない
export async function abortCurrent (state, reason) {
  const current = state.current
  if (!current) return { aborted: false, why: 'no action is running' }
  if (!current.abort(reason)) return { aborted: false, why: 'the action is already stopping' }
  await current.done
  return { aborted: true, action: current.id }
}
