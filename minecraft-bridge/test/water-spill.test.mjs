import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { straySources, scoopBack, isSource } from '../src/water-spill.mjs'

const { Vec3 } = vec3Pkg
const v = (x, y, z) => new Vec3(x, y, z)
const key = (p) => `${p.x},${p.y},${p.z}`
const HOLE = v(24, 69, -207)

// 水のブロック: level 0 が水源。blocks: キー → { name, level }
function fakeBot ({ at = v(21.5, 70, -207.5), blocks = [], bucket = true } = {}) {
  const map = new Map(blocks.map(([p, level]) => [key(p), level]))
  const inv = bucket ? [{ name: 'bucket', count: 1 }] : []
  let look = null
  const bot = {
    entity: { position: at.clone(), eyeHeight: 1.62 },
    blockAt: (p) => map.has(key(p))
      ? { name: 'water', position: p, getProperties: () => ({ level: String(map.get(key(p))) }) }
      : { name: 'air', position: p, getProperties: () => ({}) },
    inventory: { items: () => inv },
    equip: async () => {},
    lookAt: async (p) => { look = p },
    activateItem: () => {
      const target = v(Math.floor(look.x), Math.floor(look.y), Math.floor(look.z))
      if (inv[0]?.name === 'bucket' && map.get(key(target)) === 0) {
        map.delete(key(target))
        inv[0] = { name: 'water_bucket', count: 1 }
      }
    },
    waitForTicks: async () => {}
  }
  return { bot, map, inv }
}

test('穴の中の水源は数えず、穴の外の水源だけを近い順に返す', () => {
  const { bot } = fakeBot({ blocks: [[HOLE, 0], [v(23, 71, -207), 0], [v(26, 70, -207), 0], [v(24, 70, -208), 3]] })
  const found = straySources(bot, HOLE)
  assert.deepEqual(found.map(key), ['23,71,-207', '26,70,-207'])
})

test('流れている水（level 1 以上）は水源ではない', () => {
  const { bot } = fakeBot({ blocks: [[HOLE, 0], [v(24, 70, -207), 8], [v(23, 70, -207), 1]] })
  assert.deepEqual(straySources(bot, HOLE), [])
  assert.equal(isSource(bot.blockAt(HOLE)), true)
  assert.equal(isSource(bot.blockAt(v(24, 70, -207))), false)
})

test('探す範囲の外（横に 4 以上、穴より下、上に 4 以上）は見ない', () => {
  const { bot } = fakeBot({ blocks: [[v(28, 69, -207), 0], [v(24, 68, -207), 0], [v(24, 73, -207), 0], [v(24, 72, -207), 0]] })
  assert.deepEqual(straySources(bot, HOLE).map(key), ['24,72,-207'])
})

test('空のバケツで穴の外の水源を汲み戻す。バケツは満たされ、水源は消える', async () => {
  const { bot, map, inv } = fakeBot({ blocks: [[HOLE, 0], [v(23, 71, -207), 0]] })
  const r = await scoopBack(bot, HOLE, async () => {})
  assert.deepEqual(r, { scooped: 1, left: 0 })
  assert.equal(map.has('23,71,-207'), false)
  assert.equal(map.has(key(HOLE)), true)
  assert.equal(inv[0].name, 'water_bucket')
})

test('バケツは 1 回汲むと満たされるので、汲めるのは 1 つ。残りは数えて返す', async () => {
  const { bot } = fakeBot({ blocks: [[v(23, 71, -207), 0], [v(25, 71, -207), 0]] })
  const r = await scoopBack(bot, HOLE, async () => {})
  assert.deepEqual(r, { scooped: 1, left: 1 })
})

test('空のバケツがなければ汲まない', async () => {
  const { bot, map } = fakeBot({ bucket: false, blocks: [[v(23, 71, -207), 0]] })
  const r = await scoopBack(bot, HOLE, async () => {})
  assert.deepEqual(r, { scooped: 0, left: 1 })
  assert.equal(map.has('23,71,-207'), true)
})

test('届かない水源へは近づいてから汲む', async () => {
  const { bot } = fakeBot({ at: v(10.5, 70, -207.5), blocks: [[v(23, 71, -207), 0]] })
  let walked = null
  const r = await scoopBack(bot, HOLE, async (p) => { walked = key(p); bot.entity.position = v(21.5, 70, -207.5) })
  assert.equal(walked, '23,71,-207')
  assert.equal(r.scooped, 1)
})

test('届く水源へは歩かない', async () => {
  const { bot } = fakeBot({ blocks: [[v(23, 71, -207), 0]] })
  let walked = false
  await scoopBack(bot, HOLE, async () => { walked = true })
  assert.equal(walked, false)
})
