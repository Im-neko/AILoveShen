// 共通の状態（GET /state）: 見張りの質問（Jev）と、Gemini の道具の選択が見る 1 つの書式。
// 要約だけを入れる（生データはトークンが 4〜20 倍で、正答の確率も低かった）。質問ごとの専用の
// 状態は作らない: 作ると、場面ごとのコードに戻る（設計書 19 §11.2、21 §4）。
import { round, bearing, dayPhase, nearbyEntities, isHostile, inventoryCounts } from './observe.mjs'
import { isInside } from './home.mjs'
import vec3Pkg from 'vec3'

const { Vec3 } = vec3Pkg

export const LOOK_RADIUS = 3
const UP = 4 // 足元からこの高さまで上を見る。これより高い壁は「#」
const DOWN = 6 // 足元からこの深さまで下を見る。これより深い穴は「v」
const MOBS_SHOWN = 5
const MOB_RADIUS = 24

const solid = (b) => b?.boundingBox === 'block'
const liquid = (b) => b?.name === 'water' ? '~' : b?.name === 'lava' ? '!' : null

// 1 つの列（x, z）: 足元の高さ feetY から見た「立てる高さ」の差。立てる高さは、その列で上に
// 空きがある一番高い固いブロックの上。水・溶岩はその印
function column (bot, x, feetY, z) {
  let seenAir = false
  for (let y = feetY + UP; y >= feetY - DOWN; y--) {
    const b = bot.blockAt(new Vec3(x, y, z))
    if (b === null || b === undefined) return '?'
    const l = liquid(b)
    if (l && seenAir) return l
    if (solid(b)) {
      if (!seenAir) return '#'
      const d = y + 1 - feetY
      return d === 0 ? ' 0' : d > 0 ? `+${d}` : `${d}`
    }
    if (l) return l
    seenAir = true
  }
  return 'v'
}

// まわりの形: 北を上にした (2r+1)x(2r+1) の格子。各セルは足元から見た立てる高さの差（+1 は
// 1 段上がれば立てる、-3 は 3 段下、# は上まで壁、v は深い穴、~ 水、! 溶岩、? 読み込まれていない）。
// 真ん中の @ が自分
export function lookAround (bot, radius = LOOK_RADIUS) {
  const me = bot.entity.position.floored()
  const rows = []
  const cells = []
  for (let dz = -radius; dz <= radius; dz++) {
    const row = []
    for (let dx = -radius; dx <= radius; dx++) {
      if (!dx && !dz) { row.push(' @'); continue }
      const cell = column(bot, me.x + dx, me.y, me.z + dz)
      row.push(cell.padStart(2, ' '))
      cells.push({ dx, dz, cell })
    }
    rows.push(row.join(' '))
  }
  const near = cells.filter(({ dx, dz }) => Math.abs(dx) <= 1 && Math.abs(dz) <= 1)
  const high = (c) => c === '#' || (/^\+\d/.test(c) && Number(c) >= 2)
  const head = bot.blockAt(me.offset(0, 2, 0))
  const under = bot.blockAt(me.offset(0, -1, 0))
  return {
    legend: 'north is up; each cell is the height to stand at relative to your feet (+1 one step up, -3 three down, # wall higher than +4, v drop deeper than -6, ~ water, ! lava, ? not loaded); @ is you',
    grid: rows,
    walls_around: near.filter(({ cell }) => high(cell)).length, // 8 のうち、歩いて上がれない（2 段以上）隣
    head_blocked: solid(head),
    standing_on: under?.name ?? null
  }
}

function mobs (bot) {
  const me = bot.entity.position
  return nearbyEntities(bot)
    .filter(({ e, dist }) => e.type !== 'player' && e.name !== 'item' && e.type !== 'other' && e.type !== 'projectile' && dist <= MOB_RADIUS)
    .slice(0, MOBS_SHOWN)
    .map(({ e, dist }) => ({ id: e.id, name: e.name, hostile: isHostile(bot, e), distance_m: round(dist), direction: bearing(me, e.position), height_diff_m: round(e.position.y - me.y) }))
}

// needsOf(bot, state): 欲求（goals.mjs の needs）。テストでは差し替える
export function sharedState (bot, state, needsOf) {
  const p = bot.entity.position
  return {
    action: state.current?.progress?.snapshot() ?? null,
    self: {
      position: { x: round(p.x), y: round(p.y), z: round(p.z) },
      health: round(bot.health),
      food: bot.food,
      held_item: bot.heldItem?.name ?? null,
      in_water: !!bot.entity.isInWater,
      in_home: isInside(bot, state.home)
    },
    surroundings: lookAround(bot),
    mobs: mobs(bot),
    needs: needsOf(bot, state),
    inventory: inventoryCounts(bot),
    time: dayPhase(bot.time.timeOfDay)
  }
}
