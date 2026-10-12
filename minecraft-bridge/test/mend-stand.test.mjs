// 坑道のセルの床が抜けている（鉱石を掘った穴、松明を置いた穴）と、丸石で埋めてから立つ（2026-10-12）
import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { floorSolid, mendStand, placeSeal } from '../src/sealing.mjs'

const { Vec3 } = vec3Pkg
const key = (p) => `${p.x},${p.y},${p.z}`

// y 32 の坑道（z -270 から -273 へ向かって掘った）。床は y 31。blocks で上書きする。置いた・壊したブロックを記録する
function setup (blocks = {}) {
  const world = { ...blocks }
  const log = []
  const bot = {
    blockAt: (p) => {
      const name = world[key(p)] ?? (p.y >= 32 && p.y <= 33 && p.x === 124 && p.z <= -270 && p.z >= -276 ? 'air' : 'stone')
      return { name, position: p, boundingBox: /air|water|lava|torch/.test(name) ? 'empty' : 'block' }
    },
    inventory: { items: () => [{ name: 'cobblestone', count: 64 }] },
    equip: async () => {},
    dig: async (b) => { log.push(`dig ${key(b.position)} ${b.name}`); world[key(b.position)] = 'air' },
    placeBlock: async (ref, face) => {
      const p = new Vec3(ref.position.x + face.x, ref.position.y + face.y, ref.position.z + face.z)
      log.push(`place ${key(p)}`)
      world[key(p)] = 'cobblestone'
    }
  }
  const cells = [-276, -275, -274, -273, -272, -271].map((z) => ({ x: 124, y: 32, z, h: 2, part: 'branch' }))
  const free = (a) => bot.blockAt(new Vec3(a.x, a.y, a.z)).boundingBox === 'empty'
  return { bot, log, cells, free }
}
const signal = new AbortController().signal

test('床が固いセルは、そのまま立てる', async () => {
  const { bot, cells, free } = setup()
  assert.equal(floorSolid(bot, cells[5]), true)
  const walked = []
  assert.equal(await mendStand(bot, cells[5], cells.slice(0, 5).reverse(), free, async (a) => walked.push(a.z), signal), true)
  assert.deepEqual(walked, [])
})

test('床に穴（松明を置いた穴）があるセルは、床が固い前のセルまで戻って、松明を壊して丸石で埋める', async () => {
  const { bot, log, cells, free } = setup({ '124,31,-271': 'torch', '124,31,-272': 'stone' })
  assert.equal(floorSolid(bot, cells[5]), false)
  const walked = []
  assert.equal(await mendStand(bot, cells[5], cells.slice(0, 5).reverse(), free, async (a) => walked.push(a.z), signal), true)
  assert.deepEqual(log, ['dig 124,31,-271 torch', 'place 124,31,-271'])
  assert.deepEqual(walked, [-272])
  assert.equal(floorSolid(bot, cells[5]), true)
})

test('床の穴が続くときは、固い床までさかのぼって、近い順に埋める', async () => {
  const { bot, log, cells, free } = setup({ '124,31,-271': 'air', '124,31,-272': 'air' })
  const walked = []
  assert.equal(await mendStand(bot, cells[5], cells.slice(0, 5).reverse(), free, async (a) => walked.push(a.z), signal), true)
  assert.deepEqual(log, ['place 124,31,-272', 'place 124,31,-271'])
  assert.deepEqual(walked, [-273, -272])
})

test('さかのぼっても固い床がなければ、埋めずに false', async () => {
  const blocks = {}
  for (let z = -276; z <= -271; z++) blocks[`124,31,${z}`] = 'air'
  const { bot, log, cells, free } = setup(blocks)
  assert.equal(await mendStand(bot, cells[5], cells.slice(0, 5).reverse(), free, async () => {}, signal), false)
  assert.deepEqual(log, [])
})

test('丸石がなければ置けない（床の松明も壊さない）', async () => {
  const { bot, log } = setup({ '124,31,-271': 'torch' })
  bot.inventory.items = () => []
  assert.equal(await placeSeal(bot, new Vec3(124, 31, -271), signal), false)
  assert.deepEqual(log, [])
})
