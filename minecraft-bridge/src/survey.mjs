// 街の候補地の調査（docs/design/16_town_site.md §3）。
//
// 候補地は、調査を始めたときの家（まだなければボットの位置）を中心に、その場所と 8 方向
// SURVEY_RADIUS 先の 9 か所。ボットがそこまで行き（SURVEY_REACH 以内）、読み込まれたチャンクから
// まわり SITE_RADIUS の地形を数字にする。数字だけを記憶に残し、選ぶのは Gemini（数字を見て選ぶ）。

import vec3Pkg from 'vec3'
import { nearbyEntities, round } from './observe.mjs'
import { HUNTABLE } from './knowledge.mjs'
import { exposed } from './world.mjs'

const { Vec3 } = vec3Pkg
export const SURVEY_RADIUS = 96 // 承認された 64〜160m の範囲のうち、行き来が 2 レッグで済む距離
export const SITE_RADIUS = 32 // 候補地のまわりで数える範囲
export const SURVEY_REACH = 24 // ここまで近づいて数える（地形も動物も読み込まれている）
const PLOT = 9 // 7x7 の家（設計図の最大）と、まわり 1 マスずつの余白
const SCAN_UP = 24
const SCAN_DOWN = 40
const DIAGONAL = Math.SQRT1_2
const DIRECTIONS = [
  ['N', 0, -1], ['NE', DIAGONAL, -DIAGONAL], ['E', 1, 0], ['SE', DIAGONAL, DIAGONAL],
  ['S', 0, 1], ['SW', -DIAGONAL, DIAGONAL], ['W', -1, 0], ['NW', -DIAGONAL, -DIAGONAL]
]
const STONE = /^(stone|andesite|diorite|granite|deepslate|tuff)$/
const COAL = /^(deepslate_)?coal_ore$/
const IRON = /^(deepslate_)?iron_ore$/
const LOG = /^(?!stripped_).*_log$/
const BAD_GROUND = /(water|lava|ice|snow$|sand$|gravel$)/

// 調べる 9 か所: その場所（here）と 8 方向
export function plannedSites (center) {
  return [
    { id: 'here', x: Math.floor(center.x), z: Math.floor(center.z) },
    ...DIRECTIONS.map(([id, dx, dz]) => ({ id, x: Math.floor(center.x + dx * SURVEY_RADIUS), z: Math.floor(center.z + dz * SURVEY_RADIUS) }))
  ]
}

// 調査の計画（最初に要ったときに、家かボットの位置を中心に 1 回だけ作る）
export function ensureSurvey (state, bot) {
  if (!state.survey) {
    const c = state.home?.inside ?? bot.entity.position
    state.survey = { center: { x: Math.floor(c.x), z: Math.floor(c.z) }, sites: plannedSites(c) }
  }
  return state.survey
}

export const surveyedSites = (state) => (state.survey?.sites ?? []).filter((s) => state.memory?.sites?.[s.id])

// x,z の地面（木は飛ばす）: { y, name }、読み込まれていなければ null
export function surfaceAt (bot, x, z, yHint) {
  for (let y = yHint + SCAN_UP; y >= yHint - SCAN_DOWN; y--) {
    const b = bot.blockAt(new Vec3(x, y, z))
    if (!b) return null
    if (b.name.endsWith('_leaves') || LOG.test(b.name)) continue
    if (b.boundingBox === 'empty' && !/water|lava/.test(b.name)) continue
    return { y, name: b.name }
  }
  return { y: yHint - SCAN_DOWN, name: 'unknown' }
}

// 候補地のまわりを数字にする（Gemini に渡す表の 1 行）
export function surveySite (bot, state, site) {
  const yHint = Math.floor(bot.entity.position.y)
  const heights = new Map()
  let loaded = 0
  let water = 0
  let steep = 0
  const inside = (dx, dz) => dx * dx + dz * dz <= SITE_RADIUS * SITE_RADIUS
  for (let dx = -SITE_RADIUS; dx <= SITE_RADIUS; dx++) {
    for (let dz = -SITE_RADIUS; dz <= SITE_RADIUS; dz++) {
      if (!inside(dx, dz)) continue
      const g = surfaceAt(bot, site.x + dx, site.z + dz, yHint)
      if (!g) continue
      loaded++
      if (/water|lava/.test(g.name)) water++
      heights.set(`${dx},${dz}`, g)
    }
  }
  for (const [key, g] of heights) {
    const [dx, dz] = key.split(',').map(Number)
    const east = heights.get(`${dx + 1},${dz}`)
    const south = heights.get(`${dx},${dz + 1}`)
    if ((east && Math.abs(east.y - g.y) >= 3) || (south && Math.abs(south.y - g.y) >= 3)) steep++
  }
  // 重ならない 9x9 の区画のうち、全部が読み込まれた乾いた地面で、高低差が 1 以内のもの
  let plots = 0
  for (let tx = -SITE_RADIUS; tx + PLOT <= SITE_RADIUS; tx += PLOT) {
    for (let tz = -SITE_RADIUS; tz + PLOT <= SITE_RADIUS; tz += PLOT) {
      let lo = Infinity
      let hi = -Infinity
      let ok = true
      for (let dx = tx; dx < tx + PLOT && ok; dx++) {
        for (let dz = tz; dz < tz + PLOT && ok; dz++) {
          const g = heights.get(`${dx},${dz}`)
          if (!g || BAD_GROUND.test(g.name)) ok = false
          else { lo = Math.min(lo, g.y); hi = Math.max(hi, g.y) }
        }
      }
      if (ok && hi - lo <= 1) plots++
    }
  }
  const center = new Vec3(site.x, yHint, site.z)
  const count = (pattern, needAir) => {
    const ids = Object.values(bot.registry.blocksByName).filter((b) => pattern.test(b.name)).map((b) => b.id)
    return bot.findBlocks({ point: center, matching: ids, maxDistance: SITE_RADIUS, count: 4096 })
      .filter((p) => !needAir || exposed(bot, p)).length
  }
  const animals = nearbyEntities(bot)
    .filter(({ e }) => HUNTABLE.has(e.name) && Math.hypot(e.position.x - site.x, e.position.z - site.z) <= SITE_RADIUS).length
  const from = state.survey?.center ?? site
  return {
    id: site.id,
    x: site.x,
    z: site.z,
    distance: Math.round(Math.hypot(site.x - from.x, site.z - from.z)),
    flat_plots: plots,
    water_pct: loaded ? round((water / loaded) * 100, 0) : 0,
    steep_pct: loaded ? round((steep / loaded) * 100, 0) : 0,
    stone: count(STONE, true),
    coal: count(COAL, true),
    iron: count(IRON, true),
    logs: count(LOG, false),
    lava: count(/^lava$/, false),
    animals,
    loaded_pct: Math.min(100, round((loaded / (Math.PI * SITE_RADIUS * SITE_RADIUS)) * 100, 0))
  }
}
