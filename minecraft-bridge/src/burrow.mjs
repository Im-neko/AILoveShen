// 家も寝床もない夜の避難: 足元に縦穴を掘って入り、上を塞ぐ（docs/design/61_burrow_shelter.md）。
// ここは場所の選び方と「穴の中にいるか」の判定だけ（掘る・置くは primitives.mjs の dig_in / leave_burrow）。
import vec3Pkg from 'vec3'
import { TERRAIN } from './leveling.mjs'
import { inHouse } from './home.mjs'
import { liquidReason } from './liquid.mjs'
import { facilityAt } from './facilities.mjs'

const { Vec3 } = vec3Pkg

// 穴の深さ: 足の下 3 マスを掘る。いちばん上（地面と同じ高さ）を塞いで、頭の上を土にする
export const BURROW_DEPTH = 3
export const BURROW_RADIUS = 6 // 立っている所から、この範囲の平らな地面を探す

const HORIZONTAL = [[1, 0], [-1, 0], [0, 1], [0, -1]]
const FALLING = /(^|_)(sand|gravel)$|concrete_powder$/
const FLUID = /water|lava/

const solid = (b) => !!b && b.boundingBox === 'block'
const passable = (b) => !!b && b.boundingBox === 'empty' && !FLUID.test(b.name)

// 掘れるか: 道具が要る地面（石など）は、その道具を持っているときだけ
function diggable (bot, block) {
  if (!TERRAIN.test(block.name) || FALLING.test(block.name)) return false
  const tools = block.harvestTools
  if (!tools) return true
  return bot.inventory.items().some((i) => tools[i.type])
}

// x,z の列に穴を掘れるか。掘れなければ理由。掘れるなら { feet, cells, seal, floor }
// feet = 今立つ足のセル（地面の上）、cells = 掘る 3 マス（上から）、seal = 塞ぐセル（cells の先頭）
export function burrowAt (bot, state, x, feetY, z) {
  const feet = new Vec3(x, feetY, z)
  const fac = facilityAt(state, feet, 1)
  if (fac) return `part of the facility ${fac} (the farm or grove must not be dug)`
  if (!passable(bot.blockAt(feet)) || !passable(bot.blockAt(feet.offset(0, 1, 0)))) return 'no free space to stand there'
  // 四方が固い所は地面ではなく穴（掘りかけの穴の上に、さらに掘ると深くなって出られない）
  if (HORIZONTAL.every(([dx, dz]) => solid(bot.blockAt(feet.offset(dx, 0, dz))))) return 'that is a pit, not level ground'
  const cells = Array.from({ length: BURROW_DEPTH }, (_, i) => feet.offset(0, -1 - i, 0))
  for (const p of cells) {
    const b = bot.blockAt(p)
    if (!b) return 'the ground there is not loaded'
    if (inHouse(state, p, 1)) return 'part of the house or a build'
    if (!diggable(bot, b)) return `${b.name} cannot be dug here`
    const wet = liquidReason(bot, p, b)
    if (wet) return wet
    for (const [dx, dz] of HORIZONTAL) {
      const n = bot.blockAt(p.offset(dx, 0, dz))
      if (!n) return 'the ground next to it is not loaded'
      if (FLUID.test(n.name)) return `${n.name} next to the hole`
      // 横が空いていると、モブが入ってくる（斜面、洞窟、崖）
      if (!solid(n)) return 'the ground next to it is not level (a slope, a cave or a cliff)'
    }
  }
  const floor = bot.blockAt(feet.offset(0, -1 - BURROW_DEPTH, 0))
  if (!solid(floor) || FLUID.test(floor.name)) return 'no solid floor under the hole'
  return { feet, cells, seal: cells[0], floor: floor.position }
}

// 近い順に、穴を掘れる列を探す。見つからなければ、断られた理由（多い順）を返す
export function burrowSpot (bot, state, radius = BURROW_RADIUS) {
  if (inOpenShaft(bot)) return { spot: openShaftSpot(bot) } // 掘りかけの穴があれば、それを塞ぐ
  const me = bot.entity.position.floored()
  const reasons = new Map()
  const columns = []
  for (let dx = -radius; dx <= radius; dx++) {
    for (let dz = -radius; dz <= radius; dz++) columns.push([dx, dz, dx * dx + dz * dz])
  }
  columns.sort((a, b) => a[2] - b[2])
  for (const [dx, dz] of columns) {
    // 地面の高さ: 立っている高さの前後 2 マスで、足を置ける最初のセル
    for (const dy of [0, 1, -1, 2, -2]) {
      const r = burrowAt(bot, state, me.x + dx, me.y + dy, me.z + dz)
      if (typeof r !== 'string') return { spot: r }
      if (dy === 0) reasons.set(r, (reasons.get(r) ?? 0) + 1)
    }
  }
  const why = [...reasons].sort((a, b) => b[1] - a[1]).slice(0, 2).map(([r]) => r)
  return { spot: null, why: why.join('; ') || 'no ground nearby' }
}

// 縦穴の中か: 足と頭のまわりが全部ブロックで、下も固い。上が塞がっていれば塞いだ穴（inBurrow）、開いていれば
// 掘りかけの穴（inOpenShaft: 塞ぐのに失敗した、など。掘り直さずに塞ぐ）。状態は覚えない
// （ブリッジを再起動しても、穴の中にいれば分かる）
function shaftCell (bot) {
  const feet = bot.entity.position.floored()
  const head = feet.offset(0, 1, 0)
  if (!passable(bot.blockAt(feet)) || !passable(bot.blockAt(head))) return null
  if (!solid(bot.blockAt(feet.offset(0, -1, 0)))) return null
  if (![feet, head].every((p) => HORIZONTAL.every(([dx, dz]) => solid(bot.blockAt(p.offset(dx, 0, dz)))))) return null
  return { feet, roof: bot.blockAt(head.offset(0, 1, 0)) }
}

export function inBurrow (bot) {
  const cell = shaftCell(bot)
  return !!cell && solid(cell.roof)
}

export function inOpenShaft (bot) {
  const cell = shaftCell(bot)
  return !!cell && !!cell.roof && !solid(cell.roof)
}

// 掘りかけの穴の中で塞ぐだけのとき（掘り直すと、深くなって出られなくなる）
export function openShaftSpot (bot) {
  const feet = bot.entity.position.floored()
  return { feet, cells: [], seal: feet.offset(0, 2, 0), floor: feet.offset(0, -1, 0), open: true }
}

// 塞ぐブロックを置くときの、寄りかかる隣のブロックと向き（横の壁の、穴の側の面）
export function sealSupport (bot, seal) {
  for (const [dx, dz] of HORIZONTAL) {
    const ref = bot.blockAt(seal.offset(dx, 0, dz))
    if (solid(ref)) return { ref, face: new Vec3(-dx, 0, -dz) }
  }
  return null
}

// 横に歩いて出られるか: 隣のセルが（頭の高さまで）空いていて、その下が地面
export function canStepOut (bot, cell) {
  return HORIZONTAL.some(([dx, dz]) => {
    const n = cell.offset(dx, 0, dz)
    return passable(bot.blockAt(n)) && passable(bot.blockAt(n.offset(0, 1, 0))) && solid(bot.blockAt(n.offset(0, -1, 0)))
  })
}

// 夜の間は候補を作るたびに探さない（探すのは 1 回に数百列）: 同じ足元なら数秒は前の答えを使う
let cache = null
export function burrowSpotCached (bot, state, now = Date.now()) {
  const key = bot.entity.position.floored().toString()
  if (cache && cache.key === key && now - cache.at < 5000) return cache.result
  cache = { key, at: now, result: burrowSpot(bot, state) }
  return cache.result
}
