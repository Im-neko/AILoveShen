// 植林と畑（docs/design/33_planting_and_farming.md）: 述語の判定と、植える・耕す・まく場所。
//
// planted(sapling, n): 自分が植えた場所（state.plantings）に、苗木か育った木（原木）が n 本ある。
// farmed(crop, n): 家のまわりの耕地に、その作物が n マス植わっている（世界から数える）。
// どちらも世界だけから判定する。やり方（どこに、どの順で）は、道具モードの Gemini（技）か、候補で決める。
// 安全の決まり: 家・前の家・建物・建てている家の上とまわり 1 マスには植えない・耕さない。

import vec3Pkg from 'vec3'
import { inHouse } from './home.mjs'
import { isLog } from './observe.mjs'

const { Vec3 } = vec3Pkg

export const SAPLING = /(_sapling|^mangrove_propagule)$/
// 作物: 目標で使う名前 → ブロック、まくアイテム、育ちきった age
export const CROPS = {
  wheat: { block: 'wheat', seed: 'wheat_seeds', mature: 7 },
  carrots: { block: 'carrots', seed: 'carrot', mature: 7 },
  potatoes: { block: 'potatoes', seed: 'potato', mature: 7 },
  beetroots: { block: 'beetroots', seed: 'beetroot_seeds', mature: 3 }
}
const CROP_ALIASES = { wheat: 'wheat', wheat_seeds: 'wheat', carrot: 'carrots', carrots: 'carrots', potato: 'potatoes', potatoes: 'potatoes', beetroot: 'beetroots', beetroots: 'beetroots', beetroot_seeds: 'beetroots' }
export const cropOf = (item) => CROP_ALIASES[String(item)] ?? null
export const cropBySeed = (seed) => Object.keys(CROPS).find((k) => CROPS[k].seed === seed) ?? null
const SAPLING_GROUND = /^(dirt|grass_block|podzol|coarse_dirt|rooted_dirt|mud|moss_block|mycelium)$/
const TILLABLE = /^(dirt|grass_block|dirt_path)$/
export const PLANT_RING = { min: 6, max: 24 } // 家から（水平の距離）
const TREE_GAP = 4 // 苗木どうし、木との間
const WATER_REACH = 4 // 耕地が湿る水の距離
const DY = 4 // 家の床の高さから上下

const xzDist = (a, b) => Math.hypot(a.x - b.x, a.z - b.z)

export const isSaplingItem = (name) => SAPLING.test(name ?? '')

// 苗木のグループ（sapling）か 1 種類か
export function saplingMatches (spec, name) {
  return spec === 'sapling' ? SAPLING.test(name) : spec === name
}

// 植えたことを覚える（state.json に残る）
export function recordPlanting (state, pos, item) {
  state.plantings ??= []
  if (!state.plantings.some((p) => p.x === pos.x && p.y === pos.y && p.z === pos.z)) {
    state.plantings.push({ x: pos.x, y: pos.y, z: pos.z, item })
  }
}

// 植えた場所の今: { saplings, trees, unseen, gone }（見えていない場所は、あるものとして数える）
export function plantingStatus (bot, state, spec = 'sapling') {
  const out = { saplings: 0, trees: 0, unseen: 0, gone: 0 }
  for (const p of state.plantings ?? []) {
    if (!saplingMatches(spec, p.item)) continue
    const b = bot.blockAt(new Vec3(p.x, p.y, p.z))
    if (!b) out.unseen++
    else if (SAPLING.test(b.name)) out.saplings++
    else if (isLog(b.name)) out.trees++
    else out.gone++
  }
  return out
}

// 家のまわりの、その作物のブロック（耕地の上）: { planted, mature, positions }
export function cropStatus (bot, state, crop) {
  const info = CROPS[crop]
  const id = bot.registry.blocksByName[info.block]?.id
  const home = state.home
  if (id == null || !home) return { planted: 0, mature: 0, positions: [] }
  const found = bot.findBlocks({ point: home.inside, matching: id, maxDistance: PLANT_RING.max + 8, count: 256 })
    .filter((p) => bot.blockAt(p.offset(0, -1, 0))?.name === 'farmland')
  const mature = found.filter((p) => ageOf(bot.blockAt(p)) >= info.mature)
  return { planted: found.length, mature: mature.length, positions: found, maturePositions: mature }
}

export function ageOf (block) {
  const props = block?.getProperties?.() ?? {}
  return Number(props.age ?? block?.metadata ?? 0)
}

// 地面のセル（x, z）: 家の床の高さの近くで、固いブロックの上に 2 マス空いた所 → { ground, cell }
function surfaceAt (bot, x, z, y0) {
  for (let y = y0 + DY; y >= y0 - DY; y--) {
    const b = bot.blockAt(new Vec3(x, y, z))
    if (!b) return null
    if (b.boundingBox === 'empty' && !/water|lava/.test(b.name)) continue
    if (b.boundingBox !== 'block') return null
    const cell = new Vec3(x, y + 1, z)
    const above = bot.blockAt(cell)
    const head = bot.blockAt(cell.offset(0, 1, 0))
    if (!above || !head || above.name !== 'air' || head.boundingBox !== 'empty') return null
    return { ground: b, cell }
  }
  return null
}

// 植えたり耕したりしてはいけない所: 理由の文か null
export function farmRefusal (state, pos) {
  if (inHouse(state, pos, 1)) return 'it is on or next to the home or a build'
  if (!state.home) return 'there is no home yet'
  const d = xzDist(pos, state.home.inside)
  if (d < PLANT_RING.min - 2) return 'it is too close to the home (keep the door and walls clear)'
  return null
}

function ringCells (state) {
  const c = state.home.inside
  const out = []
  for (let dx = -PLANT_RING.max; dx <= PLANT_RING.max; dx++) {
    for (let dz = -PLANT_RING.max; dz <= PLANT_RING.max; dz++) {
      const d = Math.hypot(dx, dz)
      if (d >= PLANT_RING.min && d <= PLANT_RING.max) out.push([c.x + dx, c.z + dz])
    }
  }
  return out
}

const nearName = (bot, p, re, r) => {
  for (let dx = -r; dx <= r; dx++) {
    for (let dz = -r; dz <= r; dz++) {
      for (let dy = -1; dy <= 2; dy++) {
        if (re.test(bot.blockAt(p.offset(dx, dy, dz))?.name ?? '')) return true
      }
    }
  }
  return false
}

// 苗木を植える所（家から 6〜24 m、ほかの木・苗木から TREE_GAP 以上）: ボットに近い順の最初の 1 つ
export function plantSpot (bot, state) {
  if (!state.home) return null
  const me = bot.entity.position
  const y0 = state.home.inside.y
  const spots = []
  for (const [x, z] of ringCells(state)) {
    const s = surfaceAt(bot, x, z, y0)
    if (!s || !SAPLING_GROUND.test(s.ground.name) || farmRefusal(state, s.cell)) continue
    spots.push(s.cell)
  }
  spots.sort((a, b) => a.distanceTo(me) - b.distanceTo(me))
  return spots.find((p) => !nearName(bot, p, /(_log|_sapling|_propagule)$/, TREE_GAP - 1)) ?? null
}

// 耕す所: 今の畑の隣を先に（畑を 1 か所にまとめる）、なければ水の近く、なければ平らな地面
export function tillSpot (bot, state) {
  if (!state.home) return null
  const me = bot.entity.position
  const y0 = state.home.inside.y
  const ok = []
  for (const [x, z] of ringCells(state)) {
    const s = surfaceAt(bot, x, z, y0)
    if (!s || !TILLABLE.test(s.ground.name) || farmRefusal(state, s.ground.position ?? s.cell.offset(0, -1, 0))) continue
    if (nearName(bot, s.cell, /(_log|_sapling|_propagule)$/, 1)) continue
    ok.push(s.cell.offset(0, -1, 0))
  }
  const score = (p) => {
    const nextToFarm = [[1, 0], [-1, 0], [0, 1], [0, -1]].some(([dx, dz]) => bot.blockAt(p.offset(dx, 0, dz))?.name === 'farmland')
    const wet = nearName(bot, p, /^water$/, WATER_REACH)
    return (nextToFarm ? 0 : wet ? 1000 : 2000) + p.distanceTo(me)
  }
  return ok.sort((a, b) => score(a) - score(b))[0] ?? null
}

// 種をまく所: 家のまわりの、上が空いた耕地（ボットに近い順）
export function sowSpot (bot, state) {
  if (!state.home) return null
  const id = bot.registry.blocksByName.farmland?.id
  if (id == null) return null
  const me = bot.entity.position
  return bot.findBlocks({ point: state.home.inside, matching: id, maxDistance: PLANT_RING.max + 8, count: 256 })
    .filter((p) => bot.blockAt(p.offset(0, 1, 0))?.name === 'air')
    .sort((a, b) => a.distanceTo(me) - b.distanceTo(me))[0] ?? null
}

