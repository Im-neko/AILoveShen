import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { sightings, digTargets } from '../src/world.mjs'

const { Vec3 } = vec3Pkg
const v = (x, y, z) => new Vec3(x, y, z)
const md = minecraftData('1.21.4')

// y 70 より下は石、上は空気。石炭鉱石は地表（70 - 1）と地中（65）
const ORES = [v(3, 69, 0), v(5, 65, 0)]
const bot = {
  registry: md,
  entity: { position: v(0, 70, 0) },
  entities: {},
  // 探す種類（matching）に合うものだけ返す
  findBlocks: ({ matching }) => ORES.filter(() => [].concat(matching).includes(md.blocksByName.coal_ore.id)),
  blockAt: (p) => {
    if (ORES.some((o) => o.equals(p))) return { name: 'coal_ore', boundingBox: 'block' }
    return p.y < 70 ? { name: 'stone', boundingBox: 'block' } : { name: 'air', boundingBox: 'empty' }
  }
}

test('空気に触れている鉱石だけを覚える（town2: 地中の石炭を探して探索に出ていった）', () => {
  assert.deepEqual(sightings(bot).map((s) => s.pos), [v(3, 69, 0)])
})

test('水の下の鉱石は候補に出さず、覚えもしない（town2 では掘りに行って溺れた）', () => {
  const wet = { ...bot, blockAt: (p) => (bot.blockAt(p).name === 'air' ? { name: 'water', boundingBox: 'empty' } : bot.blockAt(p)) }
  assert.deepEqual(sightings(wet), [])
  assert.deepEqual(digTargets(wet, { home: null, plan: null, unreachableBlocks: new Set() }, 'coal_ore'), [])
})

test('経路が届かなかったブロックは、もう候補に出さない', () => {
  const state = { home: null, plan: null, unreachableBlocks: new Set() }
  assert.deepEqual(digTargets(bot, state, 'coal_ore'), [v(3, 69, 0)])
  state.unreachableBlocks.add('3,69,0')
  assert.deepEqual(digTargets(bot, state, 'coal_ore'), [])
})

test('下の原木を切った後の浮いた幹も木と見なす（近くの木を木と認識しなかった）', () => {
  // 地面は y 69 まで。原木は y 73〜75（下の 70〜72 は切られて空気）、葉の上に乗った原木は y 80
  const logs = [v(2, 73, 0), v(2, 74, 0), v(2, 75, 0)]
  const onLeaves = v(6, 80, 0)
  const world = (inv) => ({
    registry: md,
    entity: { position: v(0, 70, 0) },
    entities: {},
    inventory: { items: () => inv },
    findBlocks: () => [...logs, onLeaves],
    blockAt: (p) => {
      if (logs.some((l) => l.equals(p)) || onLeaves.equals(p)) return { name: 'oak_log', boundingBox: 'block', position: p }
      if (p.x === 6 && p.y === 79) return { name: 'oak_leaves', boundingBox: 'block' }
      return p.y < 70 ? { name: 'grass_block', boundingBox: 'block' } : { name: 'air', boundingBox: 'empty' }
    }
  })
  const state = { home: null, plan: null, unreachableBlocks: new Set() }
  // 地面から 4 段上の原木には手が届く。それより上は土を積めば届く
  assert.deepEqual(digTargets(world([]), state, 'oak_log'), [v(2, 73, 0), v(2, 74, 0)])
  assert.deepEqual(digTargets(world([{ name: 'dirt', count: 8 }]), state, 'oak_log', 5), [v(2, 73, 0), v(2, 74, 0), v(2, 75, 0)])
})
