// ボートで水の上を渡る（設計書 37）。
//
// Mineflayer はボートを動かせない: 乗ると自分の物理計算を止め、入力（player_input）だけを送る。ボートの
// 位置は乗っている側が計算してサーバーに送る（vehicle_move）ので、入力だけでは動かない。ここでは水の上の
// 動きを自分で計算し、1 ティックごとに vehicle_move を送る（毎秒 7m ほど。バニラの水上の速さに近い。
// Paper 1.21.4 で位置の修正なしに受け入れられることを確かめた: 2026-09-27）。
//
// ほかに Mineflayer 4.39 で合わないところ:
// - dismount() は 1.21.2 以降で「ジャンプ」の入力を送るが、降りるのはスニーク。entity_action の
//   start_sneaking で降りる
// - 降りたとき（set_passengers から自分が外れる）に bot.vehicle を消さない。watchVehicle が消す
// - 乗っている間は自分の位置（bot.entity.position）を動かさない。ボートと一緒に動かす（距離の判定や
//   見え方がずれないように）
import vec3Pkg from 'vec3'
import { round } from './observe.mjs'

const { Vec3 } = vec3Pkg

export const BOAT = /^(?!.*chest).*(_boat|_raft)$/ // 乗るためのボート（チェスト付きは持ち物用なので使わない）
export const BOAT_MIN_WATER = 12 // 道のりにこれだけ続けて水があれば、ボートで渡る
export const CROSSING_LOOK_M = 96 // 水を探す道のりの長さ（読み込まれている所まで）
const SPEED = 0.35 // 1 ティックに進むブロック
const TICK_MS = 50
const PASSENGER_Y = -0.4125 // 乗っている人の足の高さ（ボートの位置から）
// ボートの当たり判定: 軸にそろった箱（幅 1.375、高さ 0.5625）。向きで回らないので、斜めに進むと角が
// 前に出る。サーバー（Paper の hasNewCollision）は、動いた後の箱に前はなかった当たりがあると、ログなしで
// 位置を戻す（2026-09-27: 岸の草ブロックに箱の角がかかり、毎ティック戻されて 5 分以上その場で漕ぎ続けた）
const BOX_HALF = 0.6875
const BOX_HEIGHT = 0.5625
const PUSHED_BACK_TICKS = 20 // 続けてこれだけ戻されたら、進めないとして止まる（1 秒）
const TURNS = [0, 20, -20, 40, -40, 60, -60, 90, -90] // 前が塞がっていたら、この角度の向きを順に試す
const OBSTACLE_LOOK_M = 24 // 前の陸の向こうにまた水があれば、島（よけて進む）
const UNLOADED_WAIT_MS = 3000
const PLACE_REACH = 4
const BREAK_HITS = 10

const isWater = (name) => /^(water|bubble_column|kelp|kelp_plant|seagrass|tall_seagrass)$/.test(name ?? '')
const CLEAR = /^(air|cave_air|lily_pad)$/ // 水面を探すときに飛ばすもの（睡蓮は当たり判定があるので boxFree が止まる）
const SOFT = /^(short_grass|tall_grass|fern|large_fern|dead_bush|snow|vine|.*_carpet|.*_flower|dandelion|poppy)$/

export const boatItem = (bot) => (bot.inventory?.items?.() ?? []).find((i) => BOAT.test(i.name)) ?? null
export const riding = (bot) => !!bot.vehicle && BOAT.test(bot.vehicle.name ?? '')

// 降りたのに bot.vehicle が残る（上の注意）のを直す。index.mjs が 1 回だけ呼ぶ
export function watchVehicle (bot) {
  bot._client.on('set_passengers', ({ entityId, passengers }) => {
    const v = bot.vehicle
    if (!v || v.id !== entityId || passengers.includes(bot.entity?.id)) return
    bot.vehicle = null
    bot.emit('dismount', v)
  })
}

// (x, z) の列のいちばん上: { y, name, water } か、読み込まれていなければ null。草や花は地面に数えない
export function surfaceAt (bot, x, z, nearY) {
  const top = Math.floor(nearY) + 8
  for (let y = top; y > top - 24; y--) {
    const b = bot.blockAt(new Vec3(Math.floor(x), y, Math.floor(z)))
    if (!b) return null
    if (CLEAR.test(b.name) || SOFT.test(b.name)) continue
    return { y, name: b.name, water: isWater(b.name) }
  }
  return null
}

// 今いる所から target へのまっすぐな道のりで、最初に BOAT_MIN_WATER m 以上続く水: { from, length, entry,
// waterY, landAfter } か null。from は水の始まりまでの距離、entry は最初の水のセル（水面の高さ）、
// landAfter は向こう岸が見えているか（見えなければ読み込まれた所まで水）
export function waterCrossing (bot, target, lookM = CROSSING_LOOK_M, me = bot.entity.position, minWater = BOAT_MIN_WATER) {
  const dx = target.x - me.x
  const dz = target.z - me.z
  const far = Math.hypot(dx, dz)
  if (far < 1) return null
  const ux = dx / far
  const uz = dz / far
  const end = Math.min(far, lookM)
  let run = null
  for (let d = 0; d <= end; d += 1) {
    const s = surfaceAt(bot, me.x + ux * d, me.z + uz * d, me.y)
    if (!s) break
    if (s.water) {
      run ??= { from: d, entry: new Vec3(Math.floor(me.x + ux * d), s.y, Math.floor(me.z + uz * d)), waterY: s.y }
      run.length = d - run.from + 1
      continue
    }
    if (run && run.length >= minWater) return { ...run, landAfter: true }
    run = null
  }
  return run && run.length >= minWater ? { ...run, landAfter: false } : null
}

// 水面の高さ y の (x, z) を、ボートが通れるか: 水で、上が空いている
function navigable (bot, x, z, waterY) {
  const cell = bot.blockAt(new Vec3(Math.floor(x), waterY, Math.floor(z)))
  const above = bot.blockAt(new Vec3(Math.floor(x), waterY + 1, Math.floor(z)))
  if (!cell || !above) return null // 読み込まれていない
  return isWater(cell.name) && CLEAR.test(above.name)
}

// ボートの箱を (x, y, z) に置けるか: 箱にかかるブロックに当たり判定のあるものがない（読み込まれていなければ null）
const EPS = 1e-5
export function boxFree (bot, x, y, z) {
  for (let bx = Math.floor(x - BOX_HALF + EPS); bx <= Math.floor(x + BOX_HALF - EPS); bx++) {
    for (let bz = Math.floor(z - BOX_HALF + EPS); bz <= Math.floor(z + BOX_HALF - EPS); bz++) {
      for (let by = Math.floor(y + EPS); by <= Math.floor(y + BOX_HEIGHT - EPS); by++) {
        const b = bot.blockAt(new Vec3(bx, by, bz))
        if (!b) return null
        if (b.boundingBox === 'block') return false
      }
    }
  }
  return true
}

// 向き (ux, uz) に ahead m 先まで、1 ティックの歩みごとに、真ん中の下が水で、ボートの箱が何にも当たらないか
// （読み込まれていなければ null）
function clearAhead (bot, pos, ux, uz, waterY, ahead = 2.5) {
  for (let d = SPEED; d <= ahead + EPS; d += SPEED) {
    const x = pos.x + ux * d
    const z = pos.z + uz * d
    const water = navigable(bot, x, z, waterY)
    if (water !== true) return water
    const free = boxFree(bot, x, pos.y, z)
    if (free !== true) return free
  }
  return true
}

const rotate = (ux, uz, deg) => {
  const a = (deg * Math.PI) / 180
  return [ux * Math.cos(a) - uz * Math.sin(a), ux * Math.sin(a) + uz * Math.cos(a)]
}

// 水のセル water に向けてボートを置き、出てきたボートのエンティティを返す
export async function placeBoat (bot, water, signal) {
  const item = boatItem(bot)
  if (!item) throw new Error('no boat')
  await bot.equip(item, 'hand')
  signal.throwIfAborted()
  const spawned = waitFor(bot, 'entitySpawn', (e) => BOAT.test(e.name ?? '') && e.position.distanceTo(water) < PLACE_REACH + 2, 2000)
  await bot.lookAt(water.offset(0.5, 0.9, 0.5), true)
  bot.activateItem()
  const boat = await spawned
  if (!boat) throw new Error(`the boat was not placed on the water at ${water.x},${water.y},${water.z}`)
  return boat
}

// ボートに乗る（乗れなければ例外）
export async function mountBoat (bot, boat) {
  if (bot.vehicle === boat) return
  const mounted = waitFor(bot, 'mount', () => bot.vehicle?.id === boat.id, 2000)
  bot.mount(boat)
  if (!(await mounted)) throw new Error('could not get into the boat')
}

// target に向かって水の上を進む。止まるのは: target の 3m 以内、向こう岸に着いた、進めない、signal、
// 時間切れ（maxMs。既定はなし）。返すのは { moved, why, landed }（landed: 岸に着いた）
export async function drive (bot, boat, target, signal, { maxMs = Infinity, log = () => {} } = {}) {
  const start = boat.position.clone()
  let pos = boat.position.clone()
  const offsetY = pos.y - Math.floor(pos.y) // 水面のセルからのボートの高さ
  let waterY = Math.floor(pos.y)
  if (!isWater(bot.blockAt(new Vec3(Math.floor(pos.x), waterY, Math.floor(pos.z)))?.name)) waterY -= 1
  const t0 = Date.now()
  let why = ''
  let landed = false
  let waitedSince = null
  let corrected = 0
  let pushedBack = 0 // 続けて戻されたティック数
  let seen = 0
  const onCorrect = (p) => { corrected++; pos = new Vec3(p.x, p.y, p.z) }
  bot._client.on('vehicle_move', onCorrect)
  try {
    while (true) {
      if (signal.aborted) { why = `stopped: ${signal.reason?.message ?? 'aborted'}`; break }
      if (bot.vehicle?.id !== boat.id) { why = 'no longer in the boat'; break }
      if (Date.now() - t0 > maxMs) { why = 'time is up for this stretch'; break }
      pushedBack = corrected > seen ? pushedBack + 1 : 0
      seen = corrected
      if (pushedBack >= PUSHED_BACK_TICKS) {
        why = `the server kept pushing the boat back at ${round(pos.x)},${round(pos.z)} (something is in the way)`
        break
      }
      const dx = target.x - pos.x
      const dz = target.z - pos.z
      const left = Math.hypot(dx, dz)
      if (left <= 3) { why = 'arrived'; landed = true; break }
      const [ux, uz] = [dx / left, dz / left]
      const straight = clearAhead(bot, pos, ux, uz, waterY)
      if (straight === null) {
        // 先のチャンクがまだ届いていない: 少し待つ
        waitedSince ??= Date.now()
        if (Date.now() - waitedSince > UNLOADED_WAIT_MS) { why = 'the water ahead is not loaded'; break }
        await sleep(TICK_MS)
        continue
      }
      waitedSince = null
      let dir = straight ? [ux, uz] : null
      if (!dir) {
        // 前が陸: 向こう岸か島か。まっすぐ先の陸の向こうにまた長い水があれば島なのでよける
        const beyond = waterCrossing(bot, target, OBSTACLE_LOOK_M, pos)
        if (!beyond) { why = 'reached the shore'; landed = true; break }
        for (const deg of TURNS.slice(1)) {
          const [rx, rz] = rotate(ux, uz, deg)
          if (clearAhead(bot, pos, rx, rz, waterY) === true) { dir = [rx, rz]; break }
        }
        if (!dir) { why = 'reached the shore'; landed = true; break }
      }
      pos = pos.offset(dir[0] * SPEED, 0, dir[1] * SPEED)
      pos.y = waterY + offsetY
      const yaw = (Math.atan2(-dir[0], dir[1]) * 180) / Math.PI
      bot._client.write('steer_boat', { leftPaddle: true, rightPaddle: true })
      bot._client.write('vehicle_move', { x: pos.x, y: pos.y, z: pos.z, yaw, pitch: 0, onGround: false })
      boat.position.set(pos.x, pos.y, pos.z)
      bot.entity.position.set(pos.x, pos.y + PASSENGER_Y, pos.z)
      bot.entity.yaw = Math.atan2(-dir[0], -dir[1])
      await sleep(TICK_MS)
    }
  } finally {
    bot._client.off('vehicle_move', onCorrect)
  }
  if (corrected) log(`[boat] サーバーが位置を ${corrected} 回直した`)
  return { moved: round(start.xzDistanceTo(pos)), why, landed }
}

// ボートから降りる（スニーク）。サーバーが位置を送り直すまで待つ
export async function leaveBoat (bot) {
  if (!bot.vehicle) return
  const moved = waitFor(bot, 'forcedMove', () => true, 2000)
  bot._client.write('entity_action', { entityId: bot.entity.id, actionId: 0, jumpBoost: 0 })
  await moved
  bot._client.write('entity_action', { entityId: bot.entity.id, actionId: 1, jumpBoost: 0 })
  if (bot.vehicle) {
    const v = bot.vehicle
    bot.vehicle = null
    bot.emit('dismount', v)
  }
}

// ボートを叩いて壊す（アイテムに戻る）。壊れたら真
export async function breakBoat (bot, boat, signal) {
  for (let i = 0; i < BREAK_HITS && bot.entities[boat.id]; i++) {
    signal.throwIfAborted()
    if (bot.entity.position.distanceTo(boat.position) > PLACE_REACH) return false
    await bot.lookAt(boat.position.offset(0, 0.3, 0), true)
    bot.attack(boat)
    await sleep(250)
  }
  return !bot.entities[boat.id]
}

// 近くのボートのエンティティ（降りた後に探す）
export const nearbyBoat = (bot, maxM = PLACE_REACH + 2) =>
  Object.values(bot.entities).find((e) => BOAT.test(e.name ?? '') && e.position.distanceTo(bot.entity.position) <= maxM) ?? null

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

// イベントを待つ: 合うものが来ればその引数（なければ true）、時間切れなら null
function waitFor (bot, event, match, ms) {
  return new Promise((resolve) => {
    const on = (...args) => {
      if (!match(...args)) return
      clearTimeout(timer)
      bot.off(event, on)
      resolve(args[0] ?? true)
    }
    const timer = setTimeout(() => { bot.off(event, on); resolve(null) }, ms)
    bot.on(event, on)
  })
}
