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
// 間はすぐには数えないが、それが WORKING_FACTOR 倍続いても進まなければ動けないとみなす
//
// 掘っては置く繰り返し: pathfinder はブロックが変わるたびに経路を作り直す。掘ると、次の経路がそこに
// 足場の土を置き、置くとまた掘る経路になって、同じ土を掘り続けた（(-78,39,-116) の dirt）。同じ
// ブロックを LOOP_WINDOW_MS の間に LOOP_DIGS 回掘ったら、そのセルはしばらく掘らず置かない
// （guardDigLoops。Movements の除外は configureMovements が isLoopCell で読む）

import pathfinderPkg from 'mineflayer-pathfinder'

const { GoalNearXZ } = pathfinderPkg.goals

export const STUCK_MS = 6000
const MOVED_M = 0.5
const CHECK_MS = 250
const ESCAPES = ['jumped out', 'walked carefully (no sprinting or parkour)', 'backed off a few blocks']
const BACK_OFF_M = 4
const WORKING_FACTOR = 5
const LOOP_DIGS = 3
const LOOP_WINDOW_MS = 60 * 1000
const LOOP_BLOCK_MS = 3 * 60 * 1000

const cellKey = (p) => `${p.x},${p.y},${p.z}`

export function isLoopCell (state, pos, now = Date.now()) {
  const until = state.loopCells?.get(cellKey(pos))
  return !!until && until > now
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

export async function walkTo (bot, goal, signal, stuckMs = STUCK_MS) {
  signal.throwIfAborted()
  const original = bot.pathfinder.movements
  const tried = []
  try {
    for (let attempt = 0; ; attempt++) {
      if (!(await walkWatched(bot, goal, stuckMs))) return
      signal.throwIfAborted()
      if (attempt >= ESCAPES.length) {
        throw new Error(`stuck: did not move for ${stuckMs / 1000}s while walking (tried: ${tried.join(', ')})`)
      }
      console.log(`[move] ${stuckMs / 1000} 秒動けない: ${ESCAPES[attempt]}`)
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

// 目標まで歩く。動けなくなったら止めて真を返す
async function walkWatched (bot, goal, stuckMs) {
  const pos = () => bot.entity?.position
  let last = pos()?.clone?.()
  let since = Date.now()
  let stuck = false
  const timer = last && setInterval(() => {
    const p = pos()
    const working = !!bot.targetDigBlock || !!bot.pathfinder.isMining?.() || !!bot.pathfinder.isBuilding?.()
    if (!p || p.distanceTo(last) >= MOVED_M) {
      if (p) last = p.clone()
      since = Date.now()
      return
    }
    if (Date.now() - since >= stuckMs * (working ? WORKING_FACTOR : 1)) {
      stuck = true
      bot.pathfinder.setGoal(null)
    }
  }, CHECK_MS)
  try {
    await bot.pathfinder.goto(goal)
    return false
  } catch (e) {
    if (stuck) return true
    throw e
  } finally {
    if (timer) clearInterval(timer)
    bot.pathfinder.setGoal(null)
  }
}

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
