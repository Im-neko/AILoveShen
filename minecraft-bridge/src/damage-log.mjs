// 受けたダメージの原因を、ブリッジのログに残す（2026-10-01: 採掘中に体力が 19.5 から 1.5 まで落ちて死んだが、
// 長い行動の間は 1 秒ごとの記録がなく、何で受けたか分からなかった）。原因は推定: 溶岩・火・溺れ・飢え・落下・
// 状態異常（毒・衰弱）、燃焼、近くの敵対モブ、どれでもなければ不明。
import { isHostile } from './observe.mjs'
import { airLevel, headUnderwater, trackHead } from './breath.mjs'

const FALL_VY = -0.8 // 直近の落下の速さ（ブロック/tick）。これ以上速く落ちていたら落下ダメージ
const MOB_RANGE = 6
const MIN_LOSS = 0.4
const ON_FIRE_FLAG = 0x01 // エンティティの状態フラグ（metadata[0]）の最初のビット: 燃えている
const HARMFUL = /^(poison|wither|instant_?damage)$/i // 毎秒体力を削る状態異常（2026-10-04 11:31: 14 秒、毎秒ちょうど 1.0 で 8.9 → 1.9。原因は不明のまま）

// いま体力を削っている状態異常（名前、強さ、残り秒）
export function harmfulEffects (bot) {
  const active = bot.entity?.effects ?? {}
  return (bot.registry?.effectsArray ?? [])
    .filter((e) => HARMFUL.test(e.name) && active[e.id])
    .map((e) => ({ name: e.name.toLowerCase(), level: (active[e.id].amplifier ?? 0) + 1, seconds: Math.round((active[e.id].duration ?? 0) / 20) }))
}

export function isBurning (bot) {
  const flags = bot.entity?.metadata?.[0]
  return typeof flags === 'number' && (flags & ON_FIRE_FLAG) !== 0
}

export function damageCause (bot, fell) {
  const feet = bot.blockAt?.(bot.entity.position.floored())
  if (feet?.name === 'lava') return 'lava'
  if (feet?.name === 'fire' || feet?.name === 'soul_fire' || feet?.name === 'magma_block') return feet.name
  // 状態異常と燃焼は、足元のブロックより先に敵を見る前に: 毒は噛んだ敵が離れても続き、火は消えるまで続く
  const effect = harmfulEffects(bot)[0]
  if (effect) return `${effect.name} (level ${effect.level}, ${effect.seconds} s left)`
  if (isBurning(bot)) return 'burning (on fire)'
  if (bot.food === 0) return 'starving (food 0)'
  if (typeof bot.oxygenLevel === 'number' && bot.oxygenLevel <= 2) return 'drowning'
  // 頭が水の中にいた時間からの見積もりでも（bot.oxygenLevel が気泡の尺度でない: 設計書 69）
  if (headUnderwater(bot)) {
    trackHead(bot)
    if (airLevel(bot) <= 2) return 'drowning (head under water, air estimated from the time under water)'
  }
  if (fell) return `fall (${fell.toFixed(1)} blocks per second)`
  const near = Object.values(bot.entities ?? {})
    .filter((e) => e !== bot.entity && e.position && isHostile(bot, e))
    .map((e) => ({ name: e.name, d: e.position.distanceTo(bot.entity.position) }))
    .filter((m) => m.d <= MOB_RANGE)
    .sort((a, b) => a.d - b.d)[0]
  if (near) return `${near.name} ${near.d.toFixed(1)}m`
  // 水の中で原因が分からないのは、溺れているのに息の値が減っていないとき（2026-10-04 y54 で 2.0 ずつ 8 回: 息の反射も動かなかった）。値を残す
  if (bot.entity.isInWater) return `unknown (in water, oxygenLevel ${bot.oxygenLevel}, head cell ${bot.blockAt(bot.entity.position.offset(0, bot.entity.eyeHeight ?? 1.62, 0))?.name}: drowning with the air value not falling?)`
  return 'unknown (no hostile within 6 m: suffocation, an unseen shot, an explosion?)'
}

// bot の health イベントのたびに、体力が減っていれば 1 行（console.log: logs/bridge.log にも入る）。
// what: いま実行している行動の名前（state.busy など。なければ空）。onDamage: 1 件ごとに { at, lost, health, cause, what } を渡す（death-report.mjs）
export function installDamageLog (bot, what = () => '', onDamage = () => {}) {
  let last = null
  let vy = 0
  bot.on('physicsTick', () => { vy = Math.min(vy * 0.9, bot.entity?.velocity?.y ?? 0) })
  bot.on('health', () => {
    const lost = last == null ? 0 : last - bot.health
    if (lost >= MIN_LOSS) {
      const p = bot.entity.position
      const fell = vy <= FALL_VY ? Math.abs(vy) * 20 : 0
      const cause = damageCause(bot, fell)
      console.log(`[damage] -${lost.toFixed(1)} -> ${bot.health.toFixed(1)}/20 food ${bot.food}: ${cause} at ${Math.floor(p.x)},${Math.floor(p.y)},${Math.floor(p.z)}${what() ? ` while ${what()}` : ''}`)
      onDamage({ at: Date.now(), lost: Math.round(lost * 10) / 10, health: Math.round(bot.health * 10) / 10, cause, what: what() })
    }
    last = bot.health
  })
}
