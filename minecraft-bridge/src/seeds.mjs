// 種の余り（docs/design/117）: 畑の運用で種が余る（小麦の種が家のチェストに 300）。残すのは、その作物の設計した畑の数 ×
// 1 スタック（持ち物とチェストの合計）。超えた分は捨てる。持ち物の種は、残す数まではまく分なので捨てない（2026-10-08, user: 「種は畑の数*1スタック残してあとは捨てて良いです」）。
// 畑がまだなくても 1 畑分は残す（最初の畑に要る）。種そのもの（_seeds）だけ。ニンジンとジャガイモは作物を兼ねるので数えない。
import { CROPS } from './farming.mjs'
import vec3Pkg from 'vec3'
import { designedFarms, inTown } from './facilities.mjs'
import { storedCounts } from './memory.mjs'
import { inventoryCounts } from './observe.mjs'

const { Vec3 } = vec3Pkg

const SEED_CROPS = Object.entries(CROPS).filter(([, c]) => c.seed.endsWith('_seeds')).map(([crop, c]) => ({ crop, seed: c.seed }))

export const isSurplusSeed = (item) => SEED_CROPS.some((s) => s.seed === item)

// 残す数: 畑の数 × 1 スタック（最低 1 畑分）
export function seedLimit (bot, state, item) {
  const crop = SEED_CROPS.find((s) => s.seed === item)?.crop
  const stack = bot.registry?.itemsByName?.[item]?.stackSize ?? 64
  return Math.max(1, designedFarms(state, crop).length) * stack
}

// 種ごとの今: [{ item, held, stored, limit, surplus }]（余りのあるものだけ）。keep は目標が使う物の名前（目標が種そのものを
// 集めているときは、捨てない）
export function seedSurplus (bot, state, keep = new Set()) {
  const held = inventoryCounts(bot)
  const stored = state.memory && state.home ? storedCounts(state.memory, state.home) : {}
  return SEED_CROPS.map(({ seed }) => {
    const limit = seedLimit(bot, state, seed)
    const h = held[seed] ?? 0
    const s = stored[seed] ?? 0
    return { item: seed, held: h, stored: s, limit, surplus: keep.has(seed) ? 0 : Math.max(0, h + s - limit) }
  }).filter((x) => x.surplus > 0)
}

// 持ち物から捨てる分: [{ item, count }]。持ち物は、残す数までは植える分なので捨てない（捨てると、まく種がなくなる）。
// 超えた分だけ捨てる
export function seedsToToss (bot, state, keep) {
  return seedSurplus(bot, state, keep).map((x) => ({ item: x.item, count: Math.max(0, x.held - x.limit) })).filter((x) => x.count > 0)
}

// チェストから出す分: [{ item, count }]。余りがあれば、チェストの種を全部出す（出せば持ち物が残す数を超えた分になり、
// 次に捨てる。残す 1 スタックは持ち物に残る: 種をまくのは持ち物から）
export function seedsToTake (bot, state, keep) {
  return seedSurplus(bot, state, keep).map((x) => ({ item: x.item, count: x.stored })).filter((x) => x.count > 0)
}

// 捨てる場所: 街（家と施設とそのまわり）から離れた所。街の中や作業場で捨てると、床に落ちた種を歩いて拾い直す
// （2026-10-08: 家の中で捨てた 64 個を拾い直し、同じ種を 25 回捨てた）
export const TOSS_AWAY = 28
const TOSS_DIRECTIONS = 16
const STAND_UP = 4
const STAND_DOWN = 8

const passable = (b) => !!b && b.boundingBox === 'empty'
const solidGround = (b) => !!b && b.boundingBox === 'block' && !/^(water|lava)$/.test(b.name)

function standAt (bot, x, z, y0) {
  for (let y = y0 + STAND_UP; y >= y0 - STAND_DOWN; y--) {
    const floor = bot.blockAt(new Vec3(x, y - 1, z))
    if (!floor) return null
    if (solidGround(floor) && passable(bot.blockAt(new Vec3(x, y, z))) && passable(bot.blockAt(new Vec3(x, y + 1, z)))) return new Vec3(x, y, z)
  }
  return null
}

// ボットに近い、街の外の立てる所と、そこで向く向き（街から離れる向き）。なければ null
export function tossSpot (bot, state) {
  const me = bot.entity.position
  const base = state.home?.inside ?? me
  const spots = []
  for (let i = 0; i < TOSS_DIRECTIONS; i++) {
    const a = (2 * Math.PI * i) / TOSS_DIRECTIONS
    const dx = Math.sin(a)
    const dz = Math.cos(a)
    const x = Math.floor(base.x + dx * TOSS_AWAY)
    const z = Math.floor(base.z + dz * TOSS_AWAY)
    const pos = standAt(bot, x, z, Math.floor(base.y))
    if (!pos || inTown(state, pos)) continue
    spots.push({ pos, yaw: Math.atan2(-dx, -dz) })
  }
  return spots.sort((a, b) => a.pos.distanceTo(me) - b.pos.distanceTo(me))[0] ?? null
}

// 余った種を捨てる手: 街の外の捨てる場所まで歩いて、街から離れる向きに捨てる。場所がなければ null
export function seedToss (bot, state, items) {
  const spot = tossSpot(bot, state)
  if (!spot) return null
  return { verb: 'toss', target: items.length === 1 ? items[0].item : 'items', items, pos: spot.pos, yaw: spot.yaw, distance: Math.round(spot.pos.distanceTo(bot.entity.position)) }
}
