import test from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { jumpAndPlace } from '../src/home.mjs'

const { Vec3 } = vec3Pkg

// 足元のセルに置き換えられない物（松明）があるとき、足場を置く前に壊す
function bot (cellBlock) {
  const log = []
  const cell = new Vec3(5, 63, 5)
  const b = {
    entity: { position: new Vec3(5.5, 63, 5.5), yaw: 0 },
    blockAt: (p) => p.equals(cell) ? cellBlock : p.equals(cell.offset(0, -1, 0)) ? { name: 'cobblestone', boundingBox: 'block', position: p } : { name: 'stone', boundingBox: 'block', position: p },
    equip: async (it) => log.push(`equip ${it.name}`),
    look: async () => {},
    setControlState: () => {},
    waitForTicks: async () => { b.entity.position = new Vec3(5.5, 64.2, 5.5) },
    dig: async (blk) => log.push(`dig ${blk.name}`),
    placeBlock: async () => log.push('place')
  }
  return { b, log, cell }
}

test('jumpAndPlace: 立っているセルの松明は、足場を置く前に壊す（置き換えられず、積めなかった）', async () => {
  const { b, log, cell } = bot({ name: 'torch', boundingBox: 'empty', position: new Vec3(5, 63, 5) })
  await jumpAndPlace(b, cell, { name: 'cobblestone' })
  assert.deepEqual(log, ['dig torch', 'equip cobblestone', 'place'])
})

test('jumpAndPlace: 空気のセルは壊さない', async () => {
  const { b, log, cell } = bot({ name: 'air', boundingBox: 'empty', position: new Vec3(5, 63, 5) })
  await jumpAndPlace(b, cell, { name: 'cobblestone' })
  assert.deepEqual(log, ['equip cobblestone', 'place'])
})

// 足が上がっていく高さを 1 tick ずつ返すボット（prismarine-physics の跳び: 75.42 75.753 76.001 76.166 76.249 ...）
function risingBot (heights, refusals = 0) {
  const cell = new Vec3(5, 63, 5)
  const placedAt = []
  let i = 0
  let refused = 0
  const b = {
    entity: { position: new Vec3(5.5, 63, 5.5), yaw: 0 },
    blockAt: (p) => p.equals(cell) ? { name: 'air', boundingBox: 'empty', position: p } : { name: 'stone', boundingBox: 'block', position: p },
    equip: async () => {},
    look: async () => {},
    setControlState: () => {},
    waitForTicks: async () => { b.entity.position = new Vec3(5.5, heights[Math.min(i++, heights.length - 1)], 5.5) },
    dig: async () => {},
    placeBlock: async () => {
      placedAt.push(b.entity.position.y)
      if (refused++ < refusals) throw new Error('Server refused to place dirt at (5, 63, 5): the block is still air')
    }
  }
  return { b, cell, placedAt }
}

test('jumpAndPlace: 足が置くマスの上端に届いた直後ではなく、少し上に出てから置く（サーバーの位置は 1 tick 遅れる）', async () => {
  const { b, cell, placedAt } = risingBot([63.42, 63.753, 64.0013, 64.166, 64.249])
  await jumpAndPlace(b, cell, { name: 'dirt' })
  assert.deepEqual(placedAt, [64.166])
})

test('jumpAndPlace: 断られたら、跳びを続けて置き直す', async () => {
  const { b, cell, placedAt } = risingBot([64.166, 63.2, 64.166], 1)
  await jumpAndPlace(b, cell, { name: 'dirt' })
  assert.equal(placedAt.length, 2)
})

test('jumpAndPlace: 断られ続けたら、置き直しは回数で止めてエラーを投げる', async () => {
  const { b, cell, placedAt } = risingBot([64.166], 99)
  await assert.rejects(jumpAndPlace(b, cell, { name: 'dirt' }), /Server refused/)
  assert.equal(placedAt.length, 3)
})

test('jumpAndPlace: 断り以外の失敗は置き直さない', async () => {
  const { b, cell, placedAt } = risingBot([64.166])
  b.placeBlock = async () => { placedAt.push(1); throw new Error('must be holding an item to place') }
  await assert.rejects(jumpAndPlace(b, cell, { name: 'dirt' }), /holding an item/)
  assert.equal(placedAt.length, 1)
})
