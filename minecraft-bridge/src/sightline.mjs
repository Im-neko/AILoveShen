// 目から先のブロックへの視線（docs/design/136_sightline.md）。
// サーバーのバケツや右クリックのレイは、ブロックの「選択枠」に当たる。Mineflayer のレイは「当たり判定」にしか
// 当たらないので、小麦・草・苗木・松明のように、通り抜けられても枠のあるブロックを素通りする
// （ボットは水が見えると思い、サーバーは小麦に当たって何も起きない）。ここは枠のある物を足して、同じ線を引く。

import { createRequire } from 'node:module'
import vec3Pkg from 'vec3'

const { Vec3 } = vec3Pkg
const { RaycastIterator } = createRequire(import.meta.url)('prismarine-world').iterators

const EYE_HEIGHT = 1.62
const EPSILON = 1e-6
const NO_OUTLINE = new Set(['air', 'cave_air', 'void_air', 'water', 'lava', 'bubble_column', 'light', 'structure_void'])
const FULL = [[0, 0, 0, 1, 1, 1]]

const box = (x0, y0, z0, x1, y1, z1) => [[x0 / 16, y0 / 16, z0 / 16, x1 / 16, y1 / 16, z1 / 16]]
// Paper 1.21.4 の CropBlock / CarrotBlock / PotatoBlock / BeetrootBlock の SHAPE_BY_AGE（高さ。ピクセル。底は 0）
const CROP_HEIGHTS = {
  wheat: [2, 4, 6, 8, 10, 12, 14, 16],
  carrots: [2, 3, 4, 5, 6, 7, 8, 9],
  potatoes: [2, 3, 4, 5, 6, 7, 8, 9],
  beetroots: [2, 4, 6, 8]
}
const BUSH = box(2, 0, 2, 14, 13, 14) // short_grass, fern, dead_bush
const SAPLING = box(2, 0, 2, 14, 12, 14)
const FLOWER = box(5, 0, 5, 11, 10, 11)
const TORCH = box(6, 0, 6, 10, 10, 10)
const CANE = box(2, 0, 2, 14, 16, 14)
const FLOWERS = new Set(['dandelion', 'poppy', 'blue_orchid', 'allium', 'azure_bluet', 'red_tulip', 'orange_tulip', 'white_tulip', 'pink_tulip', 'oxeye_daisy', 'cornflower', 'lily_of_the_valley', 'wither_rose', 'torchflower'])

// 通り抜けられるのに選択枠のあるブロックの枠（ブロックの中の 0〜1）。表にない名前は null
export function outlineOf (block) {
  const name = block.name
  const heights = CROP_HEIGHTS[name]
  if (heights) {
    const age = Number(block.getProperties?.().age ?? heights.length - 1)
    return box(0, 0, 0, 16, heights[Math.min(Math.max(age, 0), heights.length - 1)], 16)
  }
  if (name === 'short_grass' || name === 'fern' || name === 'dead_bush') return BUSH
  if (name.endsWith('_sapling')) return SAPLING
  if (FLOWERS.has(name)) return FLOWER
  if (name === 'torch') return TORCH
  if (name === 'sugar_cane') return CANE
  return null
}

// レイが当たる形。当たり判定があればそれ、なければ枠の表、表になければ（空気と液体以外は）1 マスまるごと
function shapesOf (block) {
  const solid = block.shapes?.length ? block.shapes : (block.boundingBox === 'block' ? FULL : null)
  if (solid) return solid
  if (NO_OUTLINE.has(block.name)) return null
  return outlineOf(block) ?? FULL
}

// from から to へ引いた線で、最初に当たるブロック。to のあるマスに着くまでに何もなければ null。
// {name, x, y, z, age}。`ignore` のマス（狙うマス）は数えない
export function firstBlockBetween (bot, from, to, ignore = null) {
  const dir = to.minus(from)
  const dist = dir.norm()
  if (dist < EPSILON) return null
  const iter = new RaycastIterator(from, dir.scaled(1 / dist), dist)
  const aim = to.floored()
  for (let cell = iter.next(); cell; cell = iter.next()) {
    const at = new Vec3(cell.x, cell.y, cell.z)
    if (at.equals(aim) || (ignore && at.equals(ignore))) return null
    const block = bot.blockAt(at)
    if (!block) continue
    const shapes = shapesOf(block)
    if (!shapes) continue
    const hit = iter.intersect(shapes, at)
    if (!hit || hit.pos.distanceTo(from) > dist + EPSILON) continue
    const age = block.getProperties?.().age
    return { name: block.name, x: at.x, y: at.y, z: at.z, ...(age !== undefined ? { age: Number(age) } : {}) }
  }
  return null
}

// ボットの目から正面へ maxDistance の間で、サーバーのレイが最初に当たるブロック（水や空気は通り抜ける）
export function lookingAt (bot, maxDistance = 5) {
  const { yaw, pitch } = bot.entity
  const dir = new Vec3(-Math.sin(yaw) * Math.cos(pitch), Math.sin(pitch), -Math.cos(yaw) * Math.cos(pitch))
  const eye = eyeOf(bot)
  const iter = new RaycastIterator(eye, dir, maxDistance)
  for (let cell = iter.next(); cell; cell = iter.next()) {
    const at = new Vec3(cell.x, cell.y, cell.z)
    const block = bot.blockAt(at)
    const shapes = block && shapesOf(block)
    if (!shapes || !iter.intersect(shapes, at)) continue
    return { name: block.name, x: at.x, y: at.y, z: at.z }
  }
  return null
}

export const eyeOf = (bot) => bot.entity.position.offset(0, bot.entity.eyeHeight ?? EYE_HEIGHT, 0)

export const describeBlock = (hit) => `${hit.name}${hit.age !== undefined ? ` (age ${hit.age})` : ''} at ${hit.x},${hit.y},${hit.z}`

// pos のマスの横に立てるマス（その上 2 つが通れる）。ボットに近い順。足の位置のマスで返す
export function cellsBeside (bot, pos) {
  const me = bot.entity.position
  return [[1, 0], [-1, 0], [0, 1], [0, -1]]
    .map(([dx, dz]) => pos.offset(dx, 0, dz))
    .filter((p) => bot.blockAt(p.offset(0, 1, 0))?.boundingBox === 'empty' && bot.blockAt(p.offset(0, 2, 0))?.boundingBox === 'empty')
    .sort((a, b) => a.offset(0.5, 1, 0.5).distanceTo(me) - b.offset(0.5, 1, 0.5).distanceTo(me))
}

// pos のマスの横に、足を置いて立てるマス（足元が固く、足と頭が通れる）。ボットに近い順。畑の水のマスは地面の高さの穴なので、
// 横の地面の上（1 つ上）に立つ。作物の畑のように水のマスが作物の高さなら、同じ高さに立つ
export function standSpotsBeside (bot, pos) {
  const me = bot.entity.position
  const open = (p) => bot.blockAt(p)?.boundingBox === 'empty'
  const spots = []
  for (const [dx, dz] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
    for (const dy of [1, 0]) {
      const feet = pos.offset(dx, dy, dz)
      if (bot.blockAt(feet.offset(0, -1, 0))?.boundingBox === 'block' && open(feet) && open(feet.offset(0, 1, 0))) {
        spots.push(feet)
        break
      }
    }
  }
  return spots.sort((a, b) => a.offset(0.5, 0, 0.5).distanceTo(me) - b.offset(0.5, 0, 0.5).distanceTo(me))
}

// pos のマスの中心を狙ったとき、間にあるブロックの文。何もなければ空。
// 横に立てば線が通る所があれば、そこへ歩いてもう一度使うように言う（`aim` は狙う点。省けばマスの中心）
export function blockedNote (bot, pos, aim = pos.offset(0.5, 0.5, 0.5)) {
  const hit = firstBlockBetween(bot, eyeOf(bot), aim, pos)
  if (!hit) return ''
  const eyeAt = (p) => p.offset(0.5, EYE_HEIGHT, 0.5)
  const clear = standSpotsBeside(bot, pos).find((p) => !firstBlockBetween(bot, eyeAt(p), aim, pos))
  const stand = clear ? `; goto ${clear.x},${clear.y},${clear.z} (a cell beside it where the line is clear), then use the bucket at ${pos.x},${pos.y},${pos.z} again` : ''
  return ` (the ${describeBlock(hit)} is in the line between your eyes and ${pos.x},${pos.y},${pos.z}: the server aims at its outline, so you hit it instead${stand})`
}
