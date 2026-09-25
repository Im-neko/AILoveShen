// 真上から見た地図のデータ（docs/design/25_builds.md §3）。家を中心に、列ごとの一番上のブロックの
// 種類と、家の床からの高さ。Python が画像に描き、Gemini はマス目（4x4 ブロック）で置き場所を選ぶ。
// 座標は Gemini に見せない（マス目からワールドの座標にするのはコード）。

import vec3Pkg from 'vec3'
import { buildBox } from './builds.mjs'

const { Vec3 } = vec3Pkg

export const MAP_RADIUS = 32
const SCAN_UP = 12
const SCAN_DOWN = 12

// ブロックの名前 -> 地図の種類
export function mapKind (name) {
  if (/water/.test(name)) return 'water'
  if (/lava/.test(name)) return 'lava'
  if (/_leaves$/.test(name)) return 'leaves'
  if (/_log$|_wood$/.test(name)) return 'tree'
  if (/_planks$|cobblestone|_door$|_bed$|chest|furnace|crafting_table|torch|glass|brick/.test(name)) return 'built'
  if (/sand/.test(name)) return 'sand'
  if (/snow|ice/.test(name)) return 'snow'
  if (/^(stone|granite|diorite|andesite|deepslate|tuff|gravel|cobbled_deepslate)$|_ore$/.test(name)) return 'stone'
  if (/dirt|grass_block|podzol|mud|clay|farmland|path|mycelium/.test(name)) return 'ground'
  return 'other'
}

// cells[z][x] = [種類, 家の床の層からの高さ] か null（読み込まれていない）
export function mapAround (bot, state, radius = MAP_RADIUS) {
  const home = state.home
  const me = bot.entity.position.floored()
  const center = home ? { x: Math.floor((home.min.x + home.max.x) / 2), z: Math.floor((home.min.z + home.max.z) / 2) } : { x: me.x, z: me.z }
  const baseY = home ? home.min.y - 1 : me.y - 1
  const cells = []
  for (let dz = -radius; dz < radius; dz++) {
    const row = []
    for (let dx = -radius; dx < radius; dx++) row.push(column(bot, center.x + dx, center.z + dz, baseY))
    cells.push(row)
  }
  return {
    center,
    radius,
    base_y: baseY,
    cells,
    home: home ? { min: { x: home.min.x - 1, z: home.min.z - 1 }, max: { x: home.max.x + 1, z: home.max.z + 1 }, door: { x: home.door.x, z: home.door.z } } : null,
    builds: Object.entries(state.builds ?? {}).filter(([, p]) => p.origin).map(([name, p]) => {
      const box = buildBox(p)
      return { name, min: { x: box.min.x, z: box.min.z }, max: { x: box.max.x, z: box.max.z } }
    })
  }
}

function column (bot, x, z, baseY) {
  for (let y = baseY + SCAN_UP; y >= baseY - SCAN_DOWN; y--) {
    const b = bot.blockAt(new Vec3(x, y, z))
    if (!b) return null
    if (b.boundingBox === 'block' || /water|lava/.test(b.name)) return [mapKind(b.name), y - baseY]
  }
  return ['other', -SCAN_DOWN]
}
