// ベッドの隣に立てる所がなければ、何がふさいでいるかを言う（2026-10-11）
import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { bedBlockers } from '../src/bed-access.mjs'

const { Vec3 } = vec3Pkg
const key = (x, y, z) => `${x},${y},${z}`

// 床 y 69（石）、ベッドは (5,70,5) と (6,70,5)。blocks で上書き
function room (blocks = {}) {
  const base = { [key(5, 70, 5)]: 'white_bed', [key(6, 70, 5)]: 'white_bed' }
  const all = { ...base, ...blocks }
  return { blockAt: (p) => {
    const name = all[key(p.x, p.y, p.z)] ?? (p.y < 70 ? 'stone' : 'air')
    return { name, boundingBox: /air/.test(name) ? 'empty' : 'block' }
  } }
}

test('ベッドの隣に立てる所があれば null', () => {
  assert.equal(bedBlockers(room(), new Vec3(5, 70, 5)), null)
})

test('周りが家具と丸石でふさがっていれば、どこが何でふさがっているかを言う', () => {
  const walls = {}
  for (const [x, z] of [[4, 5], [7, 5], [5, 4], [6, 4]]) walls[key(x, 70, z)] = 'cobblestone'
  walls[key(5, 70, 6)] = 'crafting_table'
  walls[key(6, 70, 6)] = 'furnace'
  const why = bedBlockers(room(walls), new Vec3(5, 70, 5))
  assert.match(why, /west of the bed at 4,70,5: cobblestone/)
  assert.match(why, /south of the bed at 5,70,6: crafting_table/)
  assert.match(why, /south of the bed at 6,70,6: furnace/)
  // 足元はあいているが頭がふさがっている隣と、足元がない隣も言う
  const low = room({ [key(4, 70, 5)]: 'air', [key(4, 71, 5)]: 'oak_planks', [key(7, 70, 5)]: 'cobblestone', [key(5, 70, 4)]: 'cobblestone', [key(6, 70, 4)]: 'cobblestone', [key(5, 70, 6)]: 'cobblestone', [key(6, 70, 6)]: 'cobblestone' })
  assert.match(bedBlockers(low, new Vec3(5, 70, 5)), /west of the bed at 4,70,5: oak_planks above/)
})
