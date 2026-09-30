// ボットの状況を簡潔にまとめた観測。行動の列挙と /observe で共有する。

import vec3Pkg from 'vec3'

const { Vec3 } = vec3Pkg

export const MOB_RADIUS = 48
export const MOB_LIMIT = 16
export const DROP_RADIUS = 24
export const THREAT_RADIUS = 16
const MELEE_RADIUS = 4 // ここまで近い敵対モブは、葉や角に隠れていても脅威とする
export const HISTORY = 5

// Mineflayer はこれらを "hostile" とするが、挑発されたときしか攻撃しない。
const NEUTRAL = new Set(['enderman', 'zombified_piglin', 'piglin', 'wolf', 'bee', 'llama', 'trader_llama',
  'polar_bear', 'dolphin', 'panda', 'goat', 'iron_golem'])
// クモは日中は中立。
const DAY_NEUTRAL = new Set(['spider', 'cave_spider'])
// 近づくと爆発する: 家の近くでは近接で戦わない
export const EXPLODES = new Set(['creeper'])
// 空の下では日光で燃える（ハスクとウィザースケルトンは燃えない）
export const BURNS = new Set(['zombie', 'zombie_villager', 'skeleton', 'stray', 'drowned', 'phantom'])
const FULL_SKY_LIGHT = 15

// いまいる場所でモブが日光で燃えるか（ヘルメットは確認しない）
export function burningInDaylight (bot, e) {
  if (!BURNS.has(e.name) || dayPhase(bot.time.timeOfDay) !== 'day') return false
  return bot.blockAt(e.position.offset(0, e.height ?? 1.8, 0).floored())?.skyLight === FULL_SKY_LIGHT
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
  if (DAY_NEUTRAL.has(e.name) && dayPhase(bot.time.timeOfDay) === 'day') return false
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
      in_water: !!bot.entity.isInWater,
      riding: bot.vehicle?.name ?? null
    },
    inventory: inventoryCounts(bot),
    mobs,
    drops,
    recent_actions: history.slice(-HISTORY),
    ...extra
  }
}

// 昼でも暗い: 洞窟や深い屋根の下など、空の光が届かず近くに光源もない。光のデータではなくブロックから
// 判定する: mineflayer の 1.21.4 の光は、サーバーが明るいとした屋外で空の光 0 を読んだ
// （location_check の述語で確認）ので信用できない。覆い（頭上の不透明ブロック）だけでは、木陰や
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

// 地下にいる（頭の上がふさがっていて、海面より下）: 夜でも家に帰らなくてよい（ここは夜も昼も暗い）
const SEA_LEVEL = 62
export const isUnderground = (bot) => bot.entity.position.y < SEA_LEVEL - 2 && isCovered(bot)

export function isDark (bot) {
  if (!isCovered(bot) || skyNear(bot)) return false
  const ids = LIGHT_SOURCES.map((n) => bot.registry.blocksByName[n]?.id).filter((id) => id !== undefined)
  return !bot.findBlock({ matching: ids, maxDistance: LIT_RADIUS })
}

// 身につけた防具とオフハンド（インベントリのウィンドウのスロット）。空なら null
const EQUIPMENT_SLOTS = { head: 5, chest: 6, legs: 7, feet: 8, off_hand: 45 }

export function equipment (bot) {
  return Object.fromEntries(Object.entries(EQUIPMENT_SLOTS).map(([part, slot]) => [part, bot.inventory.slots[slot]?.name ?? null]))
}
