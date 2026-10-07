// ボットの状況を簡潔にまとめた観測。行動の列挙と /observe で共有する。

import vec3Pkg from 'vec3'
import { underwater } from './breath.mjs'
import { isSwimming } from './swim.mjs'
import { blockLightAt, skyLightAt, MIN_LIGHT } from './light.mjs'

const { Vec3 } = vec3Pkg

export const MOB_RADIUS = 48
export const MOB_LIMIT = 16
export const DROP_RADIUS = 24
export const THREAT_RADIUS = 16
const MELEE_RADIUS = 4 // ここまで近い敵対モブは、葉や角に隠れていても脅威とする
// 観測に載せる直近の行動の数（Jev の選択と、目標・道具・実況のプロンプトの「直近の行動」。
// 少ないと、預けては取り出す往復のような、前の手を打ち消す選択に気づけない）
export const HISTORY = 8
// 覚えておく行動の数（読むのは末尾の HISTORY 件。保存もされるので、切り詰めないと増え続ける）
const HISTORY_KEPT = 100
export function pushHistory (state, entry) {
  state.history.push(entry)
  if (state.history.length > HISTORY_KEPT) state.history.splice(0, state.history.length - HISTORY_KEPT)
}

// Mineflayer はこれらを "hostile" とするが、挑発されたときしか攻撃しない。
const NEUTRAL = new Set(['enderman', 'zombified_piglin', 'piglin', 'wolf', 'bee', 'llama', 'trader_llama',
  'polar_bear', 'dolphin', 'panda', 'goat', 'iron_golem'])
// クモは日中、明るい所では中立（地下や暗い所では敵）。洞窟クモは昼も常に敵
const DAY_NEUTRAL = new Set(['spider'])
// 近づくと爆発する: 家の近くでは近接で戦わない
export const EXPLODES = new Set(['creeper'])
// 空の下では日光で燃える（ハスクとウィザースケルトンは燃えない）
export const BURNS = new Set(['zombie', 'zombie_villager', 'skeleton', 'stray', 'drowned', 'phantom'])
const FULL_SKY_LIGHT = 15

// いまいる場所でモブが日光で燃えるか（ヘルメットは確認しない）
export function burningInDaylight (bot, e) {
  if (!BURNS.has(e.name) || dayPhase(bot.time.timeOfDay) !== 'day') return false
  return skyLightAt(bot, e.position.offset(0, e.height ?? 1.8, 0)) === FULL_SKY_LIGHT
}

export const round = (v, d = 1) => Math.round(v * 10 ** d) / 10 ** d

export function bearing (from, to) {
  // `from` から見た `to` の方位（Minecraft: -Z が北、+X が東）
  const deg = (Math.atan2(to.x - from.x, -(to.z - from.z)) * 180 / Math.PI + 360) % 360
  return ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'][Math.round(deg / 45) % 8]
}

export function dayPhase (timeOfDay) {
  if (timeOfDay < 12000) return 'day'
  if (timeOfDay < 13000) return 'dusk'
  if (timeOfDay < 23000) return 'night'
  return 'dawn'
}

export const isLog = (name) => name.endsWith('_log')
// 穴を埋めるのに使うブロック（持っているもの）: 土を先に（整地、家のドアの前の穴）
export const FILL_ITEMS = ['dirt', 'coarse_dirt', 'cobblestone', 'cobbled_deepslate']
export function fillItem (bot) {
  const items = bot.inventory.items()
  for (const name of FILL_ITEMS) {
    const item = items.find((i) => i.name === name)
    if (item) return item
  }
  return null
}

// 穴を埋めるブロックを持っている数
export const fillCount = (bot) => bot.inventory.items().filter((i) => FILL_ITEMS.includes(i.name)).reduce((n, i) => n + i.count, 0)

// ブロックの状態（耕地の moisture 0–7、作物の age、ドアの open など）。なければ null
export function blockProps (block) {
  const props = block?.getProperties?.() ?? {}
  return Object.keys(props).length ? props : null
}

// ボットが立っているセル（pathfinder の出発点と同じ決め方）: 足が半端な高さで、床を切り下げたセルが
// 空でない（耕地、土の道、ハーフブロック、ソウルサンド、泥など）なら 1 つ上
export function standingCell (bot) {
  const pos = bot.entity.position
  const p = pos.floored()
  const b = bot.blockAt(p)
  return b && pos.y - p.y > 0.001 && bot.entity.onGround && b.boundingBox !== 'empty' ? p.offset(0, 1, 0) : p
}

// 技の t.block_at: その座標のブロックの名前と状態
export function blockInfo (bot, { x, y, z }) {
  const p = { x: Math.floor(Number(x)), y: Math.floor(Number(y)), z: Math.floor(Number(z)) }
  const b = bot.blockAt(new Vec3(p.x, p.y, p.z))
  if (!b) return { name: null, error: 'not loaded' }
  const props = blockProps(b)
  return { name: b.name, ...p, ...(props ? { props } : {}) }
}
export const isPlanks = (name) => name.endsWith('_planks')

// 時間帯と、ワールドの何日目か（寝て飛ばした夜も数える。最初の時刻が届くまでは null）
export function timeOf (bot) {
  return { phase: dayPhase(bot.time.timeOfDay), time_of_day: bot.time.timeOfDay, day: bot.time.day, raining: bot.isRaining }
}

export function inventoryCounts (bot) {
  const counts = {}
  for (const it of bot.inventory.items()) counts[it.name] = (counts[it.name] ?? 0) + it.count
  return counts
}

export function countMatching (bot, pred) {
  return bot.inventory.items().filter((i) => pred(i.name)).reduce((n, i) => n + i.count, 0)
}

export function nearbyEntities (bot) {
  const me = bot.entity.position
  return Object.values(bot.entities)
    .filter((e) => e !== bot.entity && e.position)
    .map((e) => ({ e, dist: e.position.distanceTo(me) }))
    .sort((a, b) => a.dist - b.dist)
}

export function isHostile (bot, e) {
  if (e.type !== 'hostile' || NEUTRAL.has(e.name)) return false
  // 昼の洞窟のクモは敵: 日中でも暗いと襲ってくる（2026-10-07 昼、y 55 の洞窟でクモに毎秒 2.0 ずつ 12 回削られて死んだが、
  // 「昼のクモは中立」で脅威にも原因にも数えられず、反射も動かなかった）
  if (DAY_NEUTRAL.has(e.name) && dayPhase(bot.time.timeOfDay) === 'day' && !isUnderground(bot) && !isDark(bot)) return false
  return true
}

// ボットの目とエンティティを結ぶ直線を固体がさえぎっていなければ true。
export function canSee (bot, e) {
  const eye = bot.entity.position.offset(0, bot.entity.eyeHeight ?? 1.62, 0)
  const target = e.position.offset(0, (e.height ?? 1) * 0.8, 0)
  const dir = target.minus(eye)
  const dist = dir.norm()
  if (dist === 0) return true
  const hit = bot.world.raycast(eye, dir.normalize(), dist)
  return !hit
}

// いま実際にボットに届く敵対モブ: 近く、高さがほぼ同じで、視線が通るもの。
// 下の洞窟や壁の向こうのモブは観測には出すが、脅威にはしない。
export function threats (bot) {
  const me = bot.entity.position
  return nearbyEntities(bot).filter(({ e, dist }) =>
    dist <= THREAT_RADIUS && isHostile(bot, e) && Math.abs(e.position.y - me.y) < 3 && (dist <= MELEE_RADIUS || canSee(bot, e)))
}

export function summarize (bot, history, extra = {}) {
  const me = bot.entity.position
  const ents = nearbyEntities(bot)
  const mobs = ents
    .filter(({ e, dist }) => e.type !== 'player' && e.name !== 'item' && e.type !== 'other' && e.type !== 'projectile' && dist <= MOB_RADIUS)
    .slice(0, MOB_LIMIT)
    .map(({ e, dist }) => ({
      name: e.name,
      hostile: isHostile(bot, e),
      distance_m: round(dist),
      direction: bearing(me, e.position),
      height_diff_m: round(e.position.y - me.y),
      visible: canSee(bot, e)
    }))
  const drops = ents
    .filter(({ e, dist }) => e.name === 'item' && dist <= DROP_RADIUS)
    .map(({ e, dist }) => ({ item: e.getDroppedItem()?.name ?? 'unknown', distance_m: round(dist), direction: bearing(me, e.position) }))
  return {
    time: timeOf(bot),
    self: {
      health: round(bot.health),
      max_health: 20,
      food: bot.food,
      position: { x: round(me.x), y: round(me.y), z: round(me.z) },
      held_item: bot.heldItem ? bot.heldItem.name : null,
      equipment: equipment(bot),
      dark: isDark(bot),
      in_water: isSwimming(bot),
      ...(underwater(bot) ? { head_in_water: true, air: underwater(bot).air } : {}),
      riding: bot.vehicle?.name ?? null
    },
    inventory: inventoryCounts(bot),
    mobs,
    drops,
    recent_actions: history.slice(-HISTORY),
    ...extra
  }
}

// 昼でも暗い: 洞窟や深い屋根の下など、空の光が届かず近くに光源もない。空の光は、ブロックから判定する
// （光のデータは、足元のブロックの光だけ使う: isDark）: mineflayer の光は x が入れ替わって読めるので、
// 屋外で空の光 0 を読んだ（2026-10 の location_check）。今は light.mjs が正しく読む（設計書 104）が、
// 空が近いかは今までどおりブロックから決める。覆い（頭上の不透明ブロック）だけでは、木陰や
// 張り出しの下まで暗いことになるので、空の光が横へ 1 ブロックごとに 1 減ることから、空が開いた柱が
// 近くにあれば（空の光 8 以上 = モブが湧かない明るさ）暗くないとする
const COVER_HEIGHT = 32
const SKY_REACH = 7 // 空の光は 15 から横へ 1 ブロックごとに 1 減る: 8 以上ある距離
const LIT_RADIUS = 8 // たいまつはそのブロックで 14、1ブロックごとに 1 下がる
export const LIGHT_SOURCES = ['torch', 'wall_torch', 'lantern', 'soul_torch', 'soul_wall_torch', 'soul_lantern', 'glowstone',
  'jack_o_lantern', 'sea_lantern', 'shroomlight', 'lava', 'campfire', 'soul_campfire', 'redstone_lamp', 'end_rod', 'ochre_froglight', 'verdant_froglight', 'pearlescent_froglight']

// 光を通さないブロック（葉、ガラス、氷などの半透明は空の光を通す）
const blocksLight = (bot, b) => b?.boundingBox === 'block' && bot.registry.blocksByName[b.name]?.filterLight === 15
const roofed = (bot, from) => {
  for (let dy = 0; dy < COVER_HEIGHT; dy++) if (blocksLight(bot, bot.blockAt(from.offset(0, dy, 0)))) return true
  return false
}
const headCell = (bot) => bot.entity.position.floored().offset(0, 2, 0)

export const isCovered = (bot) => roofed(bot, headCell(bot))

// 近くに空が開いた柱がある（読み込まれていない柱は数えない）
function skyNear (bot) {
  const head = headCell(bot)
  for (let d = 1; d <= SKY_REACH; d++) {
    for (let dx = -d; dx <= d; dx++) {
      const rest = d - Math.abs(dx)
      for (const dz of rest === 0 ? [0] : [-rest, rest]) {
        const from = head.offset(dx, 0, dz)
        if (bot.blockAt(from) && !roofed(bot, from)) return true
      }
    }
  }
  return false
}

// 深い地下にいる（頭の上がふさがっていて、海面より下で、空が開いた柱が近くにない: 空の光が届かない）:
// ここは夜も昼も同じ暗さで、モブの湧き方も変わらない。夜でも家に帰らなくてよく、夕方・夜になったからと
// 行動を止めない（いずれ掘る前に帰らなければならなくなる。2026-09-30 user）。浅い洞窟の入口や崖の下は、
// 空が近いので含めない（昼は明るく、夜だけ危ない）
const SEA_LEVEL = 62
export const UNDERGROUND_BELOW = SEA_LEVEL - 2
// 頭の上の岩の厚さ（光を通さないブロックの数。COVER_HEIGHT まで）。葉・ガラスは数えない
function roofThickness (bot) {
  const head = headCell(bot)
  let n = 0
  for (let dy = 0; dy < COVER_HEIGHT; dy++) if (blocksLight(bot, bot.blockAt(head.offset(0, dy, 0)))) n++
  return n
}

// 海面より高くても、頭の上に岩が DEEP_ROOF ブロック以上あれば地下（地表が高い所の洞窟は y が 60 以上でも深い:
// 2026-10-04、地表 76 の畑の下、y63 の洞窟に、種を持ったまま降りて、地下の規則（松明がなければ地上へ、など）が
// 何も効かなかった）。家の屋根や崖の庇、木の下は薄いので含めない
export const DEEP_ROOF = 8
export const isUnderground = (bot) => {
  if (!isCovered(bot)) return false
  if (bot.entity.position.y >= UNDERGROUND_BELOW && roofThickness(bot) < DEEP_ROOF) return false
  return !skyNear(bot)
}

// 暗い所にいるか。サーバーが送った足元の光が分かれば、それで決める（光源が壁の向こうや遠くにあっても、
// 実際の明るさが答え。light.mjs）。分からないときだけ、近くの光源（LIT_RADIUS）で見る。
// 光源が足元のすぐそば（NEAR_LIT）にあれば、置いた直後で光がまだ届いていないだけなので、暗いとしない
const NEAR_LIT = 2
export function isDark (bot) {
  if (!isCovered(bot) || skyNear(bot)) return false
  const ids = LIGHT_SOURCES.map((n) => bot.registry.blocksByName[n]?.id).filter((id) => id !== undefined)
  const light = blockLightAt(bot, bot.entity.position.floored())
  if (light !== null) return light < MIN_LIGHT && !bot.findBlock({ matching: ids, maxDistance: NEAR_LIT })
  return !bot.findBlock({ matching: ids, maxDistance: LIT_RADIUS })
}

// 身につけた防具とオフハンド（インベントリのウィンドウのスロット）。空なら null
const EQUIPMENT_SLOTS = { head: 5, chest: 6, legs: 7, feet: 8, off_hand: 45 }

export function equipment (bot) {
  return Object.fromEntries(Object.entries(EQUIPMENT_SLOTS).map(([part, slot]) => [part, bot.inventory.slots[slot]?.name ?? null]))
}
