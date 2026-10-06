// 野営地: 家も寝床も掘れる地面もない夜に、立った所を土などで囲んで朝を待つ（docs/design/89_camp.md）。
//
// 縦穴（burrow.mjs）は、平らな掘れる地面が要る。砂浜・石の上・水のそばでは使えない。野営地は、立っている
// 1 マス（足と頭）のまわりの四方を 3 段（足・頭・天井の高さ）と、真上の天井を置く。置くブロックは掘った土か
// 丸石か板（持っていなければ、近くの土を素手で掘って集める）。囲めば inBurrow（足と頭の四方と天井が固い）が
// 真になるので、夜の過ごし方（stay_burrowed）・反射の「中にいる」扱いは縦穴と同じになる。
// 場所の選び方と置く順だけをここに置き、掘る・歩くは primitives.mjs の wall_in。
import vec3Pkg from 'vec3'
import { holdsWater } from './breath.mjs'
import { inHouse } from './home.mjs'
import { facilityAt } from './facilities.mjs'
import { FILL_ITEMS } from './observe.mjs'
import { digTargets } from './world.mjs'

const { Vec3 } = vec3Pkg

export const CAMP_RADIUS = 5 // 立っている所から、この範囲で囲む場所を探す
export const HAND_BLOCKS = /^(dirt|grass_block|coarse_dirt|podzol|dirt_path|rooted_dirt|mycelium)$/ // 素手で掘れて、土が落ちるブロック
const SIDES = [[1, 0], [-1, 0], [0, 1], [0, -1]]
const FLUID = /water|lava/
const key = (p) => `${p.x},${p.y},${p.z}`

const solid = (b) => !!b && b.boundingBox === 'block'
const free = (b) => !!b && b.boundingBox === 'empty' && !FLUID.test(b.name)
const isPlanks = (n) => n.endsWith('_planks')

// 囲むのに使える持ち物（落ちる砂・砂利は天井が落ちるので使わない）。土を先に
export const campItems = (bot) => {
  const items = bot.inventory.items()
  return [...FILL_ITEMS.flatMap((n) => items.filter((i) => i.name === n)), ...items.filter((i) => isPlanks(i.name))]
}
export const campCount = (bot) => campItems(bot).reduce((n, i) => n + i.count, 0)
export const campItem = (bot) => campItems(bot)[0] ?? null

// 足の高さ y の 4 方と、頭・天井の高さの 4 方、真上の天井 → { ring: 12 セル, roof }
function shell (feet) {
  const ring = []
  for (let dy = 0; dy <= 2; dy++) for (const [dx, dz] of SIDES) ring.push(feet.offset(dx, dy, dz))
  return { ring, roof: feet.offset(0, 2, 0) }
}

// feet に囲める場所か。だめなら理由。よければ { feet, ring, roof, need, floor }
// need: 置くブロックの数（すでに固いセルは置かない）
export function campAt (bot, state, feet) {
  const head = feet.offset(0, 1, 0)
  const floor = bot.blockAt(feet.offset(0, -1, 0))
  const f = bot.blockAt(feet)
  const h = bot.blockAt(head)
  if (!f || !h || !floor) return 'the ground there is not loaded'
  if (holdsWater(f) || holdsWater(h) || FLUID.test(f.name) || FLUID.test(h.name)) return 'water at the spot: you cannot stand there'
  if (!free(f) || !free(h)) return 'no free space to stand there'
  if (!solid(floor) || FLUID.test(floor.name)) return 'no solid ground under the spot'
  if (inHouse(state, feet, 1)) return 'part of the house or a build'
  const fac = facilityAt(state, feet, 1)
  if (fac) return `part of the facility ${fac}`
  const { ring, roof } = shell(feet)
  const cells = [...ring, roof]
  let need = 0
  for (const p of cells) {
    const b = bot.blockAt(p)
    if (!b) return 'the ground next to it is not loaded'
    if (b.name === 'lava') return 'lava next to the spot'
    if (!solid(b)) need++
  }
  return { feet, ring, roof, need, floor: floor.position }
}

// 近い順に、囲める場所を探す。ブロックが少なくて済む場所を先に（同じなら近い所）。なければ断られた理由
export function campSite (bot, state, radius = CAMP_RADIUS) {
  const me = bot.entity.position.floored()
  const found = []
  const reasons = new Map()
  for (let dx = -radius; dx <= radius; dx++) {
    for (let dz = -radius; dz <= radius; dz++) {
      for (const dy of [0, 1, -1]) {
        const r = campAt(bot, state, me.offset(dx, dy, dz))
        if (typeof r === 'string') {
          if (dx === 0 && dz === 0 && dy === 0) reasons.set(r, (reasons.get(r) ?? 0) + 1)
          continue
        }
        found.push({ ...r, d: Math.hypot(dx, dz) + Math.abs(dy) })
        break
      }
    }
  }
  if (!found.length) return { site: null, why: [...reasons.keys()].join('; ') || 'no flat ground with free space nearby' }
  found.sort((a, b) => a.need - b.need || a.d - b.d)
  return { site: found[0] }
}

let cache = null
export function campSiteCached (bot, state, now = Date.now()) {
  const k = bot.entity.position.floored().toString()
  if (cache && cache.key === k && now - cache.at < 5000) return cache.result
  cache = { key: k, at: now, result: campSite(bot, state) }
  return cache.result
}

// 足りないブロックを素手で掘って集められる土（囲みと足元は掘らない）: 近い順
export function campSources (bot, state, site) {
  const keep = campKeep(site)
  return ['dirt', 'grass_block', 'coarse_dirt', 'podzol'].flatMap((n) => digTargets(bot, state, n, 12))
    .filter((p) => !keep.has(key(p)) && HAND_BLOCKS.test(bot.blockAt(p)?.name ?? ''))
    .sort((a, b) => a.distanceTo(site.feet) - b.distanceTo(site.feet))
}

// 囲む材料がそろうか: 持っているか、近くの土で足りるか。足りなければ理由
export function campMaterials (bot, state, site) {
  const have = campCount(bot)
  if (have >= site.need) return null
  const sources = campSources(bot, state, site).length
  if (have + sources >= site.need) return null
  return `a camp takes ${site.need} blocks: you carry ${have} and there is dirt for ${sources} more within reach`
}

// 土を集めるときに掘ってはいけないセル（囲みと足元、立つ場所）
export function campKeep (site) {
  return new Set([site.feet, site.feet.offset(0, 1, 0), site.floor, ...site.ring, site.roof].map(key))
}

// target に置くときに寄りかかる隣のブロックと向き（下を先に）。立つ 2 マス（forbid）は空気で固くないので選ばれない
export function placeRef (bot, target) {
  for (const [dx, dy, dz] of [[0, -1, 0], [1, 0, 0], [-1, 0, 0], [0, 0, 1], [0, 0, -1], [0, 1, 0]]) {
    const ref = bot.blockAt(target.offset(dx, dy, dz))
    if (solid(ref)) return { ref, face: new Vec3(-dx, -dy, -dz) }
  }
  return null
}

// 囲む: 下の段から、四方 → 天井。置けないセルがあれば理由つきで投げる。置いた数を返す
export async function buildCamp (bot, site, signal) {
  const layers = [0, 1, 2].map((dy) => site.ring.filter((p) => p.y === site.feet.y + dy))
  let placed = 0
  for (const cell of [...layers.flat(), site.roof]) {
    signal?.throwIfAborted()
    if (solid(bot.blockAt(cell))) continue
    const item = campItem(bot)
    if (!item) throw new Error(`ran out of blocks after placing ${placed}`)
    const support = placeRef(bot, cell)
    if (!support) throw new Error(`nothing to place against at ${cell.x},${cell.y},${cell.z}`)
    await bot.equip(item, 'hand')
    let ok = false
    for (let attempt = 0; attempt < 2 && !ok; attempt++) {
      try {
        await bot.lookAt(cell.offset(0.5, 0.5, 0.5), true)
        await bot.placeBlock(support.ref, support.face)
      } catch (e) {
        if (attempt) throw new Error(`could not place at ${cell.x},${cell.y},${cell.z}: ${e.message}`)
      }
      await bot.waitForTicks(3)
      ok = solid(bot.blockAt(cell))
    }
    if (!ok) throw new Error(`the block at ${cell.x},${cell.y},${cell.z} did not stay`)
    placed++
  }
  return placed
}

// 朝に出る道: 囲いの壁の 1 方を、足と頭の 2 マスぶん掘って歩いて出る。向こうが空いていて、足元が地面の方向 → { dir, cells } か null。
// 縦穴の壁は自然の地面で、向こうは固いので、ここには当たらない（縦穴は上を壊して出る）
export function campExit (bot) {
  const feet = bot.entity.position.floored()
  for (const [dx, dz] of SIDES) {
    const wall = [feet.offset(dx, 0, dz), feet.offset(dx, 1, dz)]
    const out = [feet.offset(2 * dx, 0, 2 * dz), feet.offset(2 * dx, 1, 2 * dz)]
    if (!wall.every((p) => solid(bot.blockAt(p)))) continue
    if (!out.every((p) => free(bot.blockAt(p)))) continue
    if (!solid(bot.blockAt(feet.offset(2 * dx, -1, 2 * dz)))) continue
    return { dir: [dx, dz], cells: [wall[1], wall[0]], outside: out[0] }
  }
  return null
}
