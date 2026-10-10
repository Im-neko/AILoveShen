// 坑道を塞ぐ丸石が足りないとき、掘ってきた坑道の横の石を丸石にする: 何を掘ってよいか
import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { quarryTargets } from '../src/seal-stone.mjs'

const { Vec3 } = vec3Pkg
const key = (p) => `${p.x},${p.y},${p.z}`

// 石の中に、x 方向へ一直線の坑道（y 10〜11）。blocks で上書き
function setup (blocks = {}) {
  const bot = {
    blockAt: (p) => {
      const name = blocks[key(p)] ?? (p.y >= 10 && p.y <= 11 && p.z === 0 && p.x >= 0 && p.x <= 5 ? 'air' : 'stone')
      return { name, boundingBox: /air|water|lava/.test(name) ? 'empty' : 'block' }
    }
  }
  const cells = [0, 1, 2, 3].map((x) => ({ x, y: 10, z: 0, h: 2, part: 'tunnel_east' }))
  const state = { facilities: { mine_1: { kind: 'mine', layout: { cells }, box: { min: { x: 0, z: 0 }, max: { x: 5, z: 0 } } } } }
  return { bot, state, cells }
}

test('坑道の横（南北）の石を、足元と 1 つ上の高さから、近い列の順に取る', () => {
  const { bot, state, cells } = setup()
  const got = quarryTargets(bot, state, [cells[3], cells[2]], 3).map(key)
  assert.deepEqual(got, ['3,10,1', '3,10,-1', '3,11,1'])
})

test('洞窟や水・溶岩・落ちる砂利に面した石、塞ぐ面（avoid）は掘らない', () => {
  const { bot, state, cells } = setup({ '3,10,2': 'air', '3,11,-1': 'stone', '4,11,1': 'gravel', '2,10,1': 'stone' })
  // 3,10,1 は奥(3,10,2)が空洞、3,11,1 は東隣(4,11,1)が砂利
  const got = quarryTargets(bot, state, [cells[3]], 10, new Set(['3,10,-1'])).map(key)
  assert.ok(!got.includes('3,10,1') && !got.includes('3,11,1') && !got.includes('3,10,-1'), got.join(' '))
  assert.deepEqual(got, ['3,11,-1'])
  const wet = setup({ '3,10,1': 'stone', '3,9,1': 'water' })
  assert.ok(!quarryTargets(wet.bot, wet.state, [wet.cells[3]], 10).map(key).includes('3,10,1'))
})

test('石でないもの（花崗岩・鉱石・土）は取らない。0 個ならなにも取らない', () => {
  const { bot, state, cells } = setup({ '3,10,1': 'granite', '3,10,-1': 'iron_ore', '3,11,1': 'dirt', '3,11,-1': 'andesite' })
  assert.deepEqual(quarryTargets(bot, state, [cells[3]], 5), [])
  assert.deepEqual(quarryTargets(bot, state, [cells[3]], 0), [])
})
