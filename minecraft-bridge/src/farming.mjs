// 植林と畑（docs/design/33_planting_and_farming.md）: 作物と苗木、植えた場所、耕地の作物、地面。
//
// planted(sapling, n): 自分が植えた場所（state.plantings）に、苗木か育った木（原木）が n 本ある。
// farmed(crop, n): 畑（設計した畑と家の畑）に、その作物が n マス植わっている（facilities.mjs の cropFarms）。
// 新しく耕す・植える場所は、設計した施設のマスだけ（設計書 47 §14。facilities.mjs の workCell）。
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
export const CROP_BLOCK = /^(wheat|carrots|potatoes|beetroots)$/
export const cropByBlock = (name) => Object.keys(CROPS).find((k) => CROPS[k].block === name) ?? null
// 作物なら熟しているか（作物でなければ真）。育ち途中を掘ると種しか出ず、畑が減る
export const isRipe = (block) => {
  const crop = cropByBlock(block?.name)
  return !crop || ageOf(block) >= CROPS[crop].mature
}
const CROP_ALIASES = { wheat: 'wheat', wheat_seeds: 'wheat', carrot: 'carrots', carrots: 'carrots', potato: 'potatoes', potatoes: 'potatoes', beetroot: 'beetroots', beetroots: 'beetroots', beetroot_seeds: 'beetroots' }
export const cropOf = (item) => CROP_ALIASES[String(item)] ?? null
export const cropBySeed = (seed) => Object.keys(CROPS).find((k) => CROPS[k].seed === seed) ?? null
export const SAPLING_GROUND = /^(dirt|grass_block|podzol|coarse_dirt|rooted_dirt|mud|moss_block|mycelium)$/
export const TILLABLE = /^(dirt|grass_block|dirt_path)$/
// 整地（設計書 41、75、195）で畑にできる柔らかい地面: そろえて土で埋めれば耕せる
export const SOFT_GROUND = /^(dirt|grass_block|coarse_dirt|podzol|rooted_dirt|mycelium|mud|clay|sand|red_sand|gravel|snow_block|dirt_path|farmland)$/
// 耕す前に掘ってどける、地面の上の草花（耕すには上が空いている必要がある）
export const CLEARABLE = /^(short_grass|tall_grass|fern|large_fern|dead_bush|dandelion|poppy|blue_orchid|allium|azure_bluet|oxeye_daisy|cornflower|lily_of_the_valley|sunflower|lilac|rose_bush|peony|(red|orange|white|pink)_tulip)$/
export const PLANT_RING = { min: 6, max: 24 } // 家から（水平の距離）
export const TREE_GAP = 4 // 苗木どうし、木との間
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

// 湿っていない耕地（moisture 7 未満: 4 マス以内に水がない。作物の育ちが遅く、踏むと土に戻りやすい）
export const MOIST = 7
export function isDry (block) {
  if (block?.name !== 'farmland') return false
  const m = block.getProperties?.().moisture
  return m != null && Number(m) < MOIST
}

// 地面のセル（x, z）: 家の床の高さの近くで、固いブロックの上に 2 マス空いた所 → { ground, cell }
export function surfaceAt (bot, x, z, y0) {
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
  return farmProtected(state, pos) ?? farmDistance(state, pos)
}

// 家・建物の壁のそば: 木や作物が壁を壊す・ふさぐ（安全。BRIDGE_GUARDS=standard でなくても断る）
export function farmProtected (state, pos) {
  return inHouse(state, pos, 1) ? 'it is on or next to the home or a build' : null
}

// 家から離す距離（作業知識。BRIDGE_GUARDS=standard 以外では警告）
export function farmDistance (state, pos) {
  if (!state.home) return 'there is no home yet'
  const d = xzDist(pos, state.home.inside)
  if (d < PLANT_RING.min - 2) return 'it is too close to the home (keep the door and walls clear)'
  return null
}

export const nearName = (bot, p, re, r) => {
  for (let dx = -r; dx <= r; dx++) {
    for (let dz = -r; dz <= r; dz++) {
      for (let dy = -1; dy <= 2; dy++) {
        if (re.test(bot.blockAt(p.offset(dx, dy, dz))?.name ?? '')) return true
      }
    }
  }
  return false
}
