import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { landmarks, findBase, adoptFoundBase } from '../src/landmarks.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')

// 石の地面（y 69 以下）の上に 5x5 の家: 壁は x 0..4、z 0..4（高さ 70..71）、屋根は y 72、室内は 1..3。
// ドアは南の壁 (2, 70, 4)、ベッドは (1, 70, 1)〜(1, 70, 2)、作業台は (3, 70, 1)
function world ({ roof = true } = {}) {
  const blocks = new Map()
  const set = (x, y, z, name, props = {}) => blocks.set(`${x},${y},${z}`, { name, props })
  for (let x = 0; x <= 4; x++) {
    for (let z = 0; z <= 4; z++) {
      const wall = x === 0 || x === 4 || z === 0 || z === 4
      if (wall) { set(x, 70, z, 'oak_planks'); set(x, 71, z, 'oak_planks') }
      if (roof) set(x, 72, z, 'oak_planks')
    }
  }
  set(2, 70, 4, 'oak_door', { half: 'lower' })
  set(2, 71, 4, 'oak_door', { half: 'upper' })
  set(1, 70, 1, 'red_bed', { part: 'head' })
  set(1, 70, 2, 'red_bed', { part: 'foot' })
  set(3, 70, 1, 'crafting_table')
  const blockAt = (p) => {
    const b = blocks.get(`${p.x},${p.y},${p.z}`)
    const name = b?.name ?? (p.y < 70 ? 'stone' : 'air')
    const solid = name !== 'air' && !name.endsWith('_door') && !name.endsWith('_bed')
    return { name, position: p, boundingBox: solid || name.endsWith('_door') ? 'block' : 'empty', getProperties: () => b?.props ?? {} }
  }
  const byId = new Map(Object.values(md.blocksByName).map((b) => [b.id, b.name]))
  return {
    registry: md,
    entity: { position: new Vec3(2.5, 70, 8.5) },
    blockAt,
    findBlocks ({ matching, maxDistance }) {
      const names = new Set(matching.map((id) => byId.get(id)))
      return [...blocks.entries()].filter(([, b]) => names.has(b.name)).map(([k]) => new Vec3(...k.split(',').map(Number)))
        .filter((p) => p.distanceTo(this.entity.position) <= maxDistance)
    }
  }
}

test('まわりのベッド・ドア・作業台を、数と一番近いもの（距離、方角、誰のものか）で出す', () => {
  const bot = world()
  const found = Object.fromEntries(landmarks(bot, { home: null }).map((l) => [l.kind, l]))
  assert.equal(found.bed.count, 1) // 頭と足で 1 つ
  assert.equal(found.door.count, 1) // 上と下で 1 つ
  assert.deepEqual([found.bed.nearest.x, found.bed.nearest.z], [1, 2])
  assert.equal(found.bed.nearest.direction, 'N')
  assert.equal(found.bed.owner, null) // ボットが建てたものではない
  assert.ok(found.crafting_table)
  assert.equal(found.chest, undefined)
})

test('ドアの内側が屋根つきの閉じた部屋なら、拠点として見つける', () => {
  const base = findBase(world())
  assert.deepEqual(base.door, new Vec3(2, 70, 4))
  assert.deepEqual(base.inside, new Vec3(2, 70, 3))
  assert.deepEqual(base.outside, new Vec3(2, 70, 5))
  assert.equal(base.cells.length, 9) // 室内の 3x3 全部（ベッドと作業台のマスも部屋に数える。growHome と同じ）
  assert.deepEqual([base.min.x, base.min.z, base.max.x, base.max.z], [1, 1, 3, 3])
})

test('屋根がなければ拠点ではない', () => {
  assert.equal(findBase(world({ roof: false })), null)
})

test('家がないときだけ、見つけた拠点を家にする', () => {
  const bot = world()
  const state = { home: null }
  assert.equal(adoptFoundBase(bot, state), true)
  assert.equal(state.home.name, '見つけた拠点')
  const own = { door: new Vec3(50, 70, 50) }
  const other = { home: own }
  assert.equal(adoptFoundBase(bot, other), false)
  assert.equal(other.home, own)
  // 家にした後は、目印は家のもの
  const bed = landmarks(bot, state).find((l) => l.kind === 'bed')
  assert.equal(bed.owner, 'home')
})
