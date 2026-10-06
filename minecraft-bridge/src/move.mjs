// 経路移動の入口。行動の中断（signal）を移動まで届ける。
//
// 動いている最中の移動は、行動の中断が pathfinder の目標を消して止める。止められないのは、中断の
// 後に新しく始まる移動だった（掘った後に落とし物を拾いに行く移動が、中断の後に始まり、行き着けない
// まま終わらなかった: town4）。なので、移動はここからだけ始め、始める前に中断を確かめる。
//
// 動けなくなったら移動の仕方を変える: 歩いているのに位置が STUCK_MS の間ほとんど変わらない（掘って
// いる・足場を置いている間は数えない）ときは、その移動を止めて、跳んで抜け出す → 走りも飛び越えも
// しない慎重な歩き方にする → 数ブロック下がってからやり直す、の順に試す。それでも動けなければ理由を
// 添えて失敗にする（操作の精度で角や穴に引っかかり、時間切れまで同じ所で足踏みしていた）。掘る・置く
// 間はすぐには数えないが、それが WORKING_FACTOR 倍続いても進まなければ動けないとみなす。掘る・置く
// 間に「進んだ」と数えるのは、位置が変わったときと目標へ近づいたときだけ（2026-10-01: 足元の土を
// 掘っては置き、跳ねて上下しながら、go_home が 15 分同じ穴から動かなかった。掘る・置くがあるだけで
// 近づいたとみなし、上下の跳ねで「動いた」とみなしていた）
//
// 掘っては置く繰り返し: pathfinder はブロックが変わるたびに経路を作り直す。掘ると、次の経路がそこに
// 足場の土を置き、置くとまた掘る経路になって、同じ土を掘り続けた（(-78,39,-116) の dirt）。同じ
// ブロックを LOOP_WINDOW_MS の間に LOOP_DIGS 回掘ったら、そのセルはしばらく掘らず置かない
// （guardDigLoops。Movements の除外は configureMovements が isLoopCell で読む）

import pathfinderPkg from 'mineflayer-pathfinder'
import { doorOpener } from './doors.mjs'
import { isSwimming, swimToward } from './swim.mjs'
import { standingCell } from './observe.mjs'
import { snapshot, where, record } from './stuck-report.mjs'

const { GoalNearXZ } = pathfinderPkg.goals

export const STUCK_MS = 6000
const MOVED_M = 0.5
const CHECK_MS = 250
const SWIM_TRIES = 3
const ESCAPES = ['jumped out', 'walked carefully (no sprinting or parkour)', 'backed off a few blocks']
const BACK_OFF_M = 4
const WORKING_FACTOR = 5
// 動いているのに近づかない: 水の中では流されて泳ぎ続け、位置が動くので「動けない」に数えられず、
// 落とし物を取りに行く移動が 9 分終わらなかった（2026-09-30）。目標までの距離がこの間に PROGRESS_M
// 以上縮まなければ、動けないとみなす（回り道で一時的に遠ざかる時間より十分長く）
export const NO_PROGRESS_MS = 30 * 1000
const PROGRESS_M = 1
const LOOP_DIGS = 3
const LOOP_WINDOW_MS = 60 * 1000
const LOOP_BLOCK_MS = 3 * 60 * 1000

const cellKey = (p) => `${p.x},${p.y},${p.z}`
const AHEAD_M = 0.8 // 足元のこの先に 1 段の段差があれば跳ぶ
// 跳んで乗らない物: 開け閉めする物、柵や塀（1.5 段で越えられない）、足場にならない物
const NO_JUMP = /(_door|_trapdoor|_fence|_fence_gate|_wall|_bed|chest|barrel|furnace|crafting_table|_sign|_banner)$/

// 経路が見つからなかった行き先（ベッド、家の前）: しばらく候補に出さない（2026-09-27: 閉じた建物の中の
// ベッドと家の前に「No path」で失敗し続け、一晩中寝られなかった）
export const NO_PATH_MS = 5 * 60 * 1000

export function markNoPath (state, pos, ms = NO_PATH_MS, now = Date.now()) {
  state.noPath ??= new Map()
  state.noPath.set(cellKey(pos), now + ms)
}

export function isNoPathTo (state, pos, now = Date.now()) {
  const until = state.noPath?.get(cellKey(pos))
  return !!until && until > now
}

export const isNoPath = (e) => /no path/i.test(String(e?.message ?? e))

export function isLoopCell (state, pos, now = Date.now()) {
  const until = state.loopCells?.get(cellKey(pos))
  return !!until && until > now
}

// オートジャンプ（Minecraft のは画面側の設定で、サーバーにもボットにもない）: 前へ歩くキーを押して
// いて、pathfinder が動かしていない（pathfinder は自分で跳ぶ）とき、進む先の足の高さに 1 段の完全な
// ブロックがあり、その上に 2 マス空いていれば跳ぶ。自分で押した跳ぶキーだけを離す
// （2026-09-28: ドアの前の穴から前へ歩き続けて、1 段登れずに家に入れなかった）
export function installAutoJump (bot) {
  let pressed = false
  bot.on('physicsTick', () => {
    const want = shouldAutoJump(bot)
    if (want && !bot.controlState.jump) { bot.setControlState('jump', true); pressed = true } else if (!want && pressed) {
      bot.setControlState('jump', false)
      pressed = false
    }
  })
}

export function shouldAutoJump (bot) {
  const e = bot.entity
  if (!e?.onGround || !bot.controlState?.forward || bot.pathfinder?.isMoving?.()) return false
  const feet = standingCell(bot)
  const ahead = e.position.offset(-Math.sin(e.yaw) * AHEAD_M, 0, -Math.cos(e.yaw) * AHEAD_M).floored()
  ahead.y = feet.y
  if (ahead.x === feet.x && ahead.z === feet.z) return false
  const step = bot.blockAt(ahead)
  if (!step || step.boundingBox !== 'block' || NO_JUMP.test(step.name) || !isFullCube(step)) return false
  const free = (p) => bot.blockAt(p)?.boundingBox === 'empty'
  return free(ahead.offset(0, 1, 0)) && free(ahead.offset(0, 2, 0)) && free(feet.offset(0, 2, 0))
}

function isFullCube (block) {
  const shapes = block.shapes
  if (!Array.isArray(shapes)) return true
  return shapes.length === 1 && shapes[0][1] === 0 && shapes[0][4] === 1
}

export function guardDigLoops (bot, state) {
  const digs = new Map()
  state.loopCells ??= new Map()
  bot.on('diggingCompleted', (block) => {
    if (!block?.position) return
    const key = cellKey(block.position)
    const now = Date.now()
    if (digs.size > 500) digs.clear()
    const times = (digs.get(key) ?? []).filter((t) => now - t < LOOP_WINDOW_MS)
    times.push(now)
    digs.set(key, times)
    if (times.length < LOOP_DIGS || isLoopCell(state, block.position, now)) return
    state.loopCells.set(key, now + LOOP_BLOCK_MS)
    console.log(`[move] ${key} の ${block.name} を掘っては置く繰り返し: しばらくそこは掘らず、置かない`)
    // 経路を作り直させる（除外は次の経路から効く）
    if (bot.pathfinder?.movements) bot.pathfinder.setMovements(bot.pathfinder.movements)
  })
}

export async function walkTo (bot, goal, signal, stuckMs = STUCK_MS, noProgressMs = NO_PROGRESS_MS) {
  signal.throwIfAborted()
  const original = bot.pathfinder.movements
  const tried = []
  let swims = 0
  try {
    for (let attempt = 0; ; attempt++) {
      const stuck = await walkWatched(bot, goal, stuckMs, noProgressMs)
      if (!stuck) return
      signal.throwIfAborted()
      // 水の中で動けない: 跳ぶ・慎重に歩く・下がるでは出られない（pathfinder は水の中から動く手を作らない）。
      // 目標の向きに泳いで岸に上がり、また歩く（泳ぎは 3 回まで、いつもの手の合間に試す）
      const point = goalPoint(goal)
      if (!stuck.working && point && swims < SWIM_TRIES && isSwimming(bot)) {
        swims++
        console.log(`[move] 水の中で動けない（${where(stuck)}）: 目標へ泳いで岸に上がる`)
        const r = await swimToward(bot, point, signal)
        tried.push(`swam ${r.moved}m (${r.why})`)
        if (r.landed) { attempt--; continue }
      }
      // 掘る・置くを続けて近づかなかった移動は、跳ぶ・慎重に歩く・下がるで抜けられない（同じ道を作り直す
      // だけ）ので、試さずに失敗にする
      if (stuck.noProgress && stuck.working) {
        throw new Error(`stuck: did not get closer to the goal in ${noProgressMs * WORKING_FACTOR / 1000}s while digging and placing at ${where(stuck)} (tried: ${tried.join(', ') || 'nothing'})`)
      }
      if (attempt >= ESCAPES.length) {
        const why = stuck.noProgress
          ? `did not get closer to the goal in ${noProgressMs / 1000}s while walking`
          : `did not move for ${stuckMs / 1000}s while walking`
        throw new Error(`stuck: ${why} at ${where(stuck)} (tried: ${tried.join(', ')})`)
      }
      console.log(`[move] ${stuck.noProgress ? '近づけない' : `${stuckMs / 1000} 秒動けない`}（${where(stuck)}）: ${ESCAPES[attempt]}`)
      tried.push(ESCAPES[attempt])
      if (attempt === 0) await jumpOut(bot)
      else if (original) bot.pathfinder.setMovements(careful(original))
      if (attempt === 2) await backOff(bot, stuckMs)
      signal.throwIfAborted()
    }
  } finally {
    if (original && bot.pathfinder.movements !== original) bot.pathfinder.setMovements(original)
  }
}

// 目標まで歩く（経路の先のドアは開け、通ったら閉める: doors.mjs）。動けなくなったら止めて、その場の記録を返す
async function walkWatched (bot, goal, stuckMs, noProgressMs = NO_PROGRESS_MS) {
  const pos = () => bot.entity?.position
  let last = pos()?.clone?.()
  let lowY = last?.y
  let highY = last?.y
  let since = Date.now()
  let stuck = null
  let update = null
  const onPath = (u) => { update = u }
  bot.on?.('path_update', onPath)
  const target = goalPoint(goal)
  let best = target && pos() ? distanceTo(pos(), target) : Infinity
  let closerAt = Date.now()
  // 動いたか: 横に MOVED_M 動いた、または跳ねた範囲（lowY〜highY）の外へ MOVED_M 上下した。同じ高さの
  // 間を跳ねて上下するだけなら、動いていない
  const moved = (p) => {
    if (Math.hypot(p.x - last.x, p.z - last.z) >= MOVED_M) {
      last = p.clone()
      lowY = highY = p.y
      return true
    }
    if (p.y >= highY + MOVED_M || p.y <= lowY - MOVED_M) {
      lowY = Math.min(lowY, p.y)
      highY = Math.max(highY, p.y)
      return true
    }
    return false
  }
  const timer = last && setInterval(() => {
    const p = pos()
    const working = !!bot.targetDigBlock || !!bot.pathfinder.isMining?.() || !!bot.pathfinder.isBuilding?.()
    if (p && target) {
      const d = distanceTo(p, target)
      if (d <= best - PROGRESS_M) {
        best = Math.min(best, d)
        closerAt = Date.now()
      } else if (Date.now() - closerAt >= noProgressMs * (working ? WORKING_FACTOR : 1)) {
        stuck = { ...snapshot(bot, goal, update), noProgress: true, working }
        record(stuck)
        bot.pathfinder.setGoal(null)
        return
      }
    }
    if (!p || moved(p)) {
      since = Date.now()
      return
    }
    if (Date.now() - since >= stuckMs * (working ? WORKING_FACTOR : 1)) {
      stuck = snapshot(bot, goal, update)
      record(stuck)
      bot.pathfinder.setGoal(null)
    }
  }, CHECK_MS)
  const doors = doorOpener(bot)
  try {
    await bot.pathfinder.goto(goal)
    const silent = silentFailure(bot, goal, update)
    if (silent) throw silent
    return null
  } catch (e) {
    if (stuck) return stuck
    throw e
  } finally {
    bot.off?.('path_update', onPath)
    if (timer) clearInterval(timer)
    bot.pathfinder.setGoal(null)
    await doors.stop()
  }
}

// pathfinder の goto は、経路が空のとき（状態が noPath でも timeout でも）失敗にせず成功として返す
// （lib/goto.js: `results.path.length === 0` を状態の判定より先に見る）。着いていないのに成功と受け取ると、
// legToward のように「着くまで繰り返す」呼び出しが、動かないまま毎 tick やり直し続ける（2026-10-04: make_mine が
// 39 分、約 19 回/秒の noPath のまま動かなかった。6 秒の動けない検出は、1 回が短すぎて一度も働かない）。
// 最後の経路の状態が noPath / timeout で、目標に着いていなければ、pathfinder が本来投げるのと同じ失敗にする
function silentFailure (bot, goal, update) {
  if (update?.status !== 'noPath' && update?.status !== 'timeout') return null
  const here = bot.entity?.position?.floored?.()
  if (!here || typeof goal?.isEnd !== 'function' || goal.isEnd(here)) return null
  const noPath = update.status === 'noPath'
  const e = new Error(noPath ? 'No path to the goal!' : 'Took to long to decide path to goal!')
  e.name = noPath ? 'NoPath' : 'Timeout'
  return e
}

// 目標の場所（座標のない目標は null: 近づいたかは見ない）
function goalPoint (goal) {
  const at = Number.isFinite(goal?.x) && Number.isFinite(goal?.z) ? goal : goal?.pos
  if (!at || !Number.isFinite(at.x) || !Number.isFinite(at.z)) return null
  return { x: at.x, y: Number.isFinite(at.y) ? at.y : null, z: at.z }
}

const distanceTo = (p, t) => Math.hypot(p.x - t.x, p.z - t.z, t.y === null ? 0 : p.y - t.y)

// 向きを変えて、跳びながら前へ 1 秒
async function jumpOut (bot) {
  if (!bot.setControlState) return
  await bot.look?.(Math.random() * 2 * Math.PI, 0, true)
  bot.setControlState('jump', true)
  bot.setControlState('forward', true)
  try {
    await bot.waitForTicks(20)
  } finally {
    bot.clearControlStates()
  }
}

// 走らず、飛び越えない歩き方（細かい足場で踏み外す・角に引っかかるのを減らす）
function careful (m) {
  const c = Object.assign(Object.create(Object.getPrototypeOf(m)), m)
  c.allowParkour = false
  c.allowSprinting = false
  return c
}

// ランダムな向きに数ブロック下がる（そこでも動けなければそのまま）
async function backOff (bot, stuckMs) {
  const p = bot.entity?.position
  if (!p) return
  const a = Math.random() * 2 * Math.PI
  await walkWatched(bot, new GoalNearXZ(p.x + Math.cos(a) * BACK_OFF_M, p.z + Math.sin(a) * BACK_OFF_M, 1), stuckMs).catch(() => {})
}
