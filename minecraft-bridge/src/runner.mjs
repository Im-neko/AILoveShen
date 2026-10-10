// 行動を 1 つ実行する唯一の経路。候補（POST /act）も道具（POST /tool）もここを通る。
//
// 時間では止めない（ユーザー 2026-09-27:「基本制限は不要。必要になったら野営」）。止めるのは必要が
// 出たとき: ボットがダメージを受けた（反射: 判断する側はそのあと攻撃してきた相手を見て、戦うか逃げるかを
// 選べる）、夕方・夜になった、空腹・体力が危なくなった（needNow）、POST /abort（考え直し、見張り）。
// 動けない・経路がないのは移動が失敗にする（move.mjs）。中断すると経路移動と採掘を取り消し、実行関数が本当に止まるまで
// 待つので、行動が重なることはない。実行中は state.current を持ち（反射の割り込みと POST /abort が
// 使う）、進み具合（state.mjs の共通の状態）を記録する。
import { threats, round, dayPhase, isDark, isUnderground, inventoryCounts, pushHistory } from './observe.mjs'
import { PRIMITIVES, DAMAGE_TOLERANT, HEALTH_CRITICAL, HUNGER_URGENT, TRAVELS, disembark, leaveHouse } from './primitives.mjs'
import { riding } from './boat.mjs'
import { forgetRegion } from './reach.mjs'
import { isSwimming } from './swim.mjs'
import { needsOutside } from './candidates.mjs'
import { houseAround } from './home.mjs'
import { startTracking } from './progress.mjs'
import { brokeSince } from './wear.mjs'
import { carriesFood } from './healing.mjs'
import { TRAIL_VERBS, startTrail } from './trail.mjs'
import { noteRefusal } from './refusals.mjs'
import { assessRisk, atLeast } from './risk.mjs'

const NEED_CHECK_MS = 500
// 夕方・夜になっても止めない行動（夜を過ごす、家に入る、戦う・逃げる、食べる）
const NIGHT_VERBS = new Set(['sleep', 'sleep_at', 'bed_here', 'place_bed', 'go_home', 'wait', 'repair_wall', 'exit_wall',
  'attack', 'flee', 'stash', 'tidy', 'eat', 'place_torch', 'recover_items', 'plug_hole', 'wake_sleeper', 'dig_in', 'wall_in', 'reach_land', 'leave_burrow'])
// 歩いて帰る・向かう夜の行動: 食べ物を持っているなら、空腹になったら止めて食べる（なければ止めない）
const NIGHT_WALKS = new Set(['go_home', 'sleep_at', 'recover_items'])
// 始めたときより暗い時間帯になったら止める（明け方・朝になるのでは止めない）
const DARKER = { day: ['dusk', 'night'], dusk: ['night'], night: [], dawn: [] }
// 暗くて松明がないとき止めない行動（松明を置く・作る、帰る、食べる、戦う・逃げる…）。それ以外は止めて、
// 引き返して松明を作らせる（暗い所では何も見えない。設計書 15）
const DARK_OK_VERBS = new Set([...NIGHT_VERBS, 'craft', 'place_torch_at', 'surface'])
// 止めた理由のうち、やり方の失敗ではないもの（小目標の失敗・行き詰まりに数えない）
const NOT_A_FAILURE = /^failed: (interrupted: took damage|reflex: |stopped: |rethink: )/

// deps: { primitives, afterRun(): 実行の後（見えたものの記録と保存）, log(line) }
export function createRunner (bot, state, deps = {}) {
  const primitives = deps.primitives ?? PRIMITIVES
  const afterRun = deps.afterRun ?? (() => {})
  const log = deps.log ?? ((line) => console.log(line))

  // c: 実行するもの（候補か、道具が作ったもの）。c.verb が PRIMITIVES の名前。
  // label: 履歴に残す名前（候補の id か、道具の呼び出し）
  async function runOnce (c, label) {
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
    // 止めた理由に、そのとき何をしていたか（bot.actionPhase。プリミティブが書く）を添える
    bot.actionPhase = ''
    const startPos = bot.entity.position.clone()
    const tooDark = () => isDark(bot) && !(inventoryCounts(bot).torch > 0) && !(state.home && houseAround(bot, state))
    const start = { phase: dayPhase(bot.time.timeOfDay), food: bot.food, health: bot.health, tooDark: tooDark() }
    // 必要が出たか: 始めたときになかったものだけ（空腹のまま食料を探す行動を毎回止めない）
    const needNow = () => {
      if (riding(bot)) return null // 水の上: 岸まで漕ぐ
      const phase = dayPhase(bot.time.timeOfDay)
      // 深い地下は夜も昼も同じ: 夕方・夜になっても止めない（isUnderground）
      // 暗くなったら、危なさが高い（risk.mjs: 敵、体力、持ち物の値打ち）ときだけ止める。武器を持った元気なボットや、
      // 失うものがほとんどないボットは、夕方・夜も続ける（時間帯だけで止めると、昼に始めた作業が毎日夕方に切れた）
      if (DARKER[start.phase]?.includes(phase) && !NIGHT_VERBS.has(c.verb) && !isUnderground(bot) && atLeast(assessRisk(bot, state), 'high')) return `it is ${phase} now and going on is too dangerous`
      if (!DARK_OK_VERBS.has(c.verb) && !start.tooDark && tooDark()) return 'too dark to see and no torch'
      // 空腹で止めるのは、食べに行くため。夜を過ごす・家に帰る・戦う・逃げる・食べる（NIGHT_VERBS）は止めない:
      // 食べ物がないと、帰る歩きを途中で止めるだけになる（2026-09-30 18:07: 家まで 64 m の帰り道を 14 m で止め、
      // 夜の外でモブに囲まれて死んだ）
      // 食べ物を持っているなら、夜の歩きも止めて食べる（2026-10-01: 満腹度 0 で肉を持ったまま 11 分歩いた）
      const eats = NIGHT_WALKS.has(c.verb) && carriesFood(bot)
      if ((!NIGHT_VERBS.has(c.verb) || eats) && bot.food <= HUNGER_URGENT && start.food > HUNGER_URGENT) return `hungry (food ${bot.food}/20)`
      if (!DAMAGE_TOLERANT.has(c.verb) && bot.health <= HEALTH_CRITICAL && start.health > HEALTH_CRITICAL) return `health is low (${round(bot.health)}/20)`
      return null
    }
    const timer = setInterval(() => {
      const need = needNow()
      if (!need) return
      const moved = round(bot.entity.position.distanceTo(startPos))
      abort(`stopped: ${need}${bot.actionPhase ? ` (while ${bot.actionPhase}; moved ${moved}m)` : moved >= 1 ? ` (moved ${moved}m)` : ''}`)
    }, NEED_CHECK_MS)
    const stopTrail = TRAIL_VERBS.has(c.verb) ? startTrail(bot, state, log) : null
    const onHurt = (entity) => {
      if (entity !== bot.entity) return
      // ボートで水の上を進んでいる: 止まるより進み続けて離れる（設計書 37）
      if (riding(bot)) return
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
    const progress = startTracking(bot, c)
    state.current = { id: label, verb: c.verb, abort, progress, done: new Promise((resolve) => { finished = resolve }) }
    try {
      if (riding(bot) && !TRAVELS.has(c.verb)) {
        // ボートに乗ったまま: 水の上を進む行動でなければ、先に降りてボートを拾う（設計書 37）
        bot.actionPhase = 'getting off the boat'
        await disembark(bot, state, controller.signal, `次の行動 ${c.verb} は水の上を進む行動ではない`)
        bot.actionPhase = ''
      }
      const house = houseAround(bot, state)
      if (house && needsOutside(c, house, bot)) {
        bot.actionPhase = 'leaving the house'
        await leaveHouse(bot, state, house, controller.signal, { confront: !!c.confront })
        bot.actionPhase = ''
      }
      controller.signal.throwIfAborted()
      result = await primitives[c.verb](bot, state, c, controller.signal)
      if (controller.signal.aborted) throw controller.signal.reason
    } catch (e) {
      const reason = controller.signal.aborted ? controller.signal.reason : e
      abort(reason.message)
      ok = false
      result = `failed: ${reason.message}`
    } finally {
      clearInterval(timer)
      stopTrail?.()
      bot.off('entityHurt', onHurt)
      progress.stop()
      forgetRegion(bot) // 掘る・置く・歩くで、まわりの歩ける範囲が変わった
      state.busy = false
      state.current = null
      finished()
      afterRun()
    }
    const seconds = round((Date.now() - t) / 1000)
    result += brokeSince(state, t) // この行動の間に壊れた道具（すぐ持ち物の見方を直すため）
    // 水の中で終わった行動は、陸ではないと結果に書く（成功と読んで、陸にいると思い込まない）
    if (isSwimming(bot) && !riding(bot)) result += '; you are in the water now, not on land'
    pushHistory(state, { action: label, ok, result, seconds })
    log(`[act] ${label}: ${ok ? '成功' : '失敗'} ${result}（${seconds}秒）`)
    // 襲われて止まった（ダメージ、反射。設計書 28 §3）、必要が出て止めた、考え直しで止めた: 小目標の失敗に
    // 数えない
    const interrupted = !ok && NOT_A_FAILURE.test(result)
    return { ok, result, seconds, ...(interrupted ? { interrupted: true } : {}) }
  }

  // 同じ場所への同じ手が、断られた・着けなかったなら台帳に載せる（refusals.mjs。1 回で、期限まで）
  return async function run (c, label) {
    const r = await runOnce(c, label)
    if (!r.ok && !r.interrupted && c.pos) noteRefusal(state, c.verb, c.pos, r.result)
    return r
  }
}

// 閉じた所・縦穴から出る行動。途中で考え直しに止められると、岩の中の縦穴に残る（2026-10-10 14:31、climb_out が 71 秒登ったところで
// 「区画間の道を整備して」の考え直しに止められ、そのあと穴の中で作業台へ戻ろうとして No path を繰り返した）
export const ESCAPE_VERBS = new Set(['leave_burrow', 'climb_out', 'dig_out', 'surface', 'reach_land'])

// 実行中の行動を理由をつけて止める（POST /abort）。行動がないか、もう止まっていれば何もしない。
// 考え直し（rethink:）は、出る行動の間は待たせる（依頼は消えず、出たあとの切れ目で効く）
export async function abortCurrent (state, reason) {
  const current = state.current
  if (!current) return { aborted: false, why: 'no action is running' }
  if (/^rethink:/.test(reason) && ESCAPE_VERBS.has(current.verb)) return { aborted: false, why: `getting out of the hole (${current.verb}): the rethink waits until you are out` }
  if (!current.abort(reason)) return { aborted: false, why: 'the action is already stopping' }
  await current.done
  return { aborted: true, action: current.id }
}
