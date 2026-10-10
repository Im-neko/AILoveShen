// 街の道を敷く（設計書 167）。道の細い列（Python の理想図が決める。幅 3 の四角）を受け取り、まだ土の道になっていない
// 地面を、シャベルで土の道（dirt_path）にする。家の戸口までは、一番近い列から 1 マス幅でつなぐ。
//
// 敷く所は地面の列ごとに見る:
//   todo   シャベルで踏み固められる地面（草・土・粗い土・ポドゾル・根のある土・菌糸）。上が空気か草花
//   done   もう土の道、または硬い歩ける地面（石・砂利・砂・板など）
//   kept   家・建物・畑・植林場の上（触らない）
//   unfit  水・溶岩・木（原木・葉）・上がふさがれている（敷かない。道は回り込まない）
//   unseen まだ読み込まれていない
import vec3Pkg from 'vec3'
import { inHouse } from './home.mjs'
import { facilityAt } from './facilities.mjs'

const { Vec3 } = vec3Pkg

export const SHOVELABLE = /^(grass_block|dirt|coarse_dirt|podzol|rooted_dirt|mycelium)$/
const UNFIT = /(water|lava|_log|_wood|_leaves|cactus|ice|farmland|snow|bubble_column)/
const SCAN = 7 // 家の床の高さから、これだけ上下の地面を探す
const DOOR_LINK_MAX = 20 // 戸口まで、列からこれより遠ければつながない（マス）
const CACHE_TICKS = 100

export function setRoads (state, strips) {
  if (!Array.isArray(strips)) throw new Error('strips must be a list')
  const clean = strips.map((s) => ({ min_x: Math.floor(s.min_x), min_z: Math.floor(s.min_z), max_x: Math.floor(s.max_x), max_z: Math.floor(s.max_z) }))
  if (clean.some((s) => ![s.min_x, s.min_z, s.max_x, s.max_z].every(Number.isFinite) || s.max_x < s.min_x || s.max_z < s.min_z)) throw new Error('each strip needs min_x <= max_x and min_z <= max_z')
  if (clean.length > 400) throw new Error('too many strips')
  state.roads = { strips: clean }
  state.roadsCache = null
  return clean.length
}

// 計画に合わないので壊してよい既存の施設の名前（街の理想図の clear。置き換わる）
export function setClearMarks (state, names) {
  if (!Array.isArray(names) || names.some((n) => typeof n !== 'string')) throw new Error('clear must be a list of names')
  state.clearMarks = [...new Set(names)]
  return state.clearMarks.length
}

// 敷く列の一覧（x, z）。列の四角に、戸口への 1 マス幅のつなぎを足す
export function roadColumns (state) {
  const strips = state.roads?.strips ?? []
  const seen = new Set()
  const out = []
  const add = (x, z) => {
    const k = `${x},${z}`
    if (seen.has(k)) return
    seen.add(k)
    out.push({ x, z })
  }
  for (const s of strips) for (let x = s.min_x; x <= s.max_x; x++) for (let z = s.min_z; z <= s.max_z; z++) add(x, z)
  const door = state.home?.outside
  if (door && out.length) {
    let best = null
    for (const c of out) {
      const d = Math.abs(c.x - door.x) + Math.abs(c.z - door.z)
      if (!best || d < best.d) best = { c, d }
    }
    if (best.d > 0 && best.d <= DOOR_LINK_MAX) {
      const step = (a, b) => Math.sign(b - a)
      let { x, z } = best.c
      while (x !== door.x) { x += step(x, door.x); add(x, z) }
      while (z !== door.z) { z += step(z, door.z); add(x, z) }
    }
  }
  return out
}

// 1 列の今: { kind, ground? }
export function columnKind (bot, state, x, z, y0) {
  for (let y = y0 + SCAN; y >= y0 - SCAN; y--) {
    const b = bot.blockAt(new Vec3(x, y, z))
    if (!b) return { kind: 'unseen' }
    if (b.boundingBox === 'empty' && !/water|lava/.test(b.name)) continue
    if (UNFIT.test(b.name)) return { kind: 'unfit', why: b.name }
    if (inHouse(state, b.position, 0) || facilityAt(state, b.position, 0)) return { kind: 'kept' }
    if (b.name === 'dirt_path') return { kind: 'done', ground: b }
    if (!SHOVELABLE.test(b.name)) return { kind: b.boundingBox === 'block' ? 'done' : 'unfit', ground: b, ...(b.boundingBox === 'block' ? {} : { why: b.name }) }
    const above = bot.blockAt(b.position.offset(0, 1, 0))
    if (!above) return { kind: 'unseen' }
    if (above.name !== 'air' && above.boundingBox !== 'empty') return { kind: 'unfit', why: `${above.name} above` }
    return { kind: 'todo', ground: b }
  }
  return { kind: 'unfit', why: 'no ground within reach of the height' }
}

// 道の今: { total, done, todo: [Vec3], unfit, kept, unseen }。total は敷ける所（済み + これから）
export function roadStatus (bot, state, { fresh = false } = {}) {
  if (!state.roads?.strips?.length) return null
  const age = Number(bot.time?.age ?? 0)
  const cached = state.roadsCache
  if (!fresh && cached && age - cached.age < CACHE_TICKS && age >= cached.age) return cached.status
  const y0 = state.home ? state.home.min.y - 1 : Math.floor(bot.entity.position.y) - 1
  const status = { total: 0, done: 0, todo: [], unfit: 0, kept: 0, unseen: 0 }
  for (const { x, z } of roadColumns(state)) {
    const c = columnKind(bot, state, x, z, y0)
    if (c.kind === 'todo') { status.total++; status.todo.push(c.ground.position) } else if (c.kind === 'done') { status.total++; status.done++ } else status[c.kind]++
  }
  state.roadsCache = { age, status }
  return status
}

const shovelOf = (bot) => bot.inventory.items().find((i) => /_shovel$/.test(i.name))

// 敷く手の候補: シャベルを持っていて、これから敷く所が近くにあるとき
export function paveCandidate (bot, state, dist) {
  const s = roadStatus(bot, state)
  if (!s?.todo.length || !shovelOf(bot)) return []
  const me = bot.entity.position
  const near = s.todo.reduce((a, b) => (a.distanceTo(me) <= b.distanceTo(me) ? a : b))
  if (near.distanceTo(me) > 96) return []
  return [{
    id: `lay the dirt path of the town's roads (${s.todo.length} of ${s.total} cells left)`,
    verb: 'pave',
    target: 'road',
    inPlace: true,
    pos: near,
    distance: dist(bot, near),
    purpose: 'the town is laid out with roads before buildings go up: the planned roads are trodden into dirt paths with a shovel, nearest cell first'
  }]
}
