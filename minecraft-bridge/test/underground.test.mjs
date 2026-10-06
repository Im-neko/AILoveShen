// 地下の判定: y が低い洞窟に加えて、地表が高い所の洞窟（頭の上の岩が厚い）も地下（2026-10-04）
import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { isUnderground } from '../src/observe.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')

// 頭の上（床 + 2 から）に stone が roof ブロック。横は岩（空が開いた柱はない）か、open なら空
function bot (y, roof, open = false) {
  const head = Math.floor(y) + 2
  return {
    registry: md,
    entity: { position: new Vec3(0.5, y, 0.5) },
    blockAt: (p) => {
      const sky = open && (Math.abs(p.x) + Math.abs(p.z) > 0)
      const solid = !sky && p.y >= head && p.y < head + roof
      return { name: solid ? 'stone' : 'air', boundingBox: solid ? 'block' : 'empty', position: p }
    }
  }
}

test('地表の高い所の洞窟: y が 60 以上でも、頭の上の岩が 8 以上なら地下', () => {
  assert.equal(isUnderground(bot(63, 13)), true)
  assert.equal(isUnderground(bot(63, 8)), true)
})

test('薄い屋根（家、崖の庇）は地下にしない。y が低ければ従来どおり地下', () => {
  assert.equal(isUnderground(bot(70, 3)), false)
  assert.equal(isUnderground(bot(63, 7)), false)
  assert.equal(isUnderground(bot(40, 3)), true)
})

test('空が開けた柱が近くにあれば、厚い屋根でも地下にしない（崖の下）', () => {
  assert.equal(isUnderground(bot(63, 13, true)), false)
})
