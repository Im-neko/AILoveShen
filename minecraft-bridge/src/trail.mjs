// 湧き潰し: 暗い所を歩く・掘る間、モブが湧く暗闇（ブロック光 0）を作らないよう、進みながら松明を置く
// （docs/design/77_spawn_proofing.md）。2026-10-01 の 2 回の死は、松明なしで丘の中の石を掘っているとき、
// 地下 111 m の帰り道を歩いているときだった。置く場所は isDark（光源から 8 m 以上離れた、屋根の下で
// 空が近くない所）なので、間隔はおよそ 9 m（松明の光は 13 ブロック先まで届く）。
// 立ち止まる行動の間（StepPicker が place_torch を選ぶ）ではなく、歩く・掘る 1 回の長い行動の途中で効く。
import { isDark, inventoryCounts } from './observe.mjs'
import { riding } from './boat.mjs'
import { isSwimming } from './swim.mjs'
import { houseAround } from './home.mjs'
import { torchSpot } from './primitives.mjs'

export const TORCH_SPACING = 9 // 暗い所を進むとき、松明を置く間隔の目安（m）
export const TORCH_SPARE = 4 // 行きの分のほかに持っておく数（帰りと、行き先で置く分）
export const TORCH_CAP = 32 // 手持ちのストックの上限（goal_chooser.TORCH_STOCK）
const TRAIL_CHECK_MS = 500
const RETRY_MS = 10000 // 置けなかったあと、また試すまで
const RETRY_SWAPPED_MS = 1000 // 行動に持ち替えられて置かなかったとき（松明は置けるので早く試す）

// 途中で松明を置いてよい行動（歩く・掘る）。dig_mine は自分で置く。戦う・逃げる・食べる・寝るなどは置かない
export const TRAIL_VERBS = new Set(['goto_pos', 'goto_memory', 'go_home', 'explore', 'survey', 'recover_items', 'sleep_at',
  'dig', 'dig_down', 'pickup', 'level'])

// 暗い所へ入る前に持っているべき松明の数: 行き先までの距離から（ブリッジの goto と dig_down が断る）
export function torchesNeeded (meters) {
  return Math.min(TORCH_CAP, Math.ceil(meters / TORCH_SPACING) + TORCH_SPARE)
}

// 今ここに置くか: 足元に置けて、暗くて、松明を持っていて、掘ったり泳いだりしていない。置く床のブロックを返す
export function trailSpot (bot, state, dark = isDark) {
  if (state.reflex || riding(bot) || isSwimming(bot) || !bot.entity?.onGround) return null
  if (bot.targetDigBlock || bot.pathfinder?.isMining?.() || bot.pathfinder?.isBuilding?.()) return null
  if (!(inventoryCounts(bot).torch > 0) || !dark(bot) || (state.home && houseAround(bot, state))) return null
  const spot = torchSpot(bot)
  return spot ? { spot, floor: bot.blockAt(spot.offset(0, -1, 0)) } : null
}

// 1 本置く（持っている物は置いたあとに持ち直す）。置いたら位置、置かなかったら null
export async function placeTrailTorch (bot, state, dark = isDark) {
  const at = trailSpot(bot, state, dark)
  if (!at) return null
  const torch = bot.inventory.items().find((i) => i.name === 'torch')
  const held = bot.heldItem
  await bot.equip(torch, 'hand')
  try {
    // 持ち替えを待つ間に、動いている行動が道具を持ち直した（掘る手が鶴嘴を持つ）と、鶴嘴を置こうとして断られる
    // （2026-10-03 08:02 `Server refused to place stone_pickaxe`）。置く直前に確かめ、違えば置かずに後で試す
    if (bot.heldItem?.name !== 'torch' || bot.targetDigBlock || bot.pathfinder?.isMining?.()) {
      throw Object.assign(new Error(`the held item changed while placing (holding ${bot.heldItem?.name ?? 'nothing'})`), { swapped: true })
    }
    await bot.placeBlock(at.floor, { x: 0, y: 1, z: 0 })
  } finally {
    if (held && held.name !== 'torch') await bot.equip(held, 'hand').catch(() => {})
  }
  if (bot.blockAt(at.spot)?.name !== 'torch') throw new Error('the torch was not placed')
  return at.spot
}

// 行動の間、定期的に見て置く。返すのは止める関数
export function startTrail (bot, state, log = console.log) {
  let busy = false
  let retryAt = 0
  const timer = setInterval(() => {
    if (busy || Date.now() < retryAt) return
    busy = true
    placeTrailTorch(bot, state)
      .then((spot) => { if (spot) log(`[trail] 暗い所に松明を置いた: ${spot.x},${spot.y},${spot.z}`) })
      .catch((e) => {
        retryAt = Date.now() + (e.swapped ? RETRY_SWAPPED_MS : RETRY_MS)
        log(`[trail] 松明を置けなかった: ${e.message}`)
      })
      .finally(() => { busy = false })
  }, TRAIL_CHECK_MS)
  return () => clearInterval(timer)
}
