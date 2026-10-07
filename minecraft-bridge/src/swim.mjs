// 泳ぐ: 水の上を、向かう方向へ水面沿いに進み、岸に着いたら上がる（docs/design/73_swimming.md）。
//
// 経路探索器（pathfinder）は水の中から動く手を作らず（breath.mjs、設計書 58）、ケルプや海草を空気とみなして
// 海底を歩く道を作る（2026-10-01: 動けない記録 107 件のうち 45 件がケルプの中、水面から 3 ブロック下に
// 始まる道が 44 件）。ボートがないときの海は、ここで向きとジャンプを直接押して渡る。
// 息の反射（breath.mjs）が優先される（頭が水に沈んで息が減ったら、反射が行動を中断する）。

import vec3Pkg from 'vec3'
import { holdsWater, passable } from './breath.mjs'

const { Vec3 } = vec3Pkg

const AHEAD_M = 1.2 // 進む先のセルを見る距離
const ARRIVED_M = 1.2
const PROGRESS_M = 1 // この距離だけ目標に近づかないまま PROGRESS_MS 経つと、進めないとみなす
const PROGRESS_MS = 12000
const UNLOADED_WAIT_MS = 3000
const DETOUR_TICKS = 15
const CLIMB_MS = 4000 // 岸のマスの手前に着いてから、岸に上がろうと押し続ける時間
// 前がふさがっているときの向きの変え方（目標の向きからの角度。順に試す）
const TURNS = [30, -30, 60, -60, 90, -90, 135, -135, 180]
const HAZARD = /lava|fire|magma/

// いま水の中にいるか（足のセルが水・昆布・海草か、物理が水の中と言っている）
export function isSwimming (bot) {
  const p = bot.entity?.position
  if (!p) return false
  if (bot.entity.isInWater) return true
  return holdsWater(bot.blockAt?.(p.floored()))
}

export const onLand = (bot) => !isSwimming(bot) && !!bot.entity?.onGround

const rotate = (x, z, deg) => {
  const r = (deg * Math.PI) / 180
  return [x * Math.cos(r) - z * Math.sin(r), x * Math.sin(r) + z * Math.cos(r)]
}

// dir の向き AHEAD_M 先のセル: open（水か空気。進める）、bank（足の高さに地面があり、上が 2 つ空いている:
// ジャンプで上がれる岸）、blocked（登れない壁、溶岩）、unloaded
export function aheadKind (bot, me, dir) {
  const x = Math.floor(me.x + dir[0] * AHEAD_M)
  const z = Math.floor(me.z + dir[1] * AHEAD_M)
  const y = Math.floor(me.y)
  const c0 = bot.blockAt(new Vec3(x, y, z))
  const c1 = bot.blockAt(new Vec3(x, y + 1, z))
  const c2 = bot.blockAt(new Vec3(x, y + 2, z))
  if (!c0 || !c1 || !c2) return 'unloaded'
  if (HAZARD.test(c0.name) || HAZARD.test(c1.name)) return 'blocked'
  if (passable(c0) && passable(c1)) return 'open'
  if (c0.boundingBox === 'block' && passable(c1) && passable(c2)) return 'bank'
  return 'blocked'
}

// target（{ x, z }）へ水面沿いに泳ぐ（水際に立った所からでもよい）。止まるのは: 水に入ったあと岸に上がった
// （landed）、target の近く、進めない（目標に近づかない）、先が読み込まれていない、signal。時間では止めない。返すのは { landed, why, moved }
// climb: target が岸の陸のマス（陸を探して上がるとき）。手前の水の中で「着いた」にせず、上がるまで押す
// （target は岸のいちばん手前の陸のマスなので、1.2 m 手前に着いた所はまだ水の中だった。2026-10-07: 池で 5 分動けなかった）
export async function swimToward (bot, target, signal, { maxMs = Infinity, progressMs = PROGRESS_MS, climb = false } = {}) {
  const start = bot.entity.position.clone()
  const t0 = Date.now()
  let why = 'time is up'
  let landed = false
  let heading = null
  let detourTicks = 0
  let best = Infinity
  let bestAt = Date.now()
  let unloadedSince = null
  let arrivedAt = null
  // 岸から泳ぎ始めるときは、まだ水に入っていない: 一度水の中に入ってから、陸に上がったと数える
  let entered = isSwimming(bot)
  try {
    while (Date.now() - t0 < maxMs) {
      signal?.throwIfAborted()
      const me = bot.entity.position
      const dx = target.x - me.x
      const dz = target.z - me.z
      const flat = Math.hypot(dx, dz)
      if (isSwimming(bot)) entered = true
      else if (entered && onLand(bot)) { landed = true; why = 'on land'; break }
      if (flat < ARRIVED_M) {
        // 水から跳び出した直後は、泳いでいる判定が外れて、まだ地面にも着いていない（空中）: 着くまで待つ
        // （そこで「着いた」にして止めると、陸に上がれたのに失敗と返った: 2026-10-07 に 2 回）
        if (!climb) { why = 'arrived'; break }
        arrivedAt ??= Date.now()
        if (Date.now() - arrivedAt > CLIMB_MS) { why = 'arrived but could not climb onto the bank (too high or blocked)'; break }
      }
      if (flat <= best - PROGRESS_M) { best = flat; bestAt = Date.now() }
      else if (Date.now() - bestAt > progressMs) { why = `did not get closer to ${Math.round(target.x)},${Math.round(target.z)} in ${progressMs / 1000}s`; break }
      const want = flat > 0 ? [dx / flat, dz / flat] : [0, 1]
      // 前がふさがっていれば、少しの間（DETOUR_TICKS）よけた向きに進み、また目標の向きを試す
      let dir = want
      let kind = 'open'
      if (detourTicks > 0) {
        dir = heading
        kind = aheadKind(bot, me, dir)
        if (kind === 'blocked') detourTicks = 0
      }
      if (detourTicks === 0) {
        dir = want
        kind = aheadKind(bot, me, want)
        if (kind === 'blocked') {
          for (const deg of TURNS) {
            const turned = rotate(want[0], want[1], deg)
            const k = aheadKind(bot, me, turned)
            if (k === 'open' || k === 'bank') { dir = turned; kind = k; heading = turned; detourTicks = DETOUR_TICKS; break }
          }
        }
      }
      if (kind === 'unloaded') {
        unloadedSince ??= Date.now()
        if (Date.now() - unloadedSince > UNLOADED_WAIT_MS) { why = 'the water ahead is not loaded'; break }
        bot.setControlState('forward', false)
        await bot.waitForTicks(1)
        continue
      }
      unloadedSince = null
      if (kind === 'blocked') { why = 'no way forward: walls all around'; break }
      await bot.look(Math.atan2(-dir[0], -dir[1]), 0, true)
      bot.setControlState('forward', true)
      bot.setControlState('sprint', true)
      bot.setControlState('jump', true)
      await bot.waitForTicks(1)
      if (detourTicks > 0) detourTicks--
    }
  } finally {
    for (const c of ['forward', 'sprint', 'jump']) bot.setControlState(c, false)
  }
  const p = bot.entity.position
  return { landed, why, moved: Math.round(Math.hypot(p.x - start.x, p.z - start.z)) }
}
