import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import prismarineBlock from 'prismarine-block'
import { Knowledge } from '../src/knowledge.mjs'
import { createTools } from '../src/tools.mjs'
import { blockInfo } from '../src/observe.mjs'
import { isDry, ageOf } from '../src/farming.mjs'
import { cropFarms, farmSummary } from '../src/facilities.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')
const Block = prismarineBlock('1.21.4')

// 本物のブロック（状態つき）。耕地は moisture、小麦は age をサーバーと同じ状態 id から作る
const real = (name, props, p) => {
  const b = Block.fromProperties(name, props, 0)
  b.position = p
  return b
}

// 家は (0,64,0)。耕地 y 63、小麦 y 64: (10,*,0) は湿った耕地、(11,*,0) と (12,*,0) は乾いた耕地
function world () {
  const cells = {
    '10,63,0': ['farmland', { moisture: 7 }],
    '11,63,0': ['farmland', { moisture: 3 }],
    '12,63,0': ['farmland', { moisture: 0 }],
    '10,64,0': ['wheat', { age: 7 }],
    '11,64,0': ['wheat', { age: 2 }],
    '12,64,0': ['wheat', { age: 0 }]
  }
  const blockAt = (p) => {
    const c = cells[`${p.x},${p.y},${p.z}`]
    if (c) return real(c[0], c[1], p)
    return real(p.y < 63 ? 'dirt' : 'air', {}, p)
  }
  const wheat = [new Vec3(10, 64, 0), new Vec3(11, 64, 0), new Vec3(12, 64, 0)]
  return {
    registry: md,
    entity: { position: new Vec3(10, 64, 2) },
    entities: {},
    blockAt,
    findBlocks: ({ matching }) => matching === md.blocksByName.wheat.id ? wheat : [],
    inventory: { items: () => [] }
  }
}
const home = { inside: new Vec3(0, 64, 0), min: new Vec3(-1, 64, -1), max: new Vec3(1, 64, 1), door: new Vec3(0, 64, 2), outside: new Vec3(0, 64, 3) }
const state = () => ({ home, formerHomes: [], builds: {}, memory: {}, facilities: {} })

test('ブロックの状態: 本物の耕地の moisture と小麦の age を読む（7 未満の耕地は乾いている）', () => {
  const bot = world()
  assert.equal(isDry(bot.blockAt(new Vec3(10, 63, 0))), false)
  assert.equal(isDry(bot.blockAt(new Vec3(11, 63, 0))), true)
  assert.equal(isDry(bot.blockAt(new Vec3(10, 64, 0))), false) // 耕地でない
  assert.equal(ageOf(bot.blockAt(new Vec3(11, 64, 0))), 2)
})

test('技の t.block_at と find_blocks は状態（props）を返す', async () => {
  const bot = world()
  assert.deepEqual(blockInfo(bot, { x: 11.4, y: 63, z: 0 }), { name: 'farmland', x: 11, y: 63, z: 0, props: { moisture: '3' } })
  assert.deepEqual(blockInfo(bot, { x: 0, y: 70, z: 0 }), { name: 'air', x: 0, y: 70, z: 0 })

  const callTool = createTools({ bot, state: state(), knowledge: new Knowledge(md), run: async () => ({ ok: true }), candidates: () => [], check: () => [] })
  const { result } = await callTool('find_blocks', { block: 'wheat' })
  assert.deepEqual(result.map((r) => [r.x, r.ripe, r.age, r.props]), [[10, true, 7, { age: '7' }], [11, false, 2, { age: '2' }], [12, false, 0, { age: '0' }]])
})

test('畑の状態に、乾いた耕地の上の作物の数を添える（知らせるだけ）', () => {
  const bot = world()
  const s = state()
  const f = cropFarms(bot, s, 'wheat')
  assert.equal(f.planted, 3)
  assert.equal(f.dry, 2)
  assert.deepEqual(f.farms.map((x) => [x.name, x.dry]), [['home_farm', 2]])
  assert.equal(farmSummary(bot, s).wheat.dry, 2)
})
